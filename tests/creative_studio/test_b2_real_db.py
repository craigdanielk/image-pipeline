"""CS-B2 / NS-10800 / PT-9636 real PostgreSQL 17 acceptance suite.

Applies every file in supabase/migrations (filename order) on a private throwaway
PG17 cluster via CS-B1's harness, runs the real service code (WSGI API + worker
stage) against it, with a recorded-shape Marketing OS mock and CS-B1's Storage
stand-in (access decided by the migration's own storage RLS). No network beyond
loopback, no provider, no production.

Accept (CS-B2): unapproved/stale brand refused, approved one frozen with hashes;
request from fixture ContentPack -> frozen treatment whose hash is stable across
re-runs; workspace isolation; service offering compiles without geometry.
Plus ENG-03 §5: anon denied, authenticated direct write denied, spoofed actor
ignored, idempotent migration re-run.

Run:  python3 -m pytest tests/creative_studio/test_b2_real_db.py -v
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import unittest
import uuid

import psycopg2

from service.api import create_app
from service.brand.importer import StorageObjectWriter, run_import_once
from service.brand.mos_brandkit import build_package
from service.config import Settings
from service.requests import contracts, documents
from service.requests.canonical import artifact_sha256, canonical_text, request_semantic_sha256
from tests.creative_studio import b2_mos_fixtures as fx
from tests.creative_studio.fake_storage import FakeStorage
from tests.creative_studio.pg_harness import MIGRATIONS_DIR, ThrowawayCluster, claims_json
from tests.creative_studio.test_cs_b1_real_db import ISSUER, JWT_SECRET, token


def call(app, method, path, *, tok=None, body=None, idem=None, headers=None):
    raw = json.dumps(body).encode() if body is not None else b""
    environ = {"REQUEST_METHOD": method, "PATH_INFO": path, "QUERY_STRING": "", "wsgi.input": io.BytesIO(raw),
               "CONTENT_LENGTH": str(len(raw)), "SERVER_NAME": "test", "SERVER_PORT": "80", "wsgi.url_scheme": "http"}
    if tok:
        environ["HTTP_AUTHORIZATION"] = f"Bearer {tok}"
    if idem:
        environ["HTTP_IDEMPOTENCY_KEY"] = idem
    environ.update(headers or {})
    captured = {}

    def start_response(status, _headers):
        captured["status"] = int(status.split()[0])
    out = b"".join(app(environ, start_response))
    return captured["status"], json.loads(out)

B2_MIGRATION = MIGRATIONS_DIR / "20261031140100_cs_b2_brand_campaign_inputs.sql"


class CsB2RealDatabase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pg = ThrowawayCluster()
        cls.pg.init()
        cls.applied = cls.pg.install()
        with cls.pg.connect() as conn:
            conn.cursor().execute("update cs_private.cs_runtime_limits set admission_enabled = true "
                                  "where environment_id = 'test'")
        cls.storage = FakeStorage(cls.pg.dsn(), JWT_SECRET, ISSUER).start()
        cls.settings = Settings(
            api_database_url=cls.pg.dsn(user="cs_api"), worker_database_url=cls.pg.dsn(user="cs_worker"),
            environment_id="test", release_id="test-release", jwt_issuer=ISSUER, jwt_audience="authenticated",
            jwt_secret=JWT_SECRET, jwks_url=None, storage_url=cls.storage.base_url, storage_anon_key="anon-test",
            signed_download_seconds=300, download_grant_seconds=60, worker_secret="w", runtime_principal="cs-worker:test")
        cls.users = {}
        with cls.pg.connect() as conn:
            cur = conn.cursor()
            for name in ("owner_a", "producer_a", "viewer_a", "owner_b", "producer_b", "outsider", "machine"):
                uid = str(uuid.uuid4())
                cur.execute("insert into auth.users (id, email) values (%s, %s)", (uid, f"{name}@example-studio.test"))
                cls.users[name] = uid
            cur.execute("insert into cs_private.cs_runtime_principals (user_id, environment_id) values (%s, 'test')",
                        (cls.users["machine"],))
        cls.tok = {k: token(v) for k, v in cls.users.items()}
        cls.writer = StorageObjectWriter(cls.settings, lambda: cls.tok["machine"])

        cls.home, cls.home_camp = fx.home_services()
        cls.physio, cls.physio_camp = fx.physio_clinic()
        cls.kitchen, cls.kitchen_camp = fx.kitchenware()
        cls.gw = FakeMos = fx.FakeMarketingOs((cls.home, cls.home_camp), (cls.physio, cls.physio_camp),
                                               (cls.kitchen, cls.kitchen_camp))
        cls.app = create_app(cls.settings)
        cls.app.mos_gateway = FakeMos

        cls.acct_a = cls._ok(call(cls.app, "POST", "/api/v1/accounts", tok=cls.tok["owner_a"],
                                  body={"name": "Example Studio Agency"}, idem=str(uuid.uuid4())))["account_id"]
        cls.acct_b = cls._ok(call(cls.app, "POST", "/api/v1/accounts", tok=cls.tok["owner_b"],
                                  body={"name": "Example Other Agency"}, idem=str(uuid.uuid4())))["account_id"]
        cls.ws_a = cls._workspace("owner_a", cls.acct_a, "Home services studio")
        cls.ws_b = cls._workspace("owner_b", cls.acct_b, "Clinic studio")
        for who, role in (("producer_a", "producer"), ("viewer_a", "viewer")):
            cls._ok(call(cls.app, "PUT", f"/api/v1/workspaces/{cls.ws_a}/members/{cls.users[who]}",
                         tok=cls.tok["owner_a"], body={"role": role}, idem=str(uuid.uuid4())))
        cls._ok(call(cls.app, "PUT", f"/api/v1/workspaces/{cls.ws_b}/members/{cls.users['producer_b']}",
                     tok=cls.tok["owner_b"], body={"role": "producer"}, idem=str(uuid.uuid4())))
        cls.conn_a = cls._connect("owner_a", cls.ws_a, cls.home.mos_ws)
        cls.conn_b = cls._connect("owner_b", cls.ws_b, cls.physio.mos_ws)
        cls.pkg_a = cls._import("producer_a", cls.ws_a, cls.conn_a, cls.home)
        cls.pkg_b = cls._import("producer_b", cls.ws_b, cls.conn_b, cls.physio)
        cls.ws_k = cls._workspace("owner_a", cls.acct_a, "Kitchenware studio")
        cls.conn_k = cls._connect("owner_a", cls.ws_k, cls.kitchen.mos_ws)
        cls.pkg_k = cls._import("owner_a", cls.ws_k, cls.conn_k, cls.kitchen)

    @classmethod
    def tearDownClass(cls):
        cls.storage.stop()
        cls.pg.stop()

    # -- helpers -----------------------------------------------------------------
    @staticmethod
    def _ok(result):
        status, body = result
        assert status in (200, 201, 202), (status, body)
        return body

    @classmethod
    def _workspace(cls, who, account, name):
        return cls._ok(call(cls.app, "POST", f"/api/v1/accounts/{account}/workspaces", tok=cls.tok[who],
                            body={"name": name}, idem=str(uuid.uuid4())))["workspace_id"]

    @classmethod
    def _connect(cls, who, ws, mos_ws):
        return cls._ok(call(cls.app, "POST", f"/api/v1/workspaces/{ws}/connections", tok=cls.tok[who],
                            body={"authority": "marketing-os", "external_workspace_id": mos_ws},
                            idem=str(uuid.uuid4())))["connection_id"]

    @classmethod
    def _admit(cls, who, ws, conn, biz, idem=None):
        return call(cls.app, "POST", f"/api/v1/workspaces/{ws}/brand-packages/imports", tok=cls.tok[who],
                    body={"connection_id": conn, "package": biz.brandkit_ref()}, idem=idem or str(uuid.uuid4()))

    @classmethod
    def _drain(cls):
        results = []
        while True:
            r = run_import_once(cls.settings, cls.gw, cls.writer)
            if r["claimed"] == 0:
                return results
            results.append(r)

    @classmethod
    def _import(cls, who, ws, conn, biz):
        st, admitted = cls._admit(who, ws, conn, biz)
        assert st == 202, (st, admitted)
        cls._drain()
        st, op = call(cls.app, "GET", f"/api/v1/workspaces/{ws}/operations/{admitted['operation_id']}", tok=cls.tok[who])
        assert op["operation"]["state"] == "completed", op
        return {"op": op["operation"], "ref": op["operation"]["result_refs"][0]}

    def _fresh_workspace(self, biz):
        """New Studio workspace in account A connected to `biz`'s MOS workspace."""
        ws = self._workspace("owner_a", self.acct_a, f"Scenario {uuid.uuid4().hex[:6]}")
        return ws, self._connect("owner_a", ws, biz.mos_ws)

    def _import_op(self, ws, conn, biz, who="owner_a"):
        st, admitted = self._admit(who, ws, conn, biz)
        self.assertEqual(st, 202, admitted)
        self._drain()
        st, op = call(self.app, "GET", f"/api/v1/workspaces/{ws}/operations/{admitted['operation_id']}",
                      tok=self.tok[who])
        self.assertEqual(st, 200, op)
        return op

    def sql(self, role="cs_api", user=None):
        conn = psycopg2.connect(self.pg.dsn(user=role if role in ("cs_api", "cs_worker") else None))
        cur = conn.cursor()
        if role not in ("cs_api", "cs_worker"):
            cur.execute(f"set role {role}")
        if user:
            cur.execute("select set_config('request.jwt.claims', %s, false)", (claims_json(user),))
        conn.commit()
        return conn, cur

    def assertSqlState(self, state, fn, contains=None):
        with self.assertRaises(psycopg2.Error) as ctx:
            fn()
        self.assertEqual(ctx.exception.pgcode, state, ctx.exception.pgerror)
        if contains:
            self.assertIn(contains, ctx.exception.pgerror)

    def _intake(self, who, ws, conn, camp, idem=None, **vr):
        return call(self.app, "POST", f"/api/v1/workspaces/{ws}/creative-requests/from-marketing-os",
                    tok=self.tok[who], idem=idem or str(uuid.uuid4()),
                    body={"connection_id": conn, "visual_request": camp.visual_request(**vr)})

    def _accept(self, who, ws, tref):
        return call(self.app, "POST", f"/api/v1/workspaces/{ws}/treatments/{tref['id']}/accept", tok=self.tok[who],
                    idem=str(uuid.uuid4()), body={"version": tref}, headers={"HTTP_IF_MATCH": f'"{tref["sha256"]}"'})

    # -- 01 schema hygiene ---------------------------------------------------------
    def test_01_migrations_and_hygiene(self):
        self.assertEqual(self.applied[-1], B2_MIGRATION.name)
        with self.pg.connect() as conn:
            cur = conn.cursor()
            cur.execute("""select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace
                            where c.relkind = 'r' and n.nspname in ('public','cs_private') and c.relname like 'cs\\_%%'
                              and not (c.relrowsecurity and c.relforcerowsecurity)""")
            self.assertEqual(cur.fetchall(), [])
            cur.execute("""select p.oid::regprocedure::text from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                            where ((n.nspname = 'public' and p.proname like 'cs\\_%%') or n.nspname = 'cs_private')
                              and p.proname <> 'cs_storage_allowed'
                              and (has_function_privilege('anon', p.oid, 'execute')
                                   or has_function_privilege('authenticated', p.oid, 'execute'))""")
            self.assertEqual(cur.fetchall(), [], "no cs_* function executable by browser roles")
            cur.execute("""select count(*) from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                            where p.prosecdef and (n.nspname = 'cs_private' or p.proname like 'cs\\_%%')
                              and not exists (select 1 from unnest(coalesce(p.proconfig,'{}')) c where c like 'search_path=%%')""")
            self.assertEqual(cur.fetchone()[0], 0)
            cur.execute("select has_function_privilege('authenticated', 'cs_private.cs_storage_allowed(text,text,text)', 'execute')")
            self.assertTrue(cur.fetchone()[0], "B1 storage helper grant survives B2")
        for role in ("anon", "authenticated"):
            conn, cur = self.sql(role, self.users["owner_a"])
            try:
                for stmt in ("select * from public.cs_source_imports", "select * from public.cs_creative_request_versions",
                             "select * from public.cs_operations", "select * from public.cs_authoring_drafts",
                             f"insert into public.cs_external_bindings (workspace_id, authority, external_workspace_id, "
                             f"verification, verification_sha256, created_by) values ('{self.ws_a}', 'marketing-os', "
                             f"'{uuid.uuid4()}', '{{}}', repeat('a',64), '{self.users['owner_a']}')",
                             f"select public.cs_brand_package_eligibility('{self.ws_a}', '{uuid.uuid4()}', 'generate', null)"):
                    self.assertSqlState("42501", lambda: cur.execute(stmt))
                    conn.rollback()
            finally:
                conn.close()

    # -- 02 connections ------------------------------------------------------------
    def test_02_connection_requires_authority_verification_and_manage(self):
        ws = self._workspace("owner_a", self.acct_a, "Binding checks")
        other, _ = self.gw.add(*fx.kitchenware("binding-checks"))
        self.gw.deny_binding.add(other.mos_ws)
        try:
            st, body = call(self.app, "POST", f"/api/v1/workspaces/{ws}/connections", tok=self.tok["owner_a"],
                            body={"authority": "marketing-os", "external_workspace_id": other.mos_ws},
                            idem=str(uuid.uuid4()))
            self.assertEqual((st, body["error"]), (403, "authorization_blocked"))
        finally:
            self.gw.deny_binding.discard(other.mos_ws)
        st, body = call(self.app, "POST", f"/api/v1/workspaces/{self.ws_a}/connections", tok=self.tok["producer_a"],
                        body={"authority": "marketing-os", "external_workspace_id": other.mos_ws},
                        idem=str(uuid.uuid4()))
        self.assertEqual((st, body["error"]), (403, "capability_denied"))
        key = str(uuid.uuid4())
        first = call(self.app, "POST", f"/api/v1/workspaces/{ws}/connections", tok=self.tok["owner_a"],
                     body={"authority": "marketing-os", "external_workspace_id": other.mos_ws}, idem=key)
        again = call(self.app, "POST", f"/api/v1/workspaces/{ws}/connections", tok=self.tok["owner_a"],
                     body={"authority": "marketing-os", "external_workspace_id": other.mos_ws}, idem=key)
        self.assertEqual((first[0], again[0]), (201, 200))
        self.assertEqual(first[1]["connection_id"], again[1]["connection_id"])
        # one MOS workspace maps to exactly one Studio workspace (no implicit sharing)
        ws_other = self._workspace("owner_a", self.acct_a, "Second binding")
        st, body = call(self.app, "POST", f"/api/v1/workspaces/{ws_other}/connections", tok=self.tok["owner_a"],
                        body={"authority": "marketing-os", "external_workspace_id": other.mos_ws},
                        idem=str(uuid.uuid4()))
        self.assertEqual((st, body["error"]), (409, "binding_exists"))
        # SQL refuses a verification that does not name this user (body assertion is not authority)
        conn, cur = self.sql("cs_api", self.users["owner_a"])
        try:
            ws2 = self._workspace("owner_a", self.acct_a, "Spoof check")
            fake = {"authority": "marketing-os", "external_workspace_id": self.physio.mos_ws, "decision": "allowed",
                    "subject_user_id": self.users["owner_b"]}
            self.assertSqlState("CS403", lambda: cur.execute(
                "select * from public.cs_bind_external_workspace(%s,'marketing-os',%s,%s,gen_random_uuid(),repeat('a',64))",
                (ws2, self.physio.mos_ws, json.dumps(fake))), "authorization_blocked")
        finally:
            conn.close()

    # -- 03 approved package frozen with hashes ---------------------------------------
    def test_03_approved_brand_frozen_with_hashes(self):
        ref = self.pkg_a["ref"]
        st, body = call(self.app, "GET", f"/api/v1/workspaces/{self.ws_a}/brand-packages/{ref['version_id']}",
                        tok=self.tok["viewer_a"])
        self.assertEqual(st, 200, body)
        pkg = body["package"]
        contracts.validate(pkg, "brand-package-v1")
        self.assertEqual(pkg["sha256"], artifact_sha256(pkg), "stored digest is RFC 8785 of the stored document")
        self.assertEqual(ref["sha256"], pkg["sha256"])
        self.assertEqual(pkg["approval"]["subject"]["version_id"], self.home.artifact_id)
        self.assertEqual(body["eligibility"]["generate"]["decision"], "allowed", body["eligibility"])
        self.assertEqual(body["eligibility"]["export"]["decision"], "allowed", body["eligibility"])
        # every file: declared hash == MOS bytes == mirrored Studio version == stored object bytes
        mirrors = {a["sha256"]: a for a in self.pkg_a["op"]["result_asset_refs"]}
        self.assertEqual(len(mirrors), len(pkg["assets"]))
        with self.pg.connect() as conn:
            cur = conn.cursor()
            for asset in pkg["assets"]:
                src = asset["ref"]
                blob = self.home.blobs[src["asset_version_id"]]
                self.assertEqual(hashlib.sha256(blob).hexdigest(), src["sha256"])
                studio = mirrors[src["sha256"]]
                cur.execute("select storage_key, size_bytes, work_item_id is not null, run_id from public.cs_asset_versions "
                            "where workspace_id = %s and id = %s", (self.ws_a, studio["asset_version_id"]))
                key, size, has_item, run_id = cur.fetchone()
                self.assertEqual((size, has_item, run_id), (len(blob), True, None))
                self.assertEqual(self.storage.blobs[f"creative-studio/{key}"], blob)
            # frozen: even the migration owner cannot rewrite the snapshot or its mirrors
            for stmt in ("update public.cs_source_imports set payload = payload || '{\"brand_name\":\"x\"}'",
                         "delete from public.cs_source_asset_mirrors", "update public.cs_asset_versions set sha256 = sha256"):
                self.assertSqlState("CS409", lambda: cur.execute(stmt))
        # re-import of the same approved version reuses the exact package and mirrors (no refetch)
        before = len([c for c in self.gw.calls if c[0] == "fetch_asset"])
        st, admitted = self._admit("producer_a", self.ws_a, self.conn_a, self.home)
        self.assertEqual(st, 202)
        self._drain()
        st, op = call(self.app, "GET", f"/api/v1/workspaces/{self.ws_a}/operations/{admitted['operation_id']}",
                      tok=self.tok["producer_a"])
        self.assertEqual(op["operation"]["state"], "completed")
        self.assertEqual(op["operation"]["result_refs"], [ref])
        self.assertEqual(sorted(a["asset_version_id"] for a in op["operation"]["result_asset_refs"]),
                         sorted(a["asset_version_id"] for a in self.pkg_a["op"]["result_asset_refs"]))
        self.assertEqual(len([c for c in self.gw.calls if c[0] == "fetch_asset"]), before)
        self.assertEqual([e["kind"] for e in op["events"]],
                         ["operation.admitted", "work.claimed", "import.attached", "work.finished", "operation.completed"])
        # same idempotency key -> same operation, body change -> conflict
        key = str(uuid.uuid4())
        a1 = self._admit("producer_a", self.ws_a, self.conn_a, self.home, idem=key)
        a2 = self._admit("producer_a", self.ws_a, self.conn_a, self.home, idem=key)
        self.assertEqual((a1[0], a2[0], a1[1]["operation_id"]), (202, 200, a2[1]["operation_id"]))
        bad = copy.deepcopy(self.home.brandkit_ref())
        bad["revision"] = 7
        st, body = call(self.app, "POST", f"/api/v1/workspaces/{self.ws_a}/brand-packages/imports",
                        tok=self.tok["producer_a"], idem=key, body={"connection_id": self.conn_a, "package": bad})
        self.assertEqual((st, body["error"]), (409, "idempotency_conflict"))
        self._drain()

    # -- 04 unapproved / stale / incomplete / tampered refused -------------------------
    def test_04_unapproved_brand_refused(self):
        biz, _ = self.gw.add(*fx.home_services("home-unapproved"))
        biz.status = "draft"
        ws, conn = self._fresh_workspace(biz)
        op = self._import_op(ws, conn, biz)["operation"]
        self.assertEqual(op["state"], "failed")
        self.assertEqual(op["error"]["code"], "brand_not_approved")
        self.assertEqual((op["result_refs"], op["result_asset_refs"]), ([], []))
        with self.pg.connect() as c:
            cur = c.cursor()
            cur.execute("select count(*) from public.cs_source_imports where workspace_id = %s", (ws,))
            self.assertEqual(cur.fetchone()[0], 0)
            cur.execute("select count(*) from public.cs_assets where workspace_id = %s", (ws,))
            self.assertEqual(cur.fetchone()[0], 0, "nothing mirrored for an unapproved kit")

    def test_05_sql_refuses_unapproved_or_unmirrored_package_directly(self):
        """The database refuses even if the worker code were wrong."""
        biz, _ = self.gw.add(*fx.kitchenware("kitchen-sql"))
        ws, conn = self._fresh_workspace(biz)
        st, admitted = self._admit("owner_a", ws, conn, biz)
        build = build_package(biz.export())
        wconn = psycopg2.connect(self.pg.dsn(user="cs_worker"))
        try:
            cur = wconn.cursor()
            cur.execute("select work_item_id, lease_token, fencing_token from public.cs_claim_work("
                        "'test','test-worker',array['import_brand_package']::cs_work_stage[],1)")
            lease = cur.fetchone()
            wconn.commit()
            canon = canonical_text({k: v for k, v in build.document.items() if k != "sha256"})
            args = (*lease, json.dumps(build.document), canon, json.dumps(build.source_ref))
            q = "select * from public.cs_import_attach_package(%s,%s,%s,%s,%s,%s,%s,%s)"
            self.assertSqlState("CS409", lambda: cur.execute(q, (*args, "draft", json.dumps(build.approval_receipt_ref))),
                                "brand_not_approved")
            wconn.rollback()
            self.assertSqlState("CS409", lambda: cur.execute(q, (*args, "approved", json.dumps(build.approval_receipt_ref))),
                                "source_unavailable")
            wconn.rollback()
            tampered = copy.deepcopy(build.document)
            tampered["brand_name"] = "Tampered"
            self.assertSqlState("CS422", lambda: cur.execute(q, (*lease, json.dumps(tampered), canon,
                                json.dumps(build.source_ref), "approved", json.dumps(build.approval_receipt_ref))),
                                "document_digest_mismatch")
            wconn.rollback()
            other = copy.deepcopy(build.source_ref)
            other["version_id"] = str(uuid.uuid4())
            self.assertSqlState("CS409", lambda: cur.execute(q, (*lease, json.dumps(build.document), canon,
                                json.dumps(other), "approved", json.dumps(build.approval_receipt_ref))),
                                "source_revision_changed")
            wconn.rollback()
            # an operation cannot complete without attached results (no fabricated completion)
            self.assertSqlState("CS409", lambda: cur.execute(
                "select public.cs_finish_work(%s,%s,%s,'x',null,'[]'::jsonb,repeat('a',64),'r')", lease),
                "operation_result_missing")
            wconn.rollback()
            # stale fence after a newer claim cannot write
            cur.execute("update public.cs_work_items set lease_until = now() - interval '1 second' where id = %s",
                        (lease[0],))
            wconn.commit()
            cur.execute("select work_item_id, lease_token, fencing_token from public.cs_claim_work("
                        "'test','test-worker-2',array['import_brand_package']::cs_work_stage[],1)")
            newer = cur.fetchone()
            wconn.commit()
            self.assertGreater(newer[2], lease[2])
            self.assertSqlState("CS409", lambda: cur.execute("select public.cs_import_fail(%s,%s,%s,'x','x','[]')", lease),
                                "lease_lost")
            wconn.rollback()
            cur.execute("select public.cs_import_fail(%s,%s,%s,'test_abandoned','test','[]')", newer)
            cur.execute("select public.cs_finish_work(%s,%s,%s,'operation_failed',null,'[]'::jsonb,repeat('a',64),'r')", newer)
            wconn.commit()
        finally:
            wconn.close()

    def test_06_stale_brand_refused(self):
        biz, camp = self.gw.add(*fx.home_services("home-stale-a"))
        # (a) at import: MOS already has a newer approved version
        old_ref = biz.brandkit_ref()
        biz.current = {"version_id": fx.uid("brandkit", "home-stale-a", 2), "revision": 2, "status": "approved"}
        ws, conn = self._fresh_workspace(biz)
        op = self._import_op(ws, conn, biz)["operation"]
        self.assertEqual((op["state"], op["error"]["code"]), ("failed", "approval_stale"))
        # (b) after import: MOS approves a new version; the old package becomes stale for new work
        biz, camp = self.gw.add(*fx.home_services("home-stale-b"))
        old_ref = biz.brandkit_ref()
        ws, conn = self._fresh_workspace(biz)
        op = self._import_op(ws, conn, biz)["operation"]
        self.assertEqual(op["state"], "completed", op)
        old_pkg = op["result_refs"][0]
        st, body = self._intake("owner_a", ws, conn, camp)
        self.assertEqual(st, 201, body)
        biz.publish_new_version()
        st, refreshed = call(self.app, "POST", f"/api/v1/workspaces/{ws}/connections/{conn}/refresh",
                             tok=self.tok["owner_a"], body={})
        self.assertEqual(st, 200, refreshed)
        self.assertEqual(refreshed["eligibility"]["decision"], "refused")
        self.assertIn("approval_stale", refreshed["eligibility"]["reason_codes"])
        st, body = self._intake("owner_a", ws, conn, camp)
        self.assertEqual((st, body["error"]), (409, "approval_stale"), body)
        conn_sql, cur = self.sql("cs_api", self.users["owner_a"])
        try:
            self.assertSqlState("CS409", lambda: cur.execute(
                "select cs_private.cs_require_brand_eligible(%s,%s,'generate',null)", (ws, old_pkg["version_id"])),
                "approval_stale")
        finally:
            conn_sql.close()
        # the earlier saved request is historical and still readable, never mutated
        st, body = call(self.app, "GET", f"/api/v1/workspaces/{ws}/brand-packages/{old_pkg['version_id']}",
                        tok=self.tok["owner_a"])
        self.assertEqual(body["package"]["sha256"], old_pkg["sha256"])
        # explicit rebase: import the new approved version, intake works again (ContentPack pins it)
        op = self._import_op(ws, conn, biz)["operation"]
        self.assertEqual(op["state"], "completed", op)
        self.assertNotEqual(op["result_refs"][0]["version_id"], old_pkg["version_id"])
        self.assertEqual(op["result_refs"][0]["revision"], 2)
        camp.pack["pins"]["value"]["brand_kit"] = {"id": biz.artifact_id, "version": 2}
        st, body = self._intake("owner_a", ws, conn, camp)
        self.assertEqual(st, 201, body)
        self.assertNotEqual(old_ref["version_id"], biz.artifact_id)

    def test_07_rights_and_incomplete_kits_typed_refusals(self):
        def variant(key, mutate):
            biz, _ = self.gw.add(*fx.home_services(key))
            mutate(biz)
            ws, conn = self._fresh_workspace(biz)
            return biz, ws, self._import_op(ws, conn, biz)["operation"]

        def expire_logo(biz):
            f = next(f for f in biz.files if f["kind"] == "logo")
            f["rights"] = fx.rights("expired-logo", expires_at="2026-01-01T00:00:00.000Z")
        _, ws, op = variant("rights-expired", expire_logo)
        self.assertEqual(op["state"], "completed", "rights are independent of guide approval at import")
        vid = op["result_refs"][0]["version_id"]
        st, body = call(self.app, "GET", f"/api/v1/workspaces/{ws}/brand-packages/{vid}", tok=self.tok["owner_a"])
        self.assertEqual(body["eligibility"]["export"]["reason_codes"], ["rights_expired"])
        self.assertEqual(body["eligibility"]["generate"]["decision"], "allowed", "no reference image depends on it")
        conn_sql, cur = self.sql("cs_api", self.users["owner_a"])
        try:
            self.assertSqlState("CS403", lambda: cur.execute(
                "select cs_private.cs_require_brand_eligible(%s,%s,'export',null)", (ws, vid)), "rights_expired")
        finally:
            conn_sql.close()

        def unknown_font(biz):
            f = next(f for f in biz.files if f["kind"] == "font")
            f["rights"] = fx.rights("unknown-font", status="unknown", basis="unknown", uses=())
        _, ws, op = variant("rights-unknown", unknown_font)
        st, body = call(self.app, "GET", f"/api/v1/workspaces/{ws}/brand-packages/{op['result_refs'][0]['version_id']}",
                        tok=self.tok["owner_a"])
        self.assertEqual(body["eligibility"]["export"]["reason_codes"], ["rights_unknown"])

        def channel_limited(biz):
            for f in biz.files:
                if f["kind"] == "logo":
                    f["rights"] = fx.rights("ig-only-" + f["name"], channels=("instagram",))
        _, ws, op = variant("rights-channel", channel_limited)
        conn_sql, cur = self.sql("cs_api", self.users["owner_a"])
        try:
            cur.execute("select public.cs_brand_package_eligibility(%s,%s,'export','instagram,facebook')",
                        (ws, op["result_refs"][0]["version_id"]))
            self.assertEqual(cur.fetchone()[0]["reason_codes"], ["rights_channel_not_covered"])
        finally:
            conn_sql.close()

        cases = {
            "no-logo-usage": (lambda b: b.content.pop("logo_usage"), "brandkit_incomplete"),
            "no-font-files": (lambda b: [b.files.remove(f) for f in [f for f in b.files if f["kind"] == "font"]],
                              "font_asset_missing"),
            "bad-receipt": (lambda b: setattr(b, "approval", lambda: {**fx.Business.approval(b), "actor_role": "x"}),
                            "approval_receipt_invalid"),
        }
        for key, (mutate, code) in cases.items():
            _, _, op = variant(key, mutate)
            self.assertEqual((op["state"], op["error"]["code"]), ("failed", code), key)

        # bytes that do not match the declared version are refused, nothing is mirrored
        biz, _ = self.gw.add(*fx.home_services("tampered"))
        logo = next(f for f in biz.files if f["kind"] == "logo")
        self.gw.tamper[logo["ref"]["asset_version_id"]] = b"\x89PNG\r\n\x1a\nnot-the-approved-logo"
        ws, conn = self._fresh_workspace(biz)
        op = self._import_op(ws, conn, biz)["operation"]
        self.assertEqual((op["state"], op["error"]["code"]), ("failed", "asset_hash_mismatch"))

    # -- 08 ContentPack -> request -> frozen treatment, stable hash ----------------------
    def test_08_request_from_contentpack_to_frozen_treatment_stable_hash(self):
        runs = []
        for attempt in range(2):
            st, body = self._intake("producer_a", self.ws_a, self.conn_a, self.home_camp)
            self.assertEqual(st, 201, body)
            st, acc = self._accept("producer_a", self.ws_a, body["treatment"])
            self.assertEqual(st, 201, acc)
            runs.append((body, acc))
        (b1, a1), (b2, a2) = runs
        self.assertNotEqual(b1["request"]["version_id"], b2["request"]["version_id"], "two separate saved requests")
        self.assertEqual(b1["request_semantic_sha256"], b2["request_semantic_sha256"])
        self.assertEqual(b1["treatment_semantic_sha256"], b2["treatment_semantic_sha256"])
        self.assertEqual(a1["semantic_sha256"], b1["treatment_semantic_sha256"])
        self.assertEqual(a1["semantic_sha256"], a2["semantic_sha256"], "frozen treatment hash stable across re-runs")
        # stored documents: contract-valid, digest-bound, accepted revision carries actor/time
        st, rq = call(self.app, "GET", f"/api/v1/workspaces/{self.ws_a}/creative-requests/{b1['request']['id']}"
                      f"/versions/{b1['request']['version_id']}", tok=self.tok["viewer_a"])
        req = rq["request"]
        contracts.validate(req)
        self.assertEqual(req["sha256"], artifact_sha256(req))
        self.assertEqual(rq["semantic_sha256"], request_semantic_sha256(req))
        self.assertEqual(req["origin"]["kind"], "marketing-os")
        self.assertEqual(req["formats"][0]["preset"], "square_1080")
        self.assertEqual(req["offering"]["physical_product_spec"], None)
        self.assertEqual({c["id"] for c in req["exact_copy"]},
                         {"v1-headline", "v1-body-short", "v1-body-medium", "v1-body-long", "v1-cta"},
                         "flagged copy variant excluded; exact texts kept")
        self.assertEqual(req["budget"]["max_cost_micros"], 62500, "budget comes from the MOS reservation input")
        acc_ref = a1["treatment"]
        st, tr = call(self.app, "GET", f"/api/v1/workspaces/{self.ws_a}/treatments/{acc_ref['id']}"
                      f"/versions/{acc_ref['version_id']}", tok=self.tok["viewer_a"])
        t = tr["treatment"]
        contracts.validate(t)
        self.assertEqual((t["status"], t["revision"], t["accepted_by"]), ("accepted", 2, self.users["producer_a"]))
        self.assertEqual(t["source_request"]["version_id"], b1["request"]["version_id"])
        st, dr = call(self.app, "GET", f"/api/v1/workspaces/{self.ws_a}/authoring-drafts/{b1['draft_id']}",
                      tok=self.tok["viewer_a"])
        contracts.validate(dr["draft"])
        self.assertEqual(dr["draft"]["selections"]["request"], b1["request"])
        # replay with the same key returns the same saved work (response loss)
        key = str(uuid.uuid4())
        r1 = self._intake("producer_a", self.ws_a, self.conn_a, self.home_camp, idem=key)
        r2 = self._intake("producer_a", self.ws_a, self.conn_a, self.home_camp, idem=key)
        self.assertEqual((r1[0], r2[0]), (201, 200))
        self.assertEqual(r1[1]["request"], r2[1]["request"])
        # frozen: accepted revision cannot be rewritten; accepting twice is refused; changed content refused
        with self.pg.connect() as c:
            self.assertSqlState("CS409", lambda: c.cursor().execute(
                "update public.cs_treatment_versions set payload = payload where version_id = %s", (acc_ref["version_id"],)))
        st, body = self._accept("producer_a", self.ws_a, acc_ref)
        self.assertEqual((st, body["error"]), (409, "treatment_not_draft"))
        conn_sql, cur = self.sql("cs_api", self.users["producer_a"])
        try:
            st, b3 = self._intake("producer_a", self.ws_a, self.conn_a, self.home_camp)
            st, draft = call(self.app, "GET", f"/api/v1/workspaces/{self.ws_a}/treatments/{b3['treatment']['id']}"
                             f"/versions/{b3['treatment']['version_id']}", tok=self.tok["producer_a"])
            doc = documents.accepted_treatment(draft["treatment"], actor_id=self.users["producer_a"])
            doc["direction"]["scene"] = "Something the reviewer never saw"
            doc["sha256"] = artifact_sha256(doc)
            self.assertSqlState("CS409", lambda: cur.execute(
                "select * from public.cs_accept_treatment(%s,%s,%s,%s,%s,gen_random_uuid(),repeat('a',64))",
                (self.ws_a, json.dumps(doc), documents.canonical_for_db(doc), documents.treatment_semantic(doc),
                 draft["treatment"]["sha256"])), "acceptance_invalid")
        finally:
            conn_sql.close()
        # regulated service (selected copy, disclosure rule carried as a required constraint)
        st, body = self._intake("producer_b", self.ws_b, self.conn_b, self.physio_camp, canvas="4:5")
        self.assertEqual(st, 201, body)
        st, rq = call(self.app, "GET", f"/api/v1/workspaces/{self.ws_b}/creative-requests/{body['request']['id']}"
                      f"/versions/{body['request']['version_id']}", tok=self.tok["owner_b"])
        req = rq["request"]
        self.assertEqual((req["offering"]["kind"], req["formats"][0]["preset"]), ("service_group", "portrait_1080x1350"))
        self.assertEqual({c["source_copy"]["version_id"] for c in req["exact_copy"]},
                         {self.physio_camp.pack["selected_copy"]["value"]["copy_version_id"]})
        self.assertTrue(any("Individual results vary." in r for r in req["constraints"]["required"]))
        # unsupported device canvas is a typed refusal, never approximated
        st, body = self._intake("producer_a", self.ws_a, self.conn_a, self.home_camp, canvas="16:9")
        self.assertEqual((st, body["error"]), (422, "unsupported_canvas"))

    # -- 09 workspace isolation ------------------------------------------------------------
    def test_09_workspace_isolation(self):
        st, a = self._intake("producer_a", self.ws_a, self.conn_a, self.home_camp)
        self.assertEqual(st, 201, a)
        a_paths = [
            f"/api/v1/workspaces/{self.ws_a}/brand-packages/{self.pkg_a['ref']['version_id']}",
            f"/api/v1/workspaces/{self.ws_a}/operations/{self.pkg_a['op']['id']}",
            f"/api/v1/workspaces/{self.ws_a}/creative-requests/{a['request']['id']}/versions/{a['request']['version_id']}",
            f"/api/v1/workspaces/{self.ws_a}/treatments/{a['treatment']['id']}/versions/{a['treatment']['version_id']}",
            f"/api/v1/workspaces/{self.ws_a}/authoring-drafts/{a['draft_id']}",
            f"/api/v1/workspaces/{self.ws_a}/connections",
            # A's ids under B's scope
            f"/api/v1/workspaces/{self.ws_b}/brand-packages/{self.pkg_a['ref']['version_id']}",
            f"/api/v1/workspaces/{self.ws_b}/operations/{self.pkg_a['op']['id']}",
            f"/api/v1/workspaces/{self.ws_b}/authoring-drafts/{a['draft_id']}",
        ]
        for who in ("owner_b", "producer_b", "outsider"):
            for path in a_paths:
                st, body = call(self.app, "GET", path, tok=self.tok[who])
                if path.startswith(f"/api/v1/workspaces/{self.ws_b}") and who != "outsider":
                    self.assertEqual(st, 404, (who, path, body))
                else:
                    self.assertEqual((st, body), (404, {"error": "resource_unavailable"}), (who, path))
        # B cannot use A's connection or import into A
        st, body = self._admit("producer_b", self.ws_b, self.conn_a, self.home)
        self.assertEqual((st, body["error"]), (404, "resource_unavailable"))
        st, body = self._admit("producer_b", self.ws_a, self.conn_a, self.home)
        self.assertEqual((st, body["error"]), (404, "resource_unavailable"))
        # B's package ref cannot be imported through A's connection (scope of the connected MOS workspace)
        st, body = self._admit("producer_a", self.ws_a, self.conn_a, self.physio)
        self.assertEqual((st, body["error"]), (403, "scope_mismatch"))
        # B cannot pin A's package in a request, even with the exact ref
        st, rq = call(self.app, "GET", f"/api/v1/workspaces/{self.ws_a}/creative-requests/{a['request']['id']}"
                      f"/versions/{a['request']['version_id']}", tok=self.tok["owner_a"])
        fields = {k: rq["request"][k] for k in documents.REQUEST_EDITABLE}
        fields["origin"] = {"kind": "standalone", "direction_approval": rq["request"]["origin"]["concept_approval"]}
        st, body = call(self.app, "POST", f"/api/v1/workspaces/{self.ws_b}/creative-requests", tok=self.tok["producer_b"],
                        idem=str(uuid.uuid4()), body=fields)
        self.assertEqual((st, body["error"]), (404, "resource_unavailable"), body)
        # viewer of A reads but cannot produce
        st, body = call(self.app, "POST", f"/api/v1/workspaces/{self.ws_a}/creative-requests", tok=self.tok["viewer_a"],
                        idem=str(uuid.uuid4()), body=fields)
        self.assertEqual((st, body["error"]), (403, "capability_denied"))
        # SQL: B's producer sees zero of A's rows in every B2 table
        conn, cur = self.sql("cs_api", self.users["producer_b"])
        try:
            for table in ("cs_external_bindings", "cs_operations", "cs_operation_events", "cs_source_heads",
                          "cs_source_imports", "cs_source_asset_mirrors", "cs_creative_request_versions",
                          "cs_treatment_versions", "cs_authoring_drafts"):
                cur.execute(f"select count(*) from public.{table} where workspace_id = %s", (self.ws_a,))
                self.assertEqual(cur.fetchone()[0], 0, table)
                cur.execute(f"select count(*) from public.{table} where workspace_id = %s", (self.ws_b,))
                self.assertGreater(cur.fetchone()[0], 0, table)
            self.assertSqlState("CS404", lambda: cur.execute(
                "select public.cs_brand_package_eligibility(%s,%s,'generate',null)",
                (self.ws_a, self.pkg_a["ref"]["version_id"])))
            conn.rollback()
            self.assertSqlState("CS404", lambda: cur.execute(
                "select * from public.cs_admit_brand_import(%s,%s,%s,'test','r',gen_random_uuid(),repeat('a',64))",
                (self.ws_a, self.conn_a, json.dumps(self.home.brandkit_ref()))))
            conn.rollback()
            # direct writes bypassing the procedures are refused by RLS / grants
            self.assertSqlState("42501", lambda: cur.execute(
                "insert into public.cs_source_imports (workspace_id) values (%s)", (self.ws_b,)))
            conn.rollback()
            self.assertSqlState("42501", lambda: cur.execute(
                "update public.cs_operations set state = 'completed' where workspace_id = %s", (self.ws_b,)))
            conn.rollback()
            self.assertSqlState("42501", lambda: cur.execute(
                "insert into public.cs_work_items (workspace_id, environment_id, run_id, node_id, stage, dedupe_key) "
                "values (%s, 'test', gen_random_uuid(), 'n', 'compose_image', 'x')", (self.ws_b,)))
            conn.rollback()
        finally:
            conn.close()

    def test_10_spoofed_actor_and_revision_rules(self):
        st, a = self._intake("producer_a", self.ws_a, self.conn_a, self.home_camp)
        st, rq = call(self.app, "GET", f"/api/v1/workspaces/{self.ws_a}/creative-requests/{a['request']['id']}"
                      f"/versions/{a['request']['version_id']}", tok=self.tok["owner_a"])
        fields = {k: rq["request"][k] for k in documents.REQUEST_EDITABLE}
        conn, cur = self.sql("cs_api", self.users["producer_a"])
        try:
            spoof = documents.new_request(fields, workspace_id=self.ws_a, actor_id=self.users["owner_a"])
            self.assertSqlState("CS422", lambda: cur.execute(
                "select * from public.cs_save_creative_request(%s,%s,%s,%s,null,gen_random_uuid(),repeat('a',64))",
                (self.ws_a, json.dumps(spoof), documents.canonical_for_db(spoof), documents.request_semantic(spoof))),
                "actor_mismatch")
            conn.rollback()
            # a stale If-Match is a revision conflict; the current one creates revision 2
            rev = documents.new_request(fields, workspace_id=self.ws_a, actor_id=self.users["producer_a"],
                                        object_id=a["request"]["id"], revision=2)
            q = "select * from public.cs_save_creative_request(%s,%s,%s,%s,%s,gen_random_uuid(),repeat('a',64))"
            args = (self.ws_a, json.dumps(rev), documents.canonical_for_db(rev), documents.request_semantic(rev))
            self.assertSqlState("CS409", lambda: cur.execute(q, (*args, "0" * 64)), "revision_conflict")
            conn.rollback()
            cur.execute(q, (*args, a["request"]["sha256"]))
            self.assertEqual(cur.fetchone()[2], False)
            conn.commit()
        finally:
            conn.close()
        # drafts: optimistic save, stale If-Match refused, promotion refused here
        st, d = call(self.app, "POST", f"/api/v1/workspaces/{self.ws_a}/authoring-drafts", tok=self.tok["producer_a"],
                     body={"name": "Spring campaign"})
        self.assertEqual(st, 201, d)
        draft = d["draft"]
        path = f"/api/v1/workspaces/{self.ws_a}/authoring-drafts/{draft['id']}"
        st, s1 = call(self.app, "PUT", path, tok=self.tok["producer_a"], body={"step": "campaign"},
                      headers={"HTTP_IF_MATCH": draft["sha256"]})
        self.assertEqual((st, s1["revision"]), (200, 2))
        st, s2 = call(self.app, "PUT", path, tok=self.tok["producer_a"], body={"step": "outputs"},
                      headers={"HTTP_IF_MATCH": draft["sha256"]})
        self.assertEqual((st, s2["error"]), (409, "revision_conflict"))
        st, s3 = call(self.app, "PUT", path, tok=self.tok["producer_a"], body={"status": "promoted"},
                      headers={"HTTP_IF_MATCH": s1["draft"]["sha256"]})
        self.assertEqual((st, s3["error"]), (422, "promotion_not_available"))
        foreign = dict(self.pkg_b["ref"])
        sel = dict(s1["draft"]["selections"], brand_package=foreign)
        st, s4 = call(self.app, "PUT", path, tok=self.tok["producer_a"], body={"selections": sel},
                      headers={"HTTP_IF_MATCH": s1["draft"]["sha256"]})
        self.assertEqual((st, s4["error"]), (404, "resource_unavailable"), "cannot select another workspace's package")

    # -- 11 service offering compiles without geometry ------------------------------------
    def test_11_service_offering_compiles_without_geometry(self):
        for who, ws, conn, camp in (("producer_a", self.ws_a, self.conn_a, self.home_camp),
                                    ("producer_b", self.ws_b, self.conn_b, self.physio_camp)):
            st, b = self._intake(who, ws, conn, camp)
            st, acc = self._accept(who, ws, b["treatment"])
            path = f"/api/v1/workspaces/{ws}/creative-requests/{b['request']['id']}/versions/{b['request']['version_id']}"
            st, m = call(self.app, "POST", path + "/compile-mapping", tok=self.tok[who], body={"treatment": b["treatment"]})
            self.assertEqual((st, m.get("error")), (409, "treatment_not_accepted"), "draft treatments never compile")
            st, m = call(self.app, "POST", path + "/compile-mapping", tok=self.tok[who],
                         body={"treatment": acc["treatment"]})
            self.assertEqual(st, 200, m)
            self.assertEqual(m["lane"], "studio-offering")
            self.assertIn(m["offering_kind"], ("service", "service_group"))
            self.assertIsNone(m["legacy_inputs"]["product_spec"])
            prompt = m["provider_prompt"]
            text = json.dumps(prompt).lower()
            for banned in ("geometry", "preserve exact product", "proportions", "product_spec_id", "cad"):
                self.assertNotIn(banned, text, banned)
            self.assertIn(camp.offering["service_facts"][0]["value"].lower(), text)
            self.assertIn("leave clean, uncluttered space", text)
            st, m2 = call(self.app, "POST", path + "/compile-mapping", tok=self.tok[who],
                          body={"treatment": acc["treatment"]})
            self.assertEqual(m["mapping_sha256"], m2["mapping_sha256"], "mapping is deterministic")
        # SQL forbids a service request carrying a ProductSpec, even if a client skipped validation
        st, rq = call(self.app, "GET", path, tok=self.tok["producer_b"])
        doc = copy.deepcopy(rq["request"])
        doc.update({"id": str(uuid.uuid4()), "version_id": str(uuid.uuid4()), "revision": 1,
                    "created_at": documents.now_utc()})
        doc["offering"]["physical_product_spec"] = self.kitchen_camp.offering["physical_product_spec"]
        doc["sha256"] = artifact_sha256(doc)
        conn, cur = self.sql("cs_api", self.users["producer_b"])
        try:
            self.assertSqlState("23514", lambda: cur.execute(
                "select * from public.cs_save_creative_request(%s,%s,%s,%s,null,gen_random_uuid(),repeat('a',64))",
                (self.ws_b, json.dumps(doc), documents.canonical_for_db(doc), documents.request_semantic(doc))))
        finally:
            conn.close()
        # contrast: the physical lane goes through the unchanged legacy compiler and keeps its geometry
        ws, conn = self.ws_k, self.conn_k
        st, b = self._intake("owner_a", ws, conn, self.kitchen_camp)
        st, acc = self._accept("owner_a", ws, b["treatment"])
        from service.requests.compile_mapping import compile_inputs
        with self.pg.connect() as c:
            cur = c.cursor()
            cur.execute("select payload from public.cs_creative_request_versions where version_id = %s",
                        (b["request"]["version_id"],))
            req = cur.fetchone()[0]
            cur.execute("select payload from public.cs_treatment_versions where version_id = %s",
                        (acc["treatment"]["version_id"],))
            tr = cur.fetchone()[0]
            cur.execute("select payload from public.cs_source_imports where workspace_id = %s and version_id = %s",
                        (ws, req["brand_package"]["version_id"]))
            pkg = cur.fetchone()[0]
        out = compile_inputs(req, tr, pkg, product_spec=self.kitchen_camp.product_spec)
        self.assertEqual(out["lane"], "studio-physical")
        self.assertIn("Product geometry:", out["provider_prompt"]["positive"])
        self.assertEqual(out["provider_prompt"]["metadata"]["product_spec_id"], "everyday-skillet")

    # -- 12 operations ----------------------------------------------------------------------
    def test_12_operation_projection_and_owner_rules(self):
        op = self.pkg_a["op"]
        contracts.validate(op, "cs-operation-v1")
        self.assertEqual((op["kind"], op["admission"], op["cost"]), ("brand_package_import", None, None))
        with self.pg.connect() as c:
            cur = c.cursor()
            self.assertSqlState("23514", lambda: cur.execute(
                "insert into public.cs_work_items (workspace_id, environment_id, run_id, node_id, operation_id, "
                "operation_step_key, stage, dedupe_key) select workspace_id, 'test', null, null, null, null, "
                "'import_brand_package', 'no-owner' from public.cs_operations limit 1"), "cs_work_items_one_owner")
            self.assertSqlState("CS409", lambda: cur.execute(
                "update public.cs_operations set input_refs = '[]'::jsonb where id = %s", (op["id"],)))
            self.assertSqlState("CS409", lambda: cur.execute(
                "update public.cs_operations set state = 'running' where id = %s", (op["id"],)),
                "terminal operation")
            self.assertSqlState("CS409", lambda: cur.execute(
                "update public.cs_operation_events set kind = 'x' where operation_id = %s", (op["id"],)))

    # -- 13 migration re-run ----------------------------------------------------------------
    def test_13_migrations_rerun_in_order_are_idempotent(self):
        snapshot = ("select (select count(*) from pg_policies where tablename like 'cs\\_%%' or schemaname='cs_private'), "
                    "(select count(*) from public.cs_source_imports), (select count(*) from public.cs_asset_versions), "
                    "(select count(*) from pg_constraint where conname like 'cs\\_%%'), "
                    "(select string_agg(p.proname || ':' || md5(p.prosrc), ',' order by p.proname, md5(p.prosrc)) "
                    " from pg_proc p join pg_namespace n on n.oid = p.pronamespace "
                    " where n.nspname = 'public' and p.proname in ('cs_claim_work','cs_finish_work','cs_reschedule_work'))")
        with self.pg.connect() as c:
            cur = c.cursor()
            cur.execute(snapshot)
            before = cur.fetchone()
            for _ in range(2):
                cur.execute(B2_MIGRATION.read_text(encoding="utf-8"))
            cur.execute(snapshot)
            self.assertEqual(cur.fetchone(), before, "B2 alone twice")
            for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
                cur.execute(path.read_text(encoding="utf-8"))
            cur.execute(snapshot)
            self.assertEqual(cur.fetchone(), before, "all migrations in filename order")
        st, body = call(self.app, "GET", f"/api/v1/workspaces/{self.ws_a}/brand-packages/"
                        f"{self.pkg_a['ref']['version_id']}", tok=self.tok["viewer_a"])
        self.assertEqual(st, 200, "grants and policies survive re-run")
