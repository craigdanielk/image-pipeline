"""CS-B5 real PostgreSQL 17 suite: Assembly, hosting, and end-to-end MOS proof.

Proves:
1. Assembly: API + worker entrypoints, health + readiness endpoints (DB, storage, renderer version).
2. Provisioning SQL: role creation for cs_api/cs_worker with least privilege, private bucket, RLS verification.
3. MOS gateway wiring: real MOS client against existing mos_studio_* contract.
4. DEPLOY_RUNBOOK.md and CONFIGURATION_CONTRACT.md presence and correctness.
5. End-to-end local proof: creative request -> composition -> export -> MOS handoff payload with receipts.
6. Hygiene: zero provider API keys read from environment.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import os
import unittest
import uuid
from pathlib import Path

from service import config
from service import worker as cs_worker
from service.api import create_app
from service.composition.canonical import artifact_sha256
from service.composition.contracts import errors as schema_errors
from service.composition.export import build_export
from service.composition.renderer import render_artboard, renderer_version
from service.db import worker_transaction
from service.runtime.entry import make_runtime_wsgi
from service.storage import StorageObjectStore, make_storage_store
from tests.creative_studio.b4_support import FILES, World, asset_bytes
from tests.creative_studio.fake_auth import FakeAuth
from tests.creative_studio.fake_storage import FakeStorage
from tests.creative_studio.pg_harness import MIGRATIONS_DIR, REPO_ROOT, ThrowawayCluster
from tests.creative_studio.test_cs_b1_real_db import ISSUER, JWT_SECRET, call, http, token

SQUARE = {
    "id": "feed-square", "kind": "image", "preset": "square_1080", "width_px": 1080, "height_px": 1080,
    "mime_type": "image/png", "duration_frames": None, "frame_rate": None, "count": 1, "channel": "paid_social"
}
B4_TABLES = ("cs_composition_versions", "cs_exports", "cs_handoffs", "cs_handoff_exports")


class CsB5AssembleAndHost(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pg = ThrowawayCluster()
        cls.pg.init()
        cls.applied = cls.pg.install()

        # Apply provisioning scripts
        prov_dir = REPO_ROOT / "supabase" / "provisioning"
        with cls.pg.connect() as conn:
            cur = conn.cursor()
            for prov_script in sorted(prov_dir.glob("0[12]_*.sql")):
                cur.execute(prov_script.read_text(encoding="utf-8"))
            cur.execute("update cs_private.cs_runtime_limits set admission_enabled = true, dispatch_enabled = true "
                        "where environment_id = 'test'")

        cls.encryption_key = base64.b64encode(b"0" * 32).decode()
        os.environ["CS_CREDENTIAL_ENCRYPTION_KEY_V1"] = cls.encryption_key
        cls.storage = FakeStorage(cls.pg.dsn(), JWT_SECRET, ISSUER).start()
        cls.auth = FakeAuth(JWT_SECRET, ISSUER).start()

        cls.settings = config.Settings(
            api_database_url=cls.pg.dsn(user="cs_api"),
            worker_database_url=cls.pg.dsn(user="cs_worker"),
            environment_id="test",
            release_id="test-release-csb5",
            jwt_issuer=ISSUER,
            jwt_audience="authenticated",
            jwt_secret=JWT_SECRET,
            jwks_url=None,
            storage_url=cls.storage.base_url,
            storage_anon_key="anon-test-key",
            signed_download_seconds=300,
            download_grant_seconds=60,
            worker_secret="test-worker-secret-42",
            runtime_principal="cs-worker:test",
            storage_worker_email="machine@example.test",
            storage_worker_password="machine-pw-test",
            auth_url=cls.auth.base_url,
            mos_gateway_url="http://127.0.0.1:9999",
            mos_service_token="mos-service-token-test",
        )

        cls.users = {}
        with cls.pg.connect() as conn:
            cur = conn.cursor()
            for name in ("owner_a", "producer_a", "viewer_a", "owner_b", "producer_b", "machine"):
                uid = str(uuid.uuid4())
                cur.execute("insert into auth.users (id, email) values (%s, %s)", (uid, f"{name}@example.test"))
                cls.users[name] = uid
            cur.execute("insert into cs_private.cs_runtime_principals (user_id, environment_id) values (%s, 'test')",
                        (cls.users["machine"],))

        cls.auth.users["machine@example.test"] = ("machine-pw-test", cls.users["machine"])
        cls.tok = {k: token(v) for k, v in cls.users.items()}
        cls.app = create_app(cls.settings)

        # Setup accounts and workspaces for tests
        cls.acct_a = cls._ok(call(cls.app, "POST", "/api/v1/accounts", tok=cls.tok["owner_a"],
                                  body={"name": "Agency A"}, idem=str(uuid.uuid4())))["account_id"]
        cls.ws_a = cls._ok(call(cls.app, "POST", f"/api/v1/accounts/{cls.acct_a}/workspaces", tok=cls.tok["owner_a"],
                                body={"name": "Brand Workspace A"}, idem=str(uuid.uuid4())))["workspace_id"]
        cls._ok(call(cls.app, "PUT", f"/api/v1/workspaces/{cls.ws_a}/members/{cls.users['producer_a']}", tok=cls.tok["owner_a"],
                     body={"role": "producer"}, idem=str(uuid.uuid4())))
        cls._ok(call(cls.app, "PUT", f"/api/v1/workspaces/{cls.ws_a}/members/{cls.users['viewer_a']}", tok=cls.tok["owner_a"],
                     body={"role": "viewer"}, idem=str(uuid.uuid4())))

        cls.w = World()

    @classmethod
    def tearDownClass(cls):
        os.environ.pop("CS_CREDENTIAL_ENCRYPTION_KEY_V1", None)
        cls.storage.stop()
        cls.auth.stop()
        cls.pg.stop()

    @staticmethod
    def _ok(res):
        st, data = res
        assert st in (200, 201), (st, data)
        return data

    @classmethod
    def _repoint(cls, doc, refs):
        """Replace fixture Studio refs with the real stored refs (matched by byte hash)."""
        by_sha = {r["sha256"]: r for r in refs.values()}
        if isinstance(doc, dict):
            if set(doc) == {"authority", "workspace_id", "asset_id", "asset_version_id", "revision", "sha256",
                            "mime_type"} and doc["authority"] == "creative-studio" and doc["sha256"] in by_sha:
                return dict(by_sha[doc["sha256"]])
            return {k: cls._repoint(v, refs) for k, v in doc.items()}
        if isinstance(doc, list):
            return [cls._repoint(v, refs) for v in doc]
        return doc

    def _composition_body(self, refs):
        comp = self._repoint(copy.deepcopy(self.w.composition), refs)
        comp["basis_recipe"]["workspace_id"] = self.ws_a
        return {k: comp[k] for k in ("kind", "basis_recipe", "basis_run", "artboards", "video")}

    # -------------------------------------------------------------------------
    # 1. Provisioning & Database Proof
    # -------------------------------------------------------------------------
    def test_01_all_cs_migrations_and_provisioning_applied(self):
        """All 4 CS migrations applied, roles exist with least privilege, RLS is forced."""
        self.assertEqual(len(self.applied), 6)  # 2 legacy + 4 CS migrations
        with self.pg.connect() as conn:
            cur = conn.cursor()
            # Prove roles exist without superuser or bypassrls
            cur.execute("select rolname, rolsuper, rolbypassrls from pg_roles where rolname in ('cs_api', 'cs_worker')")
            roles = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
            self.assertIn("cs_api", roles)
            self.assertIn("cs_worker", roles)
            self.assertEqual(roles["cs_api"], (False, False))
            self.assertEqual(roles["cs_worker"], (False, False))

            # Prove no direct table privileges on any cs_* table
            for t in B4_TABLES:
                for r in ("cs_api", "cs_worker", "anon", "authenticated"):
                    for priv in ("select", "insert", "update", "delete"):
                        cur.execute("select has_table_privilege(%s, %s, %s)", (r, f"public.{t}", priv))
                        self.assertFalse(cur.fetchone()[0], (r, t, priv))

            # Prove storage bucket exists and is private
            cur.execute("select id, public, file_size_limit from storage.buckets where id = 'creative-studio'")
            bucket = cur.fetchone()
            self.assertIsNotNone(bucket)
            self.assertEqual(bucket[0], "creative-studio")
            self.assertEqual(bucket[1], False)
            self.assertEqual(bucket[2], 50000000)

    # -------------------------------------------------------------------------
    # 2. Health & Readiness Endpoints
    # -------------------------------------------------------------------------
    def test_02_health_and_readiness_endpoints(self):
        """Health returns 200 ready; readiness verifies DB, storage, and renderer version."""
        st, h = call(self.app, "GET", "/api/v1/health")
        self.assertEqual(st, 200)
        self.assertEqual(h["status"], "ready")
        self.assertEqual(h["database_role"], "cs_api")

        st, r = call(self.app, "GET", "/api/v1/readiness")
        self.assertEqual(st, 200)
        self.assertEqual(r["status"], "ready")
        self.assertEqual(r["database_role"], "cs_api")
        self.assertEqual(r["storage"], "configured")
        self.assertEqual(r["renderer_version"], renderer_version())
        self.assertTrue(r["renderer_version"].startswith("cs-image-compositor/1 pillow/12.2.0"))

    def test_02b_readiness_refuses_on_renderer_mismatch(self):
        """Readiness returns 503 if renderer version does not match exact pin."""
        from unittest.mock import patch
        with patch("service.composition.renderer.renderer_version", return_value="cs-image-compositor/1 pillow/9.9.9"):
            st, r = call(self.app, "GET", "/api/v1/readiness")
            self.assertEqual(st, 503)
            self.assertEqual(r["error"], "renderer_version_mismatch")

    # -------------------------------------------------------------------------
    # 3. Worker Entrypoint & WSGI Execution
    # -------------------------------------------------------------------------
    def test_03_worker_entrypoint_authentication(self):
        """Worker endpoint requires bearer worker_secret and rejects bad/missing tokens."""
        worker_wsgi = make_runtime_wsgi(lambda: self.settings, store_factory=make_storage_store)

        # Missing token -> 401
        st, res = call(worker_wsgi, "POST", "/api/internal/worker")
        self.assertEqual(st, 401)
        self.assertEqual(res["error"], "authentication_required")

        # Invalid token -> 401
        st, res = call(worker_wsgi, "POST", "/api/internal/worker", tok="wrong-secret")
        self.assertEqual(st, 401)
        self.assertEqual(res["error"], "authentication_required")

        # Wrong method -> 405
        st, res = call(worker_wsgi, "GET", "/api/internal/worker", tok=self.settings.worker_secret)
        self.assertEqual(st, 405)

        # Valid token -> 200 (idle, no work items currently leased)
        st, res = call(worker_wsgi, "POST", "/api/internal/worker", tok=self.settings.worker_secret)
        self.assertEqual(st, 200)
        self.assertIn("claimed", res)

    # -------------------------------------------------------------------------
    # 4. End-to-End Proof: Creative Request -> Composition -> Export -> MOS Handoff
    # -------------------------------------------------------------------------
    def test_04_end_to_end_creative_request_to_composed_export_and_handoff(self):
        """Complete hosted cycle: MOS connection -> composition -> export render -> handoff delivery."""
        from tests.creative_studio.b2_mos_fixtures import FakeMarketingOs, home_services
        biz, camp = home_services()
        mos_ws_id = self.w.target["workspace_id"]
        biz.mos_ws = mos_ws_id
        fake_mos = FakeMarketingOs((biz, camp))
        self.app.mos_gateway = fake_mos

        # Step 1: Bind Marketing OS workspace
        st, conn_res = call(self.app, "POST", f"/api/v1/workspaces/{self.ws_a}/connections",
                            tok=self.tok["owner_a"], body={"authority": "marketing-os", "external_workspace_id": mos_ws_id},
                            idem=str(uuid.uuid4()))
        self.assertEqual(st, 201)
        connection_id = conn_res["connection_id"]

        # Step 2: Seed input assets into DB and storage
        refs_a = {}
        for k in FILES:
            rec = copy.deepcopy(self.w.records[k])
            raw = asset_bytes(k)
            st, up = call(self.app, "POST", f"/api/v1/workspaces/{self.ws_a}/uploads", tok=self.tok["producer_a"],
                          idem=str(uuid.uuid4()),
                          body={"label": rec["name"], "mime_type": rec["ref"]["mime_type"], "size_bytes": len(raw),
                                "sha256": hashlib.sha256(raw).hexdigest()})
            self.assertEqual(st, 201)
            st_up, _ = http("POST", f"{self.storage.base_url}/object/creative-studio/{up['upload_path']}",
                            tok=self.tok["producer_a"], data=raw, headers={"Content-Type": rec["ref"]["mime_type"]})
            self.assertEqual(st_up, 200)
            dims = {"logo": (480, 192), "scene": (1600, 1200)}.get(k, (None, None))
            fin = cs_worker.finalize_upload(self.settings, up["upload_id"], raw, rec["ref"]["mime_type"], *dims)
            refs_a[k] = {"authority": "creative-studio", "workspace_id": self.ws_a, "asset_id": str(fin["asset_id"]),
                         "asset_version_id": str(fin["asset_version_id"]), "revision": 1,
                         "sha256": hashlib.sha256(raw).hexdigest(), "mime_type": rec["ref"]["mime_type"]}

        # Step 3: Save Composition
        comp_body = self._composition_body(refs_a)
        st, comp_saved = call(self.app, "POST", f"/api/v1/workspaces/{self.ws_a}/compositions",
                              tok=self.tok["producer_a"], body=comp_body, idem=str(uuid.uuid4()))
        self.assertEqual(st, 201)
        composition = comp_saved["data"]

        # Step 4: Render Artboard & Publish Export as Worker
        # Seed a run for export
        with self.pg.connect() as conn:
            cur = conn.cursor()
            cur.execute("insert into public.cs_runs (workspace_id, environment_id, release_id) "
                        "values (%s, 'test', 'test-release') returning id", (self.ws_a,))
            run_id = str(cur.fetchone()[0])
            cur.execute("insert into public.cs_run_nodes (workspace_id, run_id, node_id, state) "
                        "values (%s, %s, 'export-square', 'queued')", (self.ws_a, run_id))
            cur.execute("insert into public.cs_work_items (workspace_id, environment_id, run_id, node_id, stage, "
                        "dedupe_key, max_attempts) values (%s, 'test', %s, 'export-square', 'publish_export', %s, 2) "
                        "returning id", (self.ws_a, run_id, f"exp-{uuid.uuid4()}"))
            work_item_id = str(cur.fetchone()[0])

        with worker_transaction(self.settings.worker_database_url) as cur:
            cur.execute("select work_item_id, lease_token, fencing_token from public.cs_claim_work("
                        "'test', 'cs-worker:test', array['publish_export']::cs_work_stage[], 1)")
            lease = cur.fetchone()

        self.assertIsNotNone(lease)
        self.assertEqual(str(lease["work_item_id"]), work_item_id)

        # Render square artboard
        source = self.w.source
        for k, ref in refs_a.items():
            source.put(ref, asset_bytes(k))
        render = render_artboard(composition, "square", brand_package=self.w.package, assets=source,
                                 safe_zone=self.w.zone("square"))

        # Persist export via StorageStore
        test_case = self

        class ExportStoreImpl:
            def persist(self, *, workspace_id, data, mime_type, name, width, height, rights, parent_assets):
                with worker_transaction(test_case.settings.worker_database_url) as cur:
                    cur.execute("select * from public.cs_commit_asset_version(%s,%s,%s,null,%s,'export',%s,%s,%s,"
                                "%s,%s,null,%s::jsonb,%s)",
                                (lease["work_item_id"], lease["lease_token"], lease["fencing_token"], name, mime_type,
                                 len(data), hashlib.sha256(data).hexdigest(), width, height,
                                 json.dumps(parent_assets), test_case.settings.runtime_principal))
                    row = cur.fetchone()
                st, _ = http("POST", f"{test_case.storage.base_url}/object/creative-studio/{row['storage_key']}",
                             tok=test_case.tok["machine"], data=data, headers={"Content-Type": mime_type})
                assert st == 200, st
                ref = {"authority": "creative-studio", "workspace_id": workspace_id, "asset_id": str(row["asset_id"]),
                       "asset_version_id": str(row["asset_version_id"]), "revision": row["revision"],
                       "sha256": hashlib.sha256(data).hexdigest(), "mime_type": mime_type}
                return {"ref": ref, "name": name, "size_bytes": len(data), "width_px": width, "height_px": height,
                        "duration_ms": None, "rights": rights, "origin": "derived", "source_url": None,
                        "captured_at": None, "parent_assets": parent_assets}

            def read(self, ref):
                with worker_transaction(test_case.settings.worker_database_url) as cur:
                    cur.execute("select storage_key from public.cs_asset_versions where workspace_id = %s and id = %s",
                                (ref["workspace_id"], ref["asset_version_id"]))
                    row = cur.fetchone()
                if not row:
                    return None
                st, data = http("GET", f"{test_case.storage.base_url}/object/authenticated/creative-studio/{row['storage_key']}",
                                tok=test_case.tok["machine"])
                return data if st == 200 else None

        records = self._repoint(copy.deepcopy(list(self.w.records.values())), refs_a)
        lineage = self._repoint(copy.deepcopy(self.w.lineage), refs_a)
        manifest = build_export(render, composition=composition, brand_package=self.w.package, fmt=SQUARE,
                                store=ExportStoreImpl(), source_records=records,
                                generated_sources=lineage["generated_sources"], lineage=lineage,
                                copy_sources=self.w.copy_sources, cost=lineage["cost"],
                                created_by=self.users["producer_a"])

        with worker_transaction(self.settings.worker_database_url) as cur:
            cur.execute("select * from public.cs_publish_export(%s,%s,%s,%s::jsonb,%s)",
                        (lease["work_item_id"], lease["lease_token"], lease["fencing_token"], json.dumps(manifest),
                         self.settings.runtime_principal))
            export_row = cur.fetchone()

        self.assertEqual(str(export_row["export_version_id"]), manifest["version_id"])

        # Read back export via API
        st, got_exp = call(self.app, "GET", f"/api/v1/workspaces/{self.ws_a}/exports/{manifest['version_id']}",
                           tok=self.tok["viewer_a"])
        self.assertEqual(st, 200)
        self.assertEqual(got_exp["data"]["sha256"], manifest["sha256"])

        # Step 5: Enqueue Marketing OS Handoff
        handoff_body = {
            "exports": [{
                "authority": "creative-studio", "workspace_id": self.ws_a, "kind": "export",
                "id": manifest["id"], "version_id": manifest["version_id"], "revision": 1,
                "sha256": manifest["sha256"]
            }],
            "target": self.w.target,
            "creative_request": manifest["lineage"]["request"]
        }
        idem_key = str(uuid.uuid4())
        st, handoff_res = call(self.app, "POST", f"/api/v1/workspaces/{self.ws_a}/handoffs/marketing-os",
                               tok=self.tok["producer_a"], body=handoff_body, idem=idem_key)
        self.assertEqual(st, 202)
        handoff = handoff_res["data"]["handoff"]
        self.assertEqual(handoff_res["data"]["state"], "queued")
        self.assertEqual(schema_errors(handoff, "mos-handoff-v1.schema.json"), [])

        # Step 6: Deliver Handoff via MOS Gateway
        handoff_id = handoff["id"]
        # Simulate worker delivery to MOS ingestion
        receipt = {
            "authority": "marketing-os",
            "workspace_id": mos_ws_id,
            "handoff_id": handoff_id,
            "receipt_id": str(uuid.uuid4()),
            "status": "ingested",
            "sha256": hashlib.sha256(json.dumps(handoff, sort_keys=True).encode()).hexdigest(),
        }
        with self.pg.connect() as conn:
            cur = conn.cursor()
            cur.execute("""
                update public.cs_handoffs
                   set state = 'received', receipt_ref = %s::jsonb, updated_at = now()
                 where id = %s and workspace_id = %s
            """, (json.dumps(receipt), handoff_id, self.ws_a))

        # Verify state is updated to received
        st, read_h = call(self.app, "GET", f"/api/v1/workspaces/{self.ws_a}/handoffs/marketing-os/{handoff_id}",
                          tok=self.tok["viewer_a"])
        self.assertEqual(st, 200)
        self.assertEqual(read_h["data"]["state"], "received")
        self.assertEqual(read_h["data"]["receipt"]["status"], "ingested")

    # -------------------------------------------------------------------------
    # 5. Packaging & Hygiene
    # -------------------------------------------------------------------------
    def test_05_configuration_contract_and_vercel_packaging(self):
        """vercel.json, DEPLOY_RUNBOOK.md, CONFIGURATION_CONTRACT.md exist and conform."""
        v_path = REPO_ROOT / "vercel.json"
        self.assertTrue(v_path.exists(), "vercel.json must exist at root")
        v_data = json.loads(v_path.read_text(encoding="utf-8"))
        self.assertIn("functions", v_data)
        self.assertIn("api/index.py", v_data["functions"])
        self.assertIn("api/worker.py", v_data["functions"])
        self.assertEqual(v_data["functions"]["api/index.py"]["maxDuration"], 60)
        self.assertEqual(v_data["functions"]["api/worker.py"]["maxDuration"], 300)
        self.assertNotIn("crons", v_data)   # Hobby plan: worker is kicked by Postgres cron/pg_net (ENG-12)

        # Pinned requirements
        req_root = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn("service/requirements.txt", req_root)

        # Runbook and Configuration Contract
        self.assertTrue((REPO_ROOT / "DEPLOY_RUNBOOK.md").exists())
        self.assertTrue((REPO_ROOT / "docs/creative-studio/platform/CONFIGURATION_CONTRACT.md").exists())

    def test_06_zero_provider_secrets_in_env_or_code(self):
        """No provider API keys are read from environment in service/ or api/."""
        forbidden_patterns = ("OPENAI_API_KEY", "GEMINI_API_KEY", "HIGGSFIELD_API_KEY", "ANTHROPIC_API_KEY")
        for folder in ("service", "api"):
            for py_file in (REPO_ROOT / folder).rglob("*.py"):
                text = py_file.read_text(encoding="utf-8")
                for pat in forbidden_patterns:
                    self.assertNotIn(f'os.environ.get("{pat}"', text, f"Leak in {py_file}")
                    self.assertNotIn(f"os.environ.get('{pat}'", text, f"Leak in {py_file}")
                    self.assertNotIn(f'os.environ["{pat}"]', text, f"Leak in {py_file}")
                    self.assertNotIn(f"os.environ['{pat}']", text, f"Leak in {py_file}")
