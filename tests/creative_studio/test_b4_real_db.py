"""CS-B4 real PostgreSQL 17 suite: compositions, exports, MOS handoffs.

Applies ALL supabase/migrations in filename order (B1 foundation + this
task's 20261031140400) on a throwaway PG17 cluster via B1's harness, runs the
real API code over WSGI and a Storage stand-in decided by the migration's own
storage RLS. ENG-03 §5: anon denied; authenticated direct write denied;
workspace B cannot read/act on A; spoofed actor ignored; idempotent re-run.

Run:  python3 -m pytest tests/creative_studio/test_b4_real_db.py -v
"""
from __future__ import annotations

import copy
import hashlib
import json
import unittest
import uuid
from pathlib import Path

import psycopg2

from service import worker as cs_worker
from service.api import create_app
from service.composition.canonical import artifact_sha256
from service.composition.contracts import errors as schema_errors
from service.composition.export import build_export
from service.composition.renderer import render_artboard
from service.db import worker_transaction
from tests.creative_studio.b4_support import FILES, World, asset_bytes
from tests.creative_studio.fake_storage import FakeStorage
from tests.creative_studio.pg_harness import MIGRATIONS_DIR, ThrowawayCluster, claims_json
from tests.creative_studio import test_cs_b1_real_db as b1  # module import: B1's class is not re-collected
from tests.creative_studio.test_cs_b1_real_db import ISSUER, JWT_SECRET, call, http, token

CS_B4_MIGRATION = MIGRATIONS_DIR / "20261031140400_cs_b4_composition_exports.sql"
SQUARE = {"id": "feed-square", "kind": "image", "preset": "square_1080", "width_px": 1080, "height_px": 1080,
          "mime_type": "image/png", "duration_frames": None, "frame_rate": None, "count": 1, "channel": "paid_social"}
B4_TABLES = ("cs_composition_versions", "cs_exports", "cs_handoffs", "cs_handoff_exports")


class CsB4RealDatabase(unittest.TestCase):
    _settings = classmethod(b1.CsB1RealDatabase._settings.__func__)
    _bootstrap = classmethod(b1.CsB1RealDatabase._bootstrap.__func__)
    _seed_run = classmethod(b1.CsB1RealDatabase._seed_run.__func__)
    sql_as = b1.CsB1RealDatabase.sql_as
    assertSqlState = b1.CsB1RealDatabase.assertSqlState

    @classmethod
    def setUpClass(cls):
        cls.pg = ThrowawayCluster()
        cls.pg.init()
        cls.applied = cls.pg.install()
        cls.storage = FakeStorage(cls.pg.dsn(), JWT_SECRET, ISSUER).start()
        cls.settings = cls._settings()
        cls.app = create_app(cls.settings)
        cls.users = {}
        with cls.pg.connect() as conn:
            cur = conn.cursor()
            for name in ("owner_a", "owner_b", "producer_a", "viewer_a", "outsider", "machine"):
                uid = str(uuid.uuid4())
                cur.execute("insert into auth.users (id, email) values (%s, %s)", (uid, f"{name}@example-studio.test"))
                cls.users[name] = uid
            cur.execute("insert into cs_private.cs_runtime_principals (user_id, environment_id) values (%s, 'test')",
                        (cls.users["machine"],))
        cls.tok = {k: token(v) for k, v in cls.users.items()}
        cls.acct_a, cls.ws_a = cls._bootstrap("owner_a", "Example Home Services Co")
        cls.acct_b, cls.ws_b = cls._bootstrap("owner_b", "Example Bakery Ltd")
        for who, role in (("producer_a", "producer"), ("viewer_a", "viewer")):
            st, _ = call(cls.app, "PUT", f"/api/v1/workspaces/{cls.ws_a}/members/{cls.users[who]}",
                         tok=cls.tok["owner_a"], body={"role": role}, idem=str(uuid.uuid4()))
            assert st == 200, st
        cls.w = World()
        cls.refs_a = {k: cls._upload("producer_a", cls.ws_a, k) for k in FILES}
        cls.refs_b = {k: cls._upload("owner_b", cls.ws_b, k) for k in ("scene",)}
        cls.body = cls._composition_body(cls.refs_a)

    @classmethod
    def tearDownClass(cls):
        cls.storage.stop()
        cls.pg.stop()

    # -- helpers ---------------------------------------------------------------
    @classmethod
    def _upload(cls, who, ws, key):
        data = asset_bytes(key)
        mime = {"logo": "image/png", "scene": "image/png", "font": "font/ttf"}[key]
        sha = hashlib.sha256(data).hexdigest()
        st, up = call(cls.app, "POST", f"/api/v1/workspaces/{ws}/uploads", tok=cls.tok[who], idem=str(uuid.uuid4()),
                      body={"label": FILES[key], "mime_type": mime, "size_bytes": len(data), "sha256": sha})
        assert st == 201, (st, up)
        st, _ = http("POST", f"{cls.storage.base_url}/object/creative-studio/{up['upload_path']}", tok=cls.tok[who],
                     data=data, headers={"Content-Type": mime})
        assert st == 200, st
        dims = {"logo": (480, 192), "scene": (1600, 1200)}.get(key, (None, None))
        fin = cs_worker.finalize_upload(cls.settings, up["upload_id"], data, mime, *dims)
        return {"authority": "creative-studio", "workspace_id": ws, "asset_id": str(fin["asset_id"]),
                "asset_version_id": str(fin["asset_version_id"]), "revision": 1, "sha256": sha, "mime_type": mime}

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

    @classmethod
    def _composition_body(cls, refs):
        comp = cls._repoint(copy.deepcopy(cls.w.composition), refs)
        comp["basis_recipe"]["workspace_id"] = cls.ws_a
        return {k: comp[k] for k in ("kind", "basis_recipe", "basis_run", "artboards", "video")}

    def _create(self, who="producer_a", body=None, idem=None, ws=None):
        return call(self.app, "POST", f"/api/v1/workspaces/{ws or self.ws_a}/compositions", tok=self.tok[who],
                    body=body or self.body, idem=idem or str(uuid.uuid4()))

    def _export_flow(self, comp):
        """compose_image + publish_export stage as the worker would run it (lease, store, read back, publish)."""
        run = self._seed_run(self.ws_a, f"exp-{uuid.uuid4()}", stage="publish_export", node="export-square")
        with worker_transaction(self.settings.worker_database_url) as cur:
            cur.execute("select work_item_id, lease_token, fencing_token from public.cs_claim_work("
                        "'test', 'w-b4', array['publish_export']::cs_work_stage[], 1)")
            lease = cur.fetchone()
        source = World().source
        for k, ref in self.refs_a.items():
            source.put(ref, asset_bytes(k))
        render = render_artboard(comp, "square", brand_package=self.w.package, assets=source,
                                 safe_zone=self.w.zone("square"))
        test = self

        class StorageStore:
            key = None

            def persist(self, *, workspace_id, data, mime_type, name, width, height, rights, parent_assets):
                with worker_transaction(test.settings.worker_database_url) as cur:
                    cur.execute("select * from public.cs_commit_asset_version(%s,%s,%s,null,%s,'export',%s,%s,%s,"
                                "%s,%s,null,%s::jsonb,%s)",
                                (lease["work_item_id"], lease["lease_token"], lease["fencing_token"], name, mime_type,
                                 len(data), hashlib.sha256(data).hexdigest(), width, height,
                                 json.dumps(parent_assets), test.settings.runtime_principal))
                    row = cur.fetchone()
                StorageStore.key = row["storage_key"]
                st, _ = http("POST", f"{test.storage.base_url}/object/creative-studio/{row['storage_key']}",
                             tok=test.tok["machine"], data=data, headers={"Content-Type": mime_type})
                assert st == 200, st
                ref = {"authority": "creative-studio", "workspace_id": workspace_id, "asset_id": str(row["asset_id"]),
                       "asset_version_id": str(row["asset_version_id"]), "revision": row["revision"],
                       "sha256": hashlib.sha256(data).hexdigest(), "mime_type": mime_type}
                return {"ref": ref, "name": name, "size_bytes": len(data), "width_px": width, "height_px": height,
                        "duration_ms": None, "rights": rights, "origin": "derived", "source_url": None,
                        "captured_at": None, "parent_assets": parent_assets}

            def read(self, ref):
                st, data = http("GET", f"{test.storage.base_url}/object/authenticated/creative-studio/"
                                f"{StorageStore.key}", tok=test.tok["machine"])
                return data if st == 200 else None

        records = self._repoint(copy.deepcopy(list(self.w.records.values())), self.refs_a)
        lineage = self._repoint(copy.deepcopy(self.w.lineage), self.refs_a)
        manifest = build_export(render, composition=comp, brand_package=self.w.package, fmt=SQUARE,
                                store=StorageStore(), source_records=records,
                                generated_sources=lineage["generated_sources"], lineage=lineage,
                                copy_sources=self.w.copy_sources, cost=lineage["cost"],
                                created_by=self.users["producer_a"])
        return lease, manifest, run

    def _publish(self, lease, manifest):
        with worker_transaction(self.settings.worker_database_url) as cur:
            cur.execute("select * from public.cs_publish_export(%s,%s,%s,%s::jsonb,%s)",
                        (lease["work_item_id"], lease["lease_token"], lease["fencing_token"], json.dumps(manifest),
                         self.settings.runtime_principal))
            return cur.fetchone()

    # -- 01 install ------------------------------------------------------------
    def test_01_all_migrations_applied_in_order_with_forced_rls(self):
        self.assertEqual(self.applied, sorted(p.name for p in MIGRATIONS_DIR.glob("*.sql")))
        self.assertEqual(self.applied[-1], CS_B4_MIGRATION.name)
        with self.pg.connect() as conn:
            cur = conn.cursor()
            cur.execute("select relname, relrowsecurity, relforcerowsecurity from pg_class where relname = any(%s) "
                        "and relnamespace = 'public'::regnamespace order by 1", (list(B4_TABLES),))
            self.assertEqual(cur.fetchall(), sorted((t, True, True) for t in B4_TABLES))
            for role in ("anon", "authenticated", "public"):
                for fn in ("public.cs_save_composition_version(uuid,jsonb,text,jsonb,text,uuid,text)",
                           "public.cs_publish_export(uuid,uuid,bigint,jsonb,text)",
                           "public.cs_enqueue_handoff(uuid,jsonb,uuid,text)"):
                    cur.execute("select has_function_privilege(%s, %s, 'execute')", (role, fn))
                    self.assertFalse(cur.fetchone()[0], (role, fn))
            cur.execute("select has_function_privilege('cs_api', 'public.cs_publish_export(uuid,uuid,bigint,jsonb,"
                        "text)', 'execute'), has_function_privilege('cs_worker', 'public.cs_save_composition_version("
                        "uuid,jsonb,text,jsonb,text,uuid,text)', 'execute')")
            self.assertEqual(cur.fetchone(), (False, False))
            # procedure-only access: no policies, no table privileges for any runtime/client role
            cur.execute("select count(*) from pg_policies where tablename = any(%s)", (list(B4_TABLES),))
            self.assertEqual(cur.fetchone()[0], 0)
            for role in ("anon", "authenticated", "cs_api", "cs_worker"):
                for t in B4_TABLES:
                    for priv in ("select", "insert", "update", "delete"):
                        cur.execute("select has_table_privilege(%s, %s, %s)", (role, f"public.{t}", priv))
                        self.assertFalse(cur.fetchone()[0], (role, t, priv))
            # definer hygiene: pinned search_path on every B4 definer
            cur.execute("select p.proname, p.prosecdef, p.proconfig from pg_proc p join pg_namespace n on "
                        "n.oid = p.pronamespace where (n.nspname='public' and p.proname in ('cs_save_composition_version',"
                        "'cs_publish_export','cs_enqueue_handoff','cs_get_composition_version','cs_get_export',"
                        "'cs_get_handoff')) or (n.nspname='cs_private' and p.proname in ('cs_b4_require',"
                        "'cs_b4_assert_asset_refs')) order by 1")
            rows = cur.fetchall()
            self.assertEqual(len(rows), 8)
            for name, secdef, config in rows:
                self.assertTrue(secdef, name)
                self.assertIn("search_path=public, pg_temp", config, name)

    # -- 02 anon / authenticated denied -------------------------------------------
    def test_02_anon_and_authenticated_denied(self):
        for role in ("anon", "authenticated"):
            conn, cur = self.sql_as(role, self.users["producer_a"])
            try:
                for t in B4_TABLES:
                    self.assertSqlState("42501", lambda: cur.execute(f"select * from public.{t}"))
                    conn.rollback()
                self.assertSqlState("42501", lambda: cur.execute(
                    "insert into public.cs_handoffs (id) values (gen_random_uuid())"))
                conn.rollback()
                self.assertSqlState("42501", lambda: cur.execute(
                    "select public.cs_save_composition_version(%s, '{}'::jsonb, null, '[]'::jsonb, null, %s, %s)",
                    (self.ws_a, str(uuid.uuid4()), "0" * 64)))
                conn.rollback()
            finally:
                conn.close()

    def test_03_runtime_roles_cannot_touch_tables_directly(self):
        for role in ("cs_api", "cs_worker"):
            conn, cur = self.sql_as(role, self.users["producer_a"] if role == "cs_api" else None)
            try:
                for sql in ("select * from public.cs_composition_versions",
                            "insert into public.cs_exports (workspace_id) values ('%s')" % self.ws_a,
                            "update public.cs_handoffs set state = 'received'",
                            "delete from public.cs_handoff_exports"):
                    self.assertSqlState("42501", lambda: cur.execute(sql))
                    conn.rollback()
                if role == "cs_api":
                    self.assertSqlState("42501", lambda: cur.execute(
                        "select public.cs_publish_export(gen_random_uuid(), gen_random_uuid(), 1, '{}'::jsonb, 'x')"))
                    conn.rollback()
            finally:
                conn.close()

    # -- 04 save / replay / conflict ------------------------------------------------
    def test_04_create_composition_validates_and_is_idempotent(self):
        key = str(uuid.uuid4())
        st, out = self._create(idem=key)
        self.assertEqual(st, 201, out)
        doc = out["data"]
        self.assertEqual(schema_errors(doc, "composition-v1.schema.json"), [])
        self.assertEqual(doc["sha256"], artifact_sha256(doc))
        self.assertEqual(doc["created_by"], self.users["producer_a"])
        st, again = self._create(idem=key)
        self.assertEqual((st, again["data"]["version_id"]), (200, doc["version_id"]))
        changed = copy.deepcopy(self.body)
        changed["artboards"] = changed["artboards"][:1]
        st, conflict = self._create(idem=key, body=changed)
        self.assertEqual((st, conflict["error"]), (409, "idempotency_conflict"))
        st, got = call(self.app, "GET", f"/api/v1/workspaces/{self.ws_a}/compositions/{doc['id']}/versions/"
                       f"{doc['version_id']}", tok=self.tok["viewer_a"])
        self.assertEqual((st, got["data"]), (200, doc))

    def test_05_roles_and_workspace_isolation(self):
        st, out = self._create("viewer_a")
        self.assertEqual((st, out["error"]), (403, "capability_denied"))
        st, out = self._create("outsider")
        self.assertEqual((st, out["error"]), (404, "resource_unavailable"))
        st, out = self._create("owner_b")   # owner of B into A
        self.assertEqual((st, out["error"]), (404, "resource_unavailable"))
        st, doc = self._create()
        doc = doc["data"]
        for who, ws in (("owner_b", self.ws_a), ("owner_b", self.ws_b), ("outsider", self.ws_a)):
            st, out = call(self.app, "GET", f"/api/v1/workspaces/{ws}/compositions/{doc['id']}/versions/"
                           f"{doc['version_id']}", tok=self.tok[who])
            self.assertEqual(st, 404, (who, ws, out))
        conn, cur = self.sql_as("cs_api", self.users["owner_b"])
        try:
            self.assertSqlState("CS404", lambda: cur.execute(
                "select public.cs_get_composition_version(%s, %s, null)", (self.ws_a, doc["id"])))
            conn.rollback()
            # B's own workspace id does not reach A's object either
            self.assertSqlState("CS404", lambda: cur.execute(
                "select public.cs_get_composition_version(%s, %s, %s)", (self.ws_b, doc["id"], doc["version_id"])))
        finally:
            conn.close()
        st, out = call(self.app, "POST", f"/api/v1/workspaces/{self.ws_a}/compositions", body=self.body,
                       idem=str(uuid.uuid4()))
        self.assertEqual((st, out["error"]), (401, "authentication_required"))

    def test_06_cross_workspace_asset_refused(self):
        body = copy.deepcopy(self.body)
        body["artboards"][0]["layers"][0]["content"]["asset"] = self.refs_b["scene"]
        st, out = self._create(body=body)
        self.assertEqual((st, out["error"]), (403, "scope_mismatch"))
        # SQL path: B's asset relabelled as A's is unresolvable under A's scope
        forged = dict(self.refs_b["scene"], workspace_id=self.ws_a)
        doc = self._direct_payload(self.users["producer_a"])
        conn, cur = self.sql_as("cs_api", self.users["producer_a"])
        try:
            self.assertSqlState("CS422", lambda: cur.execute(
                "select * from public.cs_save_composition_version(%s,%s::jsonb,%s,%s::jsonb,null,%s,%s)",
                (self.ws_a, json.dumps(doc), "a" * 64, json.dumps([forged]), str(uuid.uuid4()), "b" * 64)))
        finally:
            conn.close()

    def _direct_payload(self, created_by):
        doc = {"schema_version": "cs-composition-v1", "id": str(uuid.uuid4()), "version_id": str(uuid.uuid4()),
               "revision": 1, "workspace_id": self.ws_a, "created_at": "2026-10-31T10:00:00Z",
               "created_by": created_by, "sha256": "", **copy.deepcopy(self.body)}
        doc["sha256"] = artifact_sha256(doc)
        return doc

    def test_07_spoofed_actor_ignored(self):
        body = dict(self.body, created_by=self.users["owner_b"])
        st, out = self._create(body=body)
        self.assertEqual((st, out["error"]), (422, "unsupported_field"))
        doc = self._direct_payload(self.users["owner_a"])  # claims say producer_a
        conn, cur = self.sql_as("cs_api", self.users["producer_a"])
        try:
            self.assertSqlState("CS422", lambda: cur.execute(
                "select * from public.cs_save_composition_version(%s,%s::jsonb,%s,'[]'::jsonb,null,%s,%s)",
                (self.ws_a, json.dumps(doc), "a" * 64, str(uuid.uuid4()), "b" * 64)))
        finally:
            conn.close()
        # no claims at all
        conn, cur = self.sql_as("cs_api")
        try:
            self.assertSqlState("CS401", lambda: cur.execute(
                "select * from public.cs_save_composition_version(%s,%s::jsonb,%s,'[]'::jsonb,null,%s,%s)",
                (self.ws_a, json.dumps(doc), "a" * 64, str(uuid.uuid4()), "b" * 64)))
        finally:
            conn.close()

    def test_08_revisions_use_if_match(self):
        st, first = self._create()
        first = first["data"]
        path = f"/api/v1/workspaces/{self.ws_a}/compositions/{first['id']}/revisions"
        edited = copy.deepcopy(self.body)
        edited["artboards"][0]["layers"][3]["content"]["text"] = "Same-day home repairs"

        def revise(if_match, body=edited):
            environ_extra = {"HTTP_IF_MATCH": f'"{if_match}"'} if if_match else {}
            return _call_with(self.app, "POST", path, self.tok["producer_a"], body, str(uuid.uuid4()), environ_extra)
        st, out = revise(None)
        self.assertEqual((st, out["error"]), (428, "if_match_required"))
        st, second = revise(first["sha256"])
        self.assertEqual(st, 201, second)
        self.assertEqual((second["data"]["revision"], second["data"]["id"]), (2, first["id"]))
        st, stale = revise(first["sha256"])
        self.assertEqual((st, stale["error"]), (409, "revision_conflict"))
        with self.pg.connect() as conn:
            cur = conn.cursor()
            self.assertSqlState("CS409", lambda: cur.execute(
                "update public.cs_composition_versions set kind = 'video' where object_id = %s", (first["id"],)))
            self.assertSqlState("CS409", lambda: cur.execute(
                "delete from public.cs_composition_versions where object_id = %s", (first["id"],)))

    def test_09_validate_route_reports_typed_issues_without_saving(self):
        bad = copy.deepcopy(self.body)
        bad["artboards"][0]["layers"][4]["content"]["child_ids"].append("missing-layer")
        st, out = call(self.app, "POST", f"/api/v1/workspaces/{self.ws_a}/compositions/validate",
                       tok=self.tok["viewer_a"], body=bad)
        self.assertEqual(st, 200)
        self.assertEqual(out["data"]["valid"], False)
        self.assertEqual(out["data"]["issues"][0]["code"], "invalid_composition")

    # -- 10 export publish + handoff -------------------------------------------------
    def test_10_export_publish_and_handoff(self):
        st, comp = self._create()
        comp = comp["data"]
        lease, manifest, _ = self._export_flow(comp)
        self.assertEqual(schema_errors(manifest, "export-v1.schema.json"), [])
        row = self._publish(lease, manifest)
        self.assertEqual((str(row["export_version_id"]), row["replayed"]), (manifest["version_id"], False))
        self.assertTrue(self._publish(lease, manifest)["replayed"])
        tampered = dict(manifest, sha256="f" * 64)
        self.assertRaisesRegex(Exception, "export_version_conflict", self._publish, lease, tampered)
        wrong_file = copy.deepcopy(manifest)
        wrong_file["version_id"] = str(uuid.uuid4())
        wrong_file["file"]["size_bytes"] += 1
        self.assertRaisesRegex(Exception, "asset_hash_mismatch", self._publish, lease, wrong_file)
        stale = dict(lease, fencing_token=lease["fencing_token"] + 1)
        self.assertRaisesRegex(Exception, "lease_lost", self._publish, stale, manifest)

        st, got = call(self.app, "GET", f"/api/v1/workspaces/{self.ws_a}/exports/{manifest['version_id']}",
                       tok=self.tok["viewer_a"])
        self.assertEqual((st, got["data"]), (200, manifest))
        st, out = call(self.app, "GET", f"/api/v1/workspaces/{self.ws_a}/exports/{manifest['version_id']}",
                       tok=self.tok["owner_b"])
        self.assertEqual(st, 404)

        body = {"exports": [{"authority": "creative-studio", "workspace_id": self.ws_a, "kind": "export",
                             "id": manifest["id"], "version_id": manifest["version_id"], "revision": 1,
                             "sha256": manifest["sha256"]}],
                "target": self.w.target, "creative_request": manifest["lineage"]["request"]}
        key = str(uuid.uuid4())
        path = f"/api/v1/workspaces/{self.ws_a}/handoffs/marketing-os"
        st, out = call(self.app, "POST", path, tok=self.tok["viewer_a"], body=body, idem=key)
        self.assertEqual((st, out["error"]), (403, "capability_denied"))
        st, out = call(self.app, "POST", f"/api/v1/workspaces/{self.ws_b}/handoffs/marketing-os",
                       tok=self.tok["owner_b"], body=body, idem=key)
        self.assertEqual((st, out["error"]), (403, "scope_mismatch"))
        st, out = call(self.app, "POST", path, tok=self.tok["owner_b"], body=body, idem=key)
        self.assertEqual(st, 404, out)
        st, out = call(self.app, "POST", path, tok=self.tok["producer_a"], body=body, idem=key)
        self.assertEqual(st, 202, out)
        handoff = out["data"]["handoff"]
        self.assertEqual(out["data"]["state"], "queued")
        self.assertEqual(schema_errors(handoff, "mos-handoff-v1.schema.json"), [])
        self.assertEqual(handoff["exports"][0]["sha256"], manifest["sha256"])
        self.assertEqual(handoff["cost"]["state"], "unknown")
        st, again = call(self.app, "POST", path, tok=self.tok["producer_a"], body=body, idem=key)
        self.assertEqual((st, again["data"]["handoff"]["id"]), (200, handoff["id"]))
        st, read = call(self.app, "GET", f"{path}/{handoff['id']}", tok=self.tok["viewer_a"])
        self.assertEqual((st, read["data"]["state"], read["data"]["receipt"]), (200, "queued", None))
        st, read = call(self.app, "GET", f"{path}/{handoff['id']}", tok=self.tok["owner_b"])
        self.assertEqual(st, 404)
        unbound = dict(body, target=dict(self.w.target, copy_versions=self.w.target["copy_versions"][:1]))
        st, out = call(self.app, "POST", path, tok=self.tok["producer_a"], body=unbound, idem=str(uuid.uuid4()))
        self.assertEqual((st, out["error"]), (422, "copy_revision_mismatch"))
        # SQL: an export reference whose hash differs from the stored export is refused
        forged = copy.deepcopy(handoff)
        forged.update(id=str(uuid.uuid4()), version_id=str(uuid.uuid4()), idempotency_key=str(uuid.uuid4()))
        forged["exports"][0]["sha256"] = "e" * 64
        forged["sha256"] = artifact_sha256(forged)
        conn, cur = self.sql_as("cs_api", self.users["producer_a"])
        try:
            self.assertSqlState("CS422", lambda: cur.execute(
                "select * from public.cs_enqueue_handoff(%s, %s::jsonb, %s, %s)",
                (self.ws_a, json.dumps(forged), forged["idempotency_key"], "c" * 64)))
        finally:
            conn.close()
        with self.pg.connect() as conn:
            cur = conn.cursor()
            self.assertSqlState("CS409", lambda: cur.execute(
                "update public.cs_handoffs set payload = '{}'::jsonb where id = %s", (handoff["id"],)))
            self.assertSqlState("CS409", lambda: cur.execute("delete from public.cs_exports"))
            self.assertSqlState("23514", lambda: cur.execute(
                "update public.cs_handoffs set state = 'received' where id = %s", (handoff["id"],)))

    def test_11_wrong_stage_lease_cannot_publish(self):
        st, comp = self._create()
        lease, manifest, _ = self._export_flow(comp["data"])
        self._seed_run(self.ws_a, f"other-{uuid.uuid4()}", stage="compose_image", node="compose")
        with worker_transaction(self.settings.worker_database_url) as cur:
            cur.execute("select work_item_id, lease_token, fencing_token from public.cs_claim_work("
                        "'test', 'w-b4-2', array['compose_image']::cs_work_stage[], 1)")
            other = cur.fetchone()
        self.assertRaisesRegex(Exception, "wrong_stage", self._publish, other, manifest)

    # -- 99 idempotent re-run ----------------------------------------------------------
    def test_99_migration_rerun_is_idempotent(self):
        with self.pg.connect() as conn:
            cur = conn.cursor()
            counts = []
            for t in B4_TABLES:
                cur.execute(f"select count(*) from public.{t}")
                counts.append(cur.fetchone()[0])
            grants = "select string_agg(p.oid::regprocedure::text || ':' || coalesce(p.proacl::text,''), ';' order by 1) " \
                     "from pg_proc p where p.proname like 'cs\\_%%'"
            cur.execute(grants)
            policies = cur.fetchone()[0]
            cur.execute(CS_B4_MIGRATION.read_text(encoding="utf-8"))
            cur.execute(CS_B4_MIGRATION.read_text(encoding="utf-8"))
            # and CS-B1's own re-run leaves B4 intact (no B4 policies exist to be dropped)
            self.pg.reapply_cs_b1()
            after = []
            for t in B4_TABLES:
                cur.execute(f"select count(*) from public.{t}")
                after.append(cur.fetchone()[0])
            cur.execute(grants)
            self.assertEqual((after, cur.fetchone()[0]), (counts, policies))
        st, out = self._create()
        self.assertEqual(st, 201, out)


def _call_with(app, method, path, tok, body, idem, extra):
    import io
    raw = json.dumps(body).encode()
    environ = {"REQUEST_METHOD": method, "PATH_INFO": path, "QUERY_STRING": "", "wsgi.input": io.BytesIO(raw),
               "CONTENT_LENGTH": str(len(raw)), "SERVER_NAME": "test", "SERVER_PORT": "80", "wsgi.url_scheme": "http",
               "HTTP_AUTHORIZATION": f"Bearer {tok}", "HTTP_IDEMPOTENCY_KEY": idem, **extra}
    captured = {}

    def start_response(status, headers):
        captured["status"] = int(status.split()[0])
    out = b"".join(app(environ, start_response))
    return captured["status"], json.loads(out)


if __name__ == "__main__":
    unittest.main()
