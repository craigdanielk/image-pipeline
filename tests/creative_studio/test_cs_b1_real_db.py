"""CS-B1 / NS-10803 real PostgreSQL 17 acceptance suite.

Runs the actual migration files (all of supabase/migrations, filename order) on
a private throwaway PG17 cluster, the real service code (service/, api/) over
WSGI, and a Storage stand-in whose access is decided by the migration's own
storage.objects RLS. No network beyond loopback, no provider, no production.

Accept (ENG-04h): unauthenticated refused; member of workspace A cannot read
B's jobs/assets; signed URL expiry; records survive restart (process kill +
re-read). Plus ENG-03 §5: anon denied, authenticated direct write denied,
spoofed actor ignored, idempotent migration re-run.

Run:  python3 -m pytest tests/creative_studio/test_cs_b1_real_db.py -v
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import jwt
import psycopg2

from service.api import create_app
from service.auth import TokenVerifier
from service.config import ConfigError, Settings, load
from service.errors import ServiceError
from service.storage import StorageSigner
from service import worker as cs_worker
from tests.creative_studio.fake_storage import FakeStorage
from tests.creative_studio.pg_harness import REPO_ROOT, ThrowawayCluster, claims_json

JWT_SECRET = "test-only-hs256-secret-not-a-real-credential-000"
ISSUER = "http://127.0.0.1/auth/v1"
PNG = b"\x89PNG\r\n\x1a\n" + b"example-generic-image-bytes" * 8


def token(sub: str, *, role="authenticated", aud="authenticated", iss=ISSUER, exp_in=600, secret=JWT_SECRET,
          alg="HS256") -> str:
    return jwt.encode({"sub": sub, "role": role, "aud": aud, "iss": iss, "exp": int(time.time()) + exp_in},
                      secret, algorithm=alg)


def call(app, method, path, *, tok=None, body=None, idem=None, query=""):
    raw = json.dumps(body).encode() if body is not None else b""
    environ = {"REQUEST_METHOD": method, "PATH_INFO": path, "QUERY_STRING": query, "wsgi.input": io.BytesIO(raw),
               "CONTENT_LENGTH": str(len(raw)), "SERVER_NAME": "test", "SERVER_PORT": "80",
               "wsgi.url_scheme": "http"}
    if tok:
        environ["HTTP_AUTHORIZATION"] = f"Bearer {tok}"
    if idem:
        environ["HTTP_IDEMPOTENCY_KEY"] = idem
    captured = {}

    def start_response(status, headers):
        captured["status"] = int(status.split()[0])
    out = b"".join(app(environ, start_response))
    return captured["status"], json.loads(out)


def http(method, url, *, tok=None, data=None, headers=None):
    req = urllib.request.Request(url, method=method, data=data, headers=dict(headers or {}))
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


class CsB1RealDatabase(unittest.TestCase):
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
            for name in ("owner_a", "owner_b", "producer_a", "reviewer_a", "viewer_a", "outsider", "machine"):
                uid = str(uuid.uuid4())
                cur.execute("insert into auth.users (id, email) values (%s, %s)", (uid, f"{name}@example-studio.test"))
                cls.users[name] = uid
            # Storage worker identity for the 'test' environment (CS-B5 provisions this in hosting)
            cur.execute("insert into cs_private.cs_runtime_principals (user_id, environment_id) values (%s, 'test')",
                        (cls.users["machine"],))
        cls.tok = {k: token(v) for k, v in cls.users.items()}
        cls.acct_a, cls.ws_a = cls._bootstrap("owner_a", "Example Home Services Co")
        cls.acct_b, cls.ws_b = cls._bootstrap("owner_b", "Example Bakery Ltd")
        for who, role in (("producer_a", "producer"), ("reviewer_a", "reviewer"), ("viewer_a", "viewer")):
            st, _ = call(cls.app, "PUT", f"/api/v1/workspaces/{cls.ws_a}/members/{cls.users[who]}",
                         tok=cls.tok["owner_a"], body={"role": role}, idem=str(uuid.uuid4()))
            assert st == 200, st
        cls.run_a = cls._seed_run(cls.ws_a, "Ra")
        cls.run_b = cls._seed_run(cls.ws_b, "Rb")
        cls.asset_b = cls._seed_upload_and_finalize("owner_b", cls.ws_b)

    @classmethod
    def tearDownClass(cls):
        cls.storage.stop()
        cls.pg.stop()

    # -- helpers ---------------------------------------------------------------
    @classmethod
    def _settings(cls, **over) -> Settings:
        base = dict(api_database_url=cls.pg.dsn(user="cs_api"), worker_database_url=cls.pg.dsn(user="cs_worker"),
                    environment_id="test", release_id="test-release", jwt_issuer=ISSUER,
                    jwt_audience="authenticated", jwt_secret=JWT_SECRET, jwks_url=None,
                    storage_url=cls.storage.base_url, storage_anon_key="anon-public-test-key",
                    signed_download_seconds=300, download_grant_seconds=60, worker_secret="worker-test-secret",
                    runtime_principal="cs-worker:test")
        base.update(over)
        return Settings(**base)

    @classmethod
    def _bootstrap(cls, who, name):
        st, acct = call(cls.app, "POST", "/api/v1/accounts", tok=cls.tok[who], body={"name": name},
                        idem=str(uuid.uuid4()))
        assert st == 201, (st, acct)
        st, ws = call(cls.app, "POST", f"/api/v1/accounts/{acct['account_id']}/workspaces", tok=cls.tok[who],
                      body={"name": f"{name} studio"}, idem=str(uuid.uuid4()))
        assert st == 201, (st, ws)
        return acct["account_id"], ws["workspace_id"]

    @classmethod
    def _seed_run(cls, ws, tag, stage="compose_image", node="n1"):
        """Stand-in for CS-B3 admission: one run, one node, one due work item."""
        with cls.pg.connect() as conn:
            cur = conn.cursor()
            cur.execute("insert into public.cs_runs (workspace_id, environment_id, release_id) "
                        "values (%s, 'test', 'test-release') returning id", (ws,))
            run_id = str(cur.fetchone()[0])
            cur.execute("insert into public.cs_run_nodes (workspace_id, run_id, node_id, state) "
                        "values (%s, %s, %s, 'queued')", (ws, run_id, node))
            cur.execute("insert into public.cs_work_items (workspace_id, environment_id, run_id, node_id, stage, "
                        "dedupe_key, max_attempts) values (%s, 'test', %s, %s, %s, %s, 2) returning id",
                        (ws, run_id, node, stage, f"{tag}-{uuid.uuid4()}"))
            return {"run_id": run_id, "work_item_id": str(cur.fetchone()[0]), "node_id": node}

    @classmethod
    def _seed_upload_and_finalize(cls, who, ws, data=PNG):
        sha = hashlib.sha256(data).hexdigest()
        st, up = call(cls.app, "POST", f"/api/v1/workspaces/{ws}/uploads", tok=cls.tok[who], idem=str(uuid.uuid4()),
                      body={"label": "Hero photo", "mime_type": "image/png", "size_bytes": len(data), "sha256": sha})
        assert st == 201, (st, up)
        st, _ = http("POST", f"{cls.storage.base_url}/object/creative-studio/{up['upload_path']}",
                     tok=cls.tok[who], data=data, headers={"Content-Type": "image/png"})
        assert st == 200, st
        # finalize stage: read stored bytes as the runtime principal, hash, then record
        st, stored = http("GET", f"{cls.storage.base_url}/object/authenticated/creative-studio/{up['upload_path']}",
                          tok=cls.tok["machine"])
        assert st == 200, st
        fin = cs_worker.finalize_upload(cls.settings, up["upload_id"], stored, "image/png", 64, 64)
        return {"asset_id": str(fin["asset_id"]), "version_id": str(fin["asset_version_id"]), "sha256": sha,
                "storage_key": fin["storage_key"], "upload": up}

    def sql_as(self, role, claims_user=None, claims_role="authenticated"):
        """Connection whose session role is `role` with optional request.jwt.claims."""
        conn = psycopg2.connect(self.pg.dsn(user="cs_api" if role == "cs_api" else
                                            "cs_worker" if role == "cs_worker" else None))
        cur = conn.cursor()
        if role not in ("cs_api", "cs_worker"):
            cur.execute(f"set role {role}")
        if claims_user:
            cur.execute("select set_config('request.jwt.claims', %s, false)", (claims_json(claims_user, claims_role),))
        # commit so SET ROLE / claims are session-level and survive later rollbacks
        conn.commit()
        return conn, cur

    def assertSqlState(self, state, fn):
        with self.assertRaises(psycopg2.Error) as ctx:
            fn()
        self.assertEqual(ctx.exception.pgcode, state, ctx.exception.pgerror)

    # -- 01 restricted runtime roles ------------------------------------------
    def test_01_runtime_roles_are_restricted(self):
        with self.pg.connect() as conn:
            cur = conn.cursor()
            cur.execute("select rolname, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, rolinherit "
                        "from pg_roles where rolname in ('cs_api','cs_worker') order by 1")
            self.assertEqual(cur.fetchall(), [("cs_api", False, False, False, False, False),
                                              ("cs_worker", False, False, False, False, False)])
            cur.execute("select count(*) from pg_auth_members am join pg_roles r on r.oid = am.member "
                        "where r.rolname in ('cs_api','cs_worker')")
            self.assertEqual(cur.fetchone()[0], 0)
            cur.execute("select count(*) from pg_class c join pg_roles r on r.oid = c.relowner "
                        "where r.rolname in ('cs_api','cs_worker')")
            self.assertEqual(cur.fetchone()[0], 0, "runtime roles must own no relations")
            cur.execute("select c.relname from pg_class c join pg_namespace n on n.oid = c.relnamespace "
                        "where c.relkind = 'r' and (c.relname like 'cs\\_%%' ) and n.nspname in ('public','cs_private') "
                        "and not (c.relrowsecurity and c.relforcerowsecurity)")
            self.assertEqual(cur.fetchall(), [], "every cs_* table has RLS enabled and forced")
            cur.execute("select count(*) from pg_proc p join pg_namespace n on n.oid = p.pronamespace "
                        "where p.prosecdef and (n.nspname = 'cs_private' or p.proname like 'cs\\_%%') "
                        "and not exists (select 1 from unnest(coalesce(p.proconfig, '{}')) c where c like 'search_path=%%')")
            self.assertEqual(cur.fetchone()[0], 0, "SECURITY DEFINER functions pin search_path")
        for role in ("cs_api", "cs_worker"):
            conn, cur = self.sql_as(role)
            try:
                for ddl in ("create table public.cs_x (id int)", "create table cs_private.cs_x (id int)"):
                    self.assertSqlState("42501", lambda: cur.execute(ddl))
                    conn.rollback()
            finally:
                conn.close()

    # -- 02/03 unauthenticated refused ----------------------------------------
    def test_02_unauthenticated_refused_http(self):
        path = f"/api/v1/workspaces/{self.ws_a}/runs"
        owner = self.users["owner_a"]
        cases = {
            "no header": None,
            "garbage": "not-a-jwt",
            "wrong secret": token(owner, secret="some-other-secret-value-0000000000000"),
            "expired": token(owner, exp_in=-30),
            "wrong audience": token(owner, aud="other"),
            "wrong issuer": token(owner, iss="https://elsewhere.example.test"),
            "anon role": token(owner, role="anon"),
            "service role token": token(owner, role="service_role"),
            "unsigned alg none": jwt.encode({"sub": owner, "role": "authenticated", "aud": "authenticated",
                                             "iss": ISSUER, "exp": int(time.time()) + 600}, None, algorithm="none"),
        }
        for label, tok in cases.items():
            st, body = call(self.app, "GET", path, tok=tok)
            self.assertEqual((st, body), (401, {"error": "authentication_required"}), label)
        st, _ = call(self.app, "GET", path, tok=self.tok["owner_a"])
        self.assertEqual(st, 200)

    def test_03_unauthenticated_and_direct_access_refused_sql(self):
        # cs_api session with no verified actor: every procedure refuses
        conn, cur = self.sql_as("cs_api")
        try:
            self.assertSqlState("CS401", lambda: cur.execute(
                "select * from public.cs_create_account('x', gen_random_uuid(), repeat('a', 64))"))
            conn.rollback()
            cur.execute("select count(*) from public.cs_runs")
            self.assertEqual(cur.fetchone()[0], 0, "no actor -> RLS shows nothing")
            self.assertSqlState("42501", lambda: cur.execute(
                "insert into public.cs_runs (workspace_id, environment_id, release_id) values (%s,'test','x')",
                (self.ws_a,)))
            conn.rollback()
        finally:
            conn.close()
        # anon and authenticated (browser roles) have no cs_* table or procedure access at all
        for role in ("anon", "authenticated"):
            conn, cur = self.sql_as(role, claims_user=self.users["owner_a"])
            try:
                for stmt in ("select * from public.cs_runs", "select * from public.cs_assets",
                             "select * from public.cs_workspace_members", "select * from cs_private.cs_download_grants",
                             f"insert into public.cs_runs (workspace_id, environment_id, release_id) "
                             f"values ('{self.ws_a}','test','x')",
                             "select * from public.cs_create_account('x', gen_random_uuid(), repeat('a', 64))",
                             "select * from public.cs_claim_work('test','w',array['compose_image']::cs_work_stage[],1)"):
                    self.assertSqlState("42501", lambda: cur.execute(stmt))
                    conn.rollback()
            finally:
                conn.close()
        # anon sees no Storage objects in the private bucket
        conn, cur = self.sql_as("anon")
        try:
            cur.execute("select count(*) from storage.objects where bucket_id = 'creative-studio'")
            self.assertEqual(cur.fetchone()[0], 0)
        finally:
            conn.close()

    # -- 04/05 workspace isolation ---------------------------------------------
    def test_04_member_of_a_cannot_read_b_http(self):
        b = self.asset_b
        for who in ("owner_a", "producer_a", "reviewer_a", "viewer_a", "outsider"):
            t = self.tok[who]
            for method, path, body in (
                ("GET", f"/api/v1/workspaces/{self.ws_b}/runs", None),
                ("GET", f"/api/v1/workspaces/{self.ws_b}/runs/{self.run_b['run_id']}", None),
                ("GET", f"/api/v1/workspaces/{self.ws_a}/runs/{self.run_b['run_id']}", None),   # B's run via A's scope
                ("GET", f"/api/v1/workspaces/{self.ws_b}/assets", None),
                ("POST", f"/api/v1/workspaces/{self.ws_b}/assets/{b['asset_id']}/versions/{b['version_id']}/download",
                 {"sha256": b["sha256"]}),
            ):
                st, resp = call(self.app, method, path, tok=t, body=body)
                self.assertEqual((st, resp), (404, {"error": "resource_unavailable"}), (who, method, path))
            # B's asset ids under A's scope: download-capable A roles get "unavailable"; a viewer is refused
            # on role before any lookup. Neither reveals whether B's asset exists.
            st, resp = call(self.app, "POST", f"/api/v1/workspaces/{self.ws_a}/assets/{b['asset_id']}/versions/"
                            f"{b['version_id']}/download", tok=t, body={"sha256": b["sha256"]})
            expected = {"viewer_a": (403, {"error": "capability_denied"}),
                        "outsider": (404, {"error": "resource_unavailable"})}.get(
                who, (404, {"error": "resource_unavailable"}))
            self.assertEqual((st, resp), expected, (who, "A-scope download of B asset"))
            st, resp = call(self.app, "GET", "/api/v1/me/workspaces", tok=t)
            self.assertNotIn(self.ws_b, [w["id"] for w in resp["workspaces"]], who)
        # positive controls: B's owner reads B; A's members read A
        st, resp = call(self.app, "GET", f"/api/v1/workspaces/{self.ws_b}/runs/{self.run_b['run_id']}",
                        tok=self.tok["owner_b"])
        self.assertEqual((st, resp["run"]["id"]), (200, self.run_b["run_id"]))
        st, resp = call(self.app, "GET", f"/api/v1/workspaces/{self.ws_b}/assets", tok=self.tok["owner_b"])
        self.assertEqual([a["id"] for a in resp["assets"]], [b["asset_id"]])
        st, resp = call(self.app, "GET", f"/api/v1/workspaces/{self.ws_a}/runs", tok=self.tok["viewer_a"])
        self.assertIn(self.run_a["run_id"], [r["id"] for r in resp["runs"]])

    def test_05_member_of_a_cannot_read_or_act_on_b_sql(self):
        b = self.asset_b
        conn, cur = self.sql_as("cs_api", claims_user=self.users["producer_a"])
        try:
            for table in ("cs_runs", "cs_run_nodes", "cs_run_events", "cs_assets", "cs_asset_versions",
                          "cs_workspace_members"):
                cur.execute(f"select count(*) from public.{table} where workspace_id = %s", (self.ws_b,))
                self.assertEqual(cur.fetchone()[0], 0, table)
            cur.execute("select count(*) from public.cs_workspaces where id = %s", (self.ws_b,))
            self.assertEqual(cur.fetchone()[0], 0)
            self.assertSqlState("CS404", lambda: cur.execute(
                "select * from public.cs_create_download_grant(%s,%s,%s,%s,30)",
                (self.ws_b, b["asset_id"], b["version_id"], b["sha256"])))
            conn.rollback()
            self.assertSqlState("CS404", lambda: cur.execute(
                "select * from public.cs_create_upload_intent(%s,'test','x','image/png',10,repeat('a',64),"
                "gen_random_uuid(),repeat('b',64))", (self.ws_b,)))
            conn.rollback()
            self.assertSqlState("CS404", lambda: cur.execute(
                "select * from public.cs_set_workspace_member(%s,%s,'producer',gen_random_uuid(),repeat('c',64))",
                (self.ws_b, self.users["producer_a"])))
            conn.rollback()
            # direct insert of a grant for B's object is refused by RLS
            self.assertSqlState("42501", lambda: cur.execute(
                "insert into cs_private.cs_download_grants (workspace_id, actor_id, asset_version_id, "
                "exact_storage_key, expires_at) values (%s,%s,%s,%s, clock_timestamp() + interval '30 seconds')",
                (self.ws_b, self.users["producer_a"], b["version_id"], b["storage_key"])))
            conn.rollback()
        finally:
            conn.close()
        # Storage RLS: A's members can neither read B's object nor write into B's prefix
        conn, cur = self.sql_as("authenticated", claims_user=self.users["producer_a"])
        try:
            cur.execute("select count(*) from storage.objects where name = %s", (b["storage_key"],))
            self.assertEqual(cur.fetchone()[0], 0)
            forged = b["storage_key"].replace(b["version_id"], str(uuid.uuid4()))
            self.assertSqlState("42501", lambda: cur.execute(
                "insert into storage.objects (bucket_id, name) values ('creative-studio', %s)", (forged,)))
            conn.rollback()
        finally:
            conn.close()

    def test_06_roles_inside_workspace_and_revocation(self):
        sha = hashlib.sha256(b"x").hexdigest()
        body = {"label": "x", "mime_type": "image/png", "size_bytes": 1, "sha256": sha}
        for who in ("viewer_a", "reviewer_a"):
            st, resp = call(self.app, "POST", f"/api/v1/workspaces/{self.ws_a}/uploads", tok=self.tok[who],
                            body=body, idem=str(uuid.uuid4()))
            self.assertEqual((st, resp), (403, {"error": "capability_denied"}), who)
        st, _ = call(self.app, "POST", f"/api/v1/workspaces/{self.ws_a}/uploads", tok=self.tok["producer_a"],
                     body=body, idem=str(uuid.uuid4()))
        self.assertEqual(st, 201)
        asset = self._seed_upload_and_finalize("producer_a", self.ws_a, data=PNG + b"-roles")
        dl = f"/api/v1/workspaces/{self.ws_a}/assets/{asset['asset_id']}/versions/{asset['version_id']}/download"
        st, resp = call(self.app, "POST", dl, tok=self.tok["viewer_a"], body={"sha256": asset["sha256"]})
        self.assertEqual((st, resp), (403, {"error": "capability_denied"}))
        st, resp = call(self.app, "POST", dl, tok=self.tok["reviewer_a"], body={"sha256": asset["sha256"]})
        self.assertEqual(st, 200, resp)
        st, resp = call(self.app, "POST", dl, tok=self.tok["reviewer_a"], body={"sha256": "0" * 64})
        self.assertEqual((st, resp), (422, {"error": "asset_hash_mismatch"}))
        # non-manager cannot change membership
        st, resp = call(self.app, "PUT", f"/api/v1/workspaces/{self.ws_a}/members/{self.users['outsider']}",
                        tok=self.tok["producer_a"], body={"role": "producer"}, idem=str(uuid.uuid4()))
        self.assertEqual((st, resp), (403, {"error": "capability_denied"}))
        # revocation takes effect on the very next request
        temp = str(uuid.uuid4())
        with self.pg.connect() as conn:
            conn.cursor().execute("insert into auth.users (id) values (%s)", (temp,))
        call(self.app, "PUT", f"/api/v1/workspaces/{self.ws_a}/members/{temp}", tok=self.tok["owner_a"],
             body={"role": "viewer"}, idem=str(uuid.uuid4()))
        st, _ = call(self.app, "GET", f"/api/v1/workspaces/{self.ws_a}/runs", tok=token(temp))
        self.assertEqual(st, 200)
        st, resp = call(self.app, "DELETE", f"/api/v1/workspaces/{self.ws_a}/members/{temp}", tok=self.tok["owner_a"])
        self.assertEqual((st, resp), (200, {"revoked": True}))
        st, resp = call(self.app, "GET", f"/api/v1/workspaces/{self.ws_a}/runs", tok=token(temp))
        self.assertEqual((st, resp), (404, {"error": "resource_unavailable"}))

    # -- 07 spoofed actor / local path selectors ------------------------------
    def test_07_spoofed_actor_and_local_paths_ignored(self):
        victim = self.users["owner_b"]
        st, acct = call(self.app, "POST", "/api/v1/accounts", tok=self.tok["outsider"], idem=str(uuid.uuid4()),
                        body={"name": "Example Plumbing Co", "owner_id": victim, "actor_id": victim, "p_actor": victim})
        self.assertEqual(st, 201)
        with self.pg.connect() as conn:
            cur = conn.cursor()
            cur.execute("select owner_id::text from public.cs_accounts where id = %s", (acct["account_id"],))
            self.assertEqual(cur.fetchone()[0], self.users["outsider"])
            cur.execute("select actor_id::text from public.cs_audit_events where account_id = %s", (acct["account_id"],))
            self.assertEqual([r[0] for r in cur.fetchall()], [self.users["outsider"]])
        # A service_role/anon claim in the session never becomes an actor
        for claims_role in ("service_role", "anon"):
            conn, cur = self.sql_as("cs_api", claims_user=self.users["owner_a"], claims_role=claims_role)
            try:
                self.assertSqlState("CS401", lambda: cur.execute(
                    "select * from public.cs_create_account('x', gen_random_uuid(), repeat('a', 64))"))
            finally:
                conn.close()
        # No filesystem / tenant / jobs_root selector is reachable
        for query in ("jobs_root=/tmp", "path=/etc/passwd", "tenant=default"):
            st, resp = call(self.app, "GET", f"/api/v1/workspaces/{self.ws_a}/runs", tok=self.tok["owner_a"],
                            query=query)
            self.assertEqual(st, 400, query)
            self.assertEqual(resp["error"], "unsupported_parameter")
        for path in ("/media", "/api/tenants", "/api/jobs", "/api/v1/jobs_root", "/api/execute"):
            st, _ = call(self.app, "GET", path, tok=self.tok["owner_a"])
            self.assertEqual(st, 404, path)

    # -- 08 private storage + signed URL expiry -------------------------------
    def test_08_signed_url_issued_then_expires(self):
        app = create_app(self._settings(signed_download_seconds=2))
        a = self._seed_upload_and_finalize("producer_a", self.ws_a, data=PNG + b"-expiry")
        st, resp = call(app, "POST",
                        f"/api/v1/workspaces/{self.ws_a}/assets/{a['asset_id']}/versions/{a['version_id']}/download",
                        tok=self.tok["owner_a"], body={"sha256": a["sha256"]})
        self.assertEqual(st, 200, resp)
        self.assertEqual(set(resp), {"url", "expires_in", "sha256", "mime_type", "size_bytes"})
        self.assertTrue(resp["url"].startswith(self.storage.base_url + "/object/sign/creative-studio/"))
        self.assertEqual(resp["expires_in"], 2)
        st, got = http("GET", resp["url"])
        self.assertEqual((st, hashlib.sha256(got).hexdigest()), (200, a["sha256"]))
        time.sleep(3.1)
        st, got = http("GET", resp["url"])
        self.assertEqual(st, 400)
        self.assertIn(b"jwt expired", got)

    def test_09_download_grant_expires_and_is_bounded(self):
        a = self._seed_upload_and_finalize("producer_a", self.ws_a, data=PNG + b"-grant")
        conn, cur = self.sql_as("cs_api", claims_user=self.users["reviewer_a"])
        try:
            self.assertSqlState("CS422", lambda: cur.execute(
                "select * from public.cs_create_download_grant(%s,%s,%s,%s,61)",
                (self.ws_a, a["asset_id"], a["version_id"], a["sha256"])))
            conn.rollback()
            cur.execute("select expires_at - clock_timestamp() < interval '2 seconds' "
                        "from public.cs_create_download_grant(%s,%s,%s,%s,1)",
                        (self.ws_a, a["asset_id"], a["version_id"], a["sha256"]))
            self.assertTrue(cur.fetchone()[0])
            conn.commit()
        finally:
            conn.close()
        with self.pg.connect() as conn:   # table constraint, independent of the procedure
            self.assertSqlState("23514", lambda: conn.cursor().execute(
                "insert into cs_private.cs_download_grants (workspace_id, actor_id, asset_version_id, "
                "exact_storage_key, expires_at) values (%s,%s,%s,%s, clock_timestamp() + interval '61 seconds')",
                (self.ws_a, self.users["reviewer_a"], a["version_id"], a["storage_key"])))
        visible = "select count(*) from storage.objects where bucket_id='creative-studio' and name=%s"
        conn, cur = self.sql_as("authenticated", claims_user=self.users["reviewer_a"])
        try:
            cur.execute(visible, (a["storage_key"],))
            self.assertEqual(cur.fetchone()[0], 1, "live grant -> exact object visible to its holder")
            conn.commit()
            time.sleep(1.5)
            cur.execute(visible, (a["storage_key"],))
            self.assertEqual(cur.fetchone()[0], 0, "expired grant -> object invisible")
            cur.execute("select name from storage.objects where bucket_id='creative-studio'")
            seen = {r[0] for r in cur.fetchall()}
        finally:
            conn.close()
        with self.pg.connect() as su:
            c = su.cursor()
            c.execute("select exact_storage_key from cs_private.cs_download_grants "
                      "where actor_id = %s and expires_at > clock_timestamp()", (self.users["reviewer_a"],))
            live = {r[0] for r in c.fetchall()}
            c.execute("select count(*) from storage.objects where bucket_id='creative-studio'")
            total = c.fetchone()[0]
        self.assertLessEqual(seen, live, "only exact objects with a live grant are visible (no prefix access)")
        self.assertLess(len(seen), total)
        # another user cannot ride on someone else's grant
        conn, cur = self.sql_as("cs_api", claims_user=self.users["reviewer_a"])
        cur.execute("select 1 from public.cs_create_download_grant(%s,%s,%s,%s,30)",
                    (self.ws_a, a["asset_id"], a["version_id"], a["sha256"]))
        conn.commit()
        conn.close()
        conn, cur = self.sql_as("authenticated", claims_user=self.users["producer_a"])
        try:
            cur.execute(visible, (a["storage_key"],))
            self.assertEqual(cur.fetchone()[0], 0)
        finally:
            conn.close()
        # signing after the grant is gone is refused by Storage RLS -> typed refusal
        signer = StorageSigner(self.settings)
        with self.assertRaises(ServiceError) as ctx:
            signer.sign_download(self.tok["viewer_a"], a["storage_key"], 30)
        self.assertEqual((ctx.exception.status, ctx.exception.code), (403, "download_not_permitted"))
        with self.assertRaises(ServiceError):
            signer.sign_download(self.tok["reviewer_a"], a["storage_key"], 301)

    def test_10_upload_is_exact_key_and_never_upserts(self):
        data = PNG + b"-upload"
        sha = hashlib.sha256(data).hexdigest()
        st, up = call(self.app, "POST", f"/api/v1/workspaces/{self.ws_a}/uploads", tok=self.tok["producer_a"],
                      idem=str(uuid.uuid4()),
                      body={"label": "x", "mime_type": "image/png", "size_bytes": len(data), "sha256": sha})
        self.assertEqual(st, 201)
        self.assertNotIn("http", json.dumps(up))
        other = up["upload_path"].rsplit("/", 1)[0] + "/" + "f" * 64 + ".png"
        st, _ = http("POST", f"{self.storage.base_url}/object/creative-studio/{other}", tok=self.tok["producer_a"],
                     data=data)
        self.assertEqual(st, 403, "only the exact intent key is writable")
        st, _ = http("POST", f"{self.storage.base_url}/object/creative-studio/{up['upload_path']}",
                     tok=self.tok["owner_b"], data=data)
        self.assertEqual(st, 403, "intent belongs to its creator only")
        st, _ = http("POST", f"{self.storage.base_url}/object/creative-studio/{up['upload_path']}",
                     tok=self.tok["producer_a"], data=data)
        self.assertEqual(st, 200)
        st, _ = http("POST", f"{self.storage.base_url}/object/creative-studio/{up['upload_path']}",
                     tok=self.tok["producer_a"], data=b"replacement")
        self.assertEqual(st, 409, "no overwrite")
        with self.assertRaises(ServiceError) as ctx:
            cs_worker.finalize_upload(self.settings, up["upload_id"], b"tampered bytes", "image/png")
        self.assertEqual(ctx.exception.code, "asset_hash_mismatch")
        first = cs_worker.finalize_upload(self.settings, up["upload_id"], data, "image/png")
        again = cs_worker.finalize_upload(self.settings, up["upload_id"], data, "image/png")
        self.assertEqual((first["replayed"], again["replayed"]), (False, True))
        self.assertEqual(first["asset_version_id"], again["asset_version_id"])
        # idempotency: same key + same body replays; same key + different body conflicts
        key = str(uuid.uuid4())
        body = {"label": "y", "mime_type": "image/png", "size_bytes": 5, "sha256": sha}
        st1, r1 = call(self.app, "POST", f"/api/v1/workspaces/{self.ws_a}/uploads", tok=self.tok["producer_a"],
                       idem=key, body=body)
        st2, r2 = call(self.app, "POST", f"/api/v1/workspaces/{self.ws_a}/uploads", tok=self.tok["producer_a"],
                       idem=key, body=body)
        st3, r3 = call(self.app, "POST", f"/api/v1/workspaces/{self.ws_a}/uploads", tok=self.tok["producer_a"],
                       idem=key, body={**body, "label": "z"})
        self.assertEqual((st1, st2, r2["upload_id"], r2["replayed"]), (201, 200, r1["upload_id"], True))
        self.assertEqual((st3, r3), (409, {"error": "idempotency_conflict"}))
        st, r = call(self.app, "POST", f"/api/v1/workspaces/{self.ws_a}/uploads", tok=self.tok["producer_a"], body=body)
        self.assertEqual((st, r), (422, {"error": "idempotency_key_required"}))

    # -- 11 scheduling primitive ------------------------------------------------
    def test_11_scheduling_lease_fence_and_bounded_retry(self):
        seed = self._seed_run(self.ws_a, "sched", stage="render_thumbnail")
        stages = "array['render_thumbnail']::cs_work_stage[]"
        conn, cur = self.sql_as("cs_worker")
        try:
            cur.execute(f"select work_item_id::text, lease_token::text, fencing_token from "
                        f"public.cs_claim_work('test','w1',{stages},1)")
            item, tok1, fence1 = cur.fetchone()
            conn.commit()
            self.assertEqual((item, fence1), (seed["work_item_id"], 1))
            cur.execute(f"select count(*) from public.cs_claim_work('test','w2',{stages},1)")
            self.assertEqual(cur.fetchone()[0], 0, "leased item is not double-claimed")
            conn.commit()
            with self.pg.connect() as su:   # simulate lease expiry (worker died)
                su.cursor().execute("update public.cs_work_items set lease_until = now() - interval '1 second' "
                                    "where id = %s", (item,))
            cur.execute(f"select lease_token::text, fencing_token from public.cs_claim_work('test','w2',{stages},1)")
            tok2, fence2 = cur.fetchone()
            conn.commit()
            self.assertEqual(fence2, 2)
            self.assertSqlState("CS409", lambda: cur.execute(
                "select public.cs_finish_work(%s,%s,%s,'thumbnail',null,'[]',repeat('a',64),'r')",
                (item, tok1, fence1)))
            conn.rollback()
            cur.execute("select public.cs_reschedule_work(%s,%s,%s,'transient_failure',1)::text", (item, tok2, fence2))
            self.assertEqual(cur.fetchone()[0], "dead", "attempt 2 of max 2 -> dead, not endless retry")
            conn.commit()
            cur.execute("select state::text from public.cs_run_nodes where run_id = %s", (seed["run_id"],))
            self.assertEqual(cur.fetchone()[0], "failed")
            cur.execute("select state::text from public.cs_runs where id = %s", (seed["run_id"],))
            self.assertNotEqual(cur.fetchone()[0], "completed")
            cur.execute("select kind from public.cs_run_events where run_id = %s order by sequence", (seed["run_id"],))
            self.assertEqual([r[0] for r in cur.fetchall()], ["work.claimed", "work.claimed", "work.dead"])
            # immutable history
            self.assertSqlState("42501", lambda: cur.execute(
                "update public.cs_run_events set kind = 'x' where run_id = %s", (seed["run_id"],)))
            conn.rollback()
        finally:
            conn.close()
        with self.pg.connect() as su:
            self.assertSqlState("CS409", lambda: su.cursor().execute(
                "update public.cs_run_events set kind = 'x' where run_id = %s", (seed["run_id"],)))
        # an interrupted paid dispatch is never blindly re-claimed
        paid = self._seed_run(self.ws_a, "paid", stage="provider_submit")
        with self.pg.connect() as su:      # CS-INT2: paid stages are leased only while the spend switch is on
            su.cursor().execute("update cs_private.cs_runtime_limits set paid_provider_enabled = true "
                                "where environment_id='test'")
        conn, cur = self.sql_as("cs_worker")
        try:
            cur.execute("select count(*) from public.cs_claim_work('test','w1',"
                        "array['provider_submit']::cs_work_stage[],1)")
            self.assertEqual(cur.fetchone()[0], 1)
            conn.commit()
            with self.pg.connect() as su:
                su.cursor().execute("update public.cs_work_items set lease_until = now() - interval '1 second' "
                                    "where id = %s", (paid["work_item_id"],))
            cur.execute("select count(*) from public.cs_claim_work('test','w2',"
                        "array['provider_submit']::cs_work_stage[],1)")
            self.assertEqual(cur.fetchone()[0], 0)
            conn.commit()
        finally:
            conn.close()
        # dispatch switch is honoured
        with self.pg.connect() as su:
            su.cursor().execute("update cs_private.cs_runtime_limits set dispatch_enabled = false where environment_id='test'")
        try:
            conn, cur = self.sql_as("cs_worker")
            self.assertSqlState("CS503", lambda: cur.execute(f"select * from public.cs_claim_work('test','w',{stages},1)"))
            conn.close()
        finally:
            with self.pg.connect() as su:
                su.cursor().execute("update cs_private.cs_runtime_limits set dispatch_enabled = true "
                                    "where environment_id='test'")

    def test_12_worker_entry_runs_one_item_through_a_handler(self):
        self.assertEqual(cs_worker.run_once(self.settings, {}),
                         {"claimed": 0, "status": "idle", "reason": "no_stage_handlers_registered"})
        seed = self._seed_run(self.ws_a, "handler", stage="mux_video")

        def handler(item, ctx):
            ctx.heartbeat()
            v = ctx.commit_asset_version(label="Generated still", kind="generated", mime_type="image/png",
                                         data=PNG + b"-out", width=64, height=64)
            ref = {"workspace_id": v["workspace_id"], "asset_id": str(v["asset_id"]),
                   "asset_version_id": str(v["asset_version_id"]), "sha256": v["sha256"]}
            return cs_worker.StageResult("mux", {"engine": "test-double"}, [ref])
        wsgi = cs_worker.make_wsgi(lambda: self.settings, {"mux_video": handler})
        environ = {"REQUEST_METHOD": "POST", "HTTP_AUTHORIZATION": "Bearer wrong"}
        out = {}
        body = b"".join(wsgi(environ, lambda s, h: out.setdefault("s", s)))
        self.assertEqual((out["s"][:3], json.loads(body)), ("401", {"error": "authentication_required"}))
        out = {}
        environ["HTTP_AUTHORIZATION"] = "Bearer worker-test-secret"
        result = json.loads(b"".join(wsgi(environ, lambda s, h: out.setdefault("s", s))))
        self.assertEqual((out["s"][:3], result["status"], result["work_item_id"]), ("200", "finished", seed["work_item_id"]))
        st, run = call(self.app, "GET", f"/api/v1/workspaces/{self.ws_a}/runs/{seed['run_id']}", tok=self.tok["viewer_a"])
        self.assertEqual(run["nodes"][0]["state"], "succeeded")
        self.assertEqual(run["run"]["state"], "running", "B1 never fabricates run completion")
        self.assertEqual([e["kind"] for e in run["events"]], ["work.claimed", "work.finished"])
        # failing handler -> readable retry, not swallowed
        failing = self._seed_run(self.ws_a, "fail", stage="publish_export")

        def boom(item, ctx):
            raise RuntimeError("renderer crashed")
        res = cs_worker.run_once(self.settings, {"publish_export": boom})
        self.assertEqual((res["status"], res["failure_code"], res["work_item_id"]),
                         ("retry_wait", "stage_handler_error", failing["work_item_id"]))
        # a handler cannot attach another workspace's asset as output
        cross = self._seed_run(self.ws_a, "cross", stage="render_video_transition")
        b = self.asset_b

        def steal(item, ctx):
            return cs_worker.StageResult("x", {}, [{"workspace_id": self.ws_b, "asset_id": b["asset_id"],
                                                   "asset_version_id": b["version_id"], "sha256": b["sha256"]}])
        with self.assertRaises(ServiceError) as ctx:
            cs_worker.run_once(self.settings, {"render_video_transition": steal})
        self.assertEqual(ctx.exception.code, "output_ref_unresolved")
        del cross

    # -- 13 config refusals -----------------------------------------------------
    def test_13_config_bounds(self):
        env = {"CS_ENVIRONMENT_ID": "test", "CS_API_DATABASE_URL": "x", "CS_RELEASE_ID": "r",
               "CS_JWT_ISSUER": ISSUER, "CS_JWT_SECRET": "s", "CS_STORAGE_URL": "https://storage.example.test/storage/v1",
               "CS_STORAGE_ANON_KEY": "k"}
        self.assertEqual(load(env).signed_download_seconds, 300)
        self.assertEqual(load(env).download_grant_seconds, 60)
        for k, v in (("CS_SIGNED_DOWNLOAD_SECONDS", "301"), ("CS_DOWNLOAD_GRANT_SECONDS", "61"),
                     ("CS_ENVIRONMENT_ID", "../etc"), ("CS_JWT_SECRET", "")):
            with self.assertRaises(ConfigError, msg=k):
                load({**env, k: v})

    # -- 14 migration re-run ------------------------------------------------------
    def test_14_migration_is_idempotent(self):
        snapshot = ("select (select count(*) from pg_policies where tablename like 'cs\\_%%' or schemaname='cs_private' "
                    "or policyname like 'cs\\_%%'), (select count(*) from public.cs_workspaces), "
                    "(select count(*) from public.cs_asset_versions), (select count(*) from storage.buckets), "
                    "(select string_agg(rolname || rolsuper::text || rolbypassrls::text, ',' order by rolname) "
                    "from pg_roles where rolname like 'cs\\_%%')")
        with self.pg.connect() as conn:
            cur = conn.cursor()
            cur.execute(snapshot)
            before = cur.fetchone()
        self.pg.reapply_cs_b1()
        self.pg.reapply_cs_b1()
        with self.pg.connect() as conn:
            cur = conn.cursor()
            cur.execute(snapshot)
            self.assertEqual(cur.fetchone(), before)
        st, _ = call(self.app, "GET", f"/api/v1/workspaces/{self.ws_a}/runs", tok=self.tok["viewer_a"])
        self.assertEqual(st, 200, "grants survive re-run")

    # -- 99 durability: kill API + database processes, re-read ---------------
    def _start_api_process(self, port):
        env = {**os.environ, "CS_ENVIRONMENT_ID": "test", "CS_API_DATABASE_URL": self.pg.dsn(user="cs_api"),
               "CS_RELEASE_ID": "test-release", "CS_JWT_ISSUER": ISSUER, "CS_JWT_SECRET": JWT_SECRET,
               "CS_STORAGE_URL": self.storage.base_url, "CS_STORAGE_ANON_KEY": "anon-public-test-key"}
        proc = subprocess.Popen([sys.executable, str(Path(__file__).with_name("serve_api.py")), str(port)],
                                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=REPO_ROOT)
        self.assertEqual(proc.stdout.readline().strip(), b"ready")
        return proc

    def _read_state(self, base):
        out = {}
        for path in (f"/api/v1/workspaces/{self.ws_a}/runs/{self.run_a['run_id']}",
                     f"/api/v1/workspaces/{self.ws_b}/runs/{self.run_b['run_id']}",
                     f"/api/v1/workspaces/{self.ws_b}/assets", "/api/v1/me/workspaces"):
            who = "owner_b" if self.ws_b in path else "owner_a"
            st, body = http("GET", base + path, tok=self.tok[who])
            self.assertEqual(st, 200, (path, body))
            out[path] = json.loads(body)
        return out

    def test_99_records_survive_process_kill_and_database_crash(self):
        # put the run through real state changes first
        conn, cur = self.sql_as("cs_worker")
        cur.execute("select count(*) from public.cs_claim_work('test','w-durable',array['compose_image']::cs_work_stage[],2)")
        conn.commit()
        conn.close()
        port = __import__("tests.creative_studio.pg_harness", fromlist=["_free_port"])._free_port()
        base = f"http://127.0.0.1:{port}"
        proc = self._start_api_process(port)
        try:
            st, health = http("GET", base + "/api/v1/health")
            self.assertEqual((st, json.loads(health)["database_role"]), (200, "cs_api"))
            before = self._read_state(base)
            self.assertEqual(before[f"/api/v1/workspaces/{self.ws_a}/runs/{self.run_a['run_id']}"]["run"]["state"],
                             "running")
        finally:
            proc.kill()            # SIGKILL: no graceful shutdown
            proc.wait(10)
        old_pid = self.pg.postmaster_pid()
        self.pg.crash()            # SIGKILL postmaster: no shutdown checkpoint, WAL recovery on start
        self.pg.start()
        self.assertNotEqual(self.pg.postmaster_pid(), old_pid)
        proc = self._start_api_process(port)
        try:
            after = self._read_state(base)
        finally:
            proc.kill()
            proc.wait(10)
        self.assertEqual(after, before)
        with open(self.pg.tmp / "pg.log", encoding="utf-8", errors="replace") as fh:
            self.assertIn("database system was interrupted", fh.read())


if __name__ == "__main__":
    unittest.main(verbosity=2)
