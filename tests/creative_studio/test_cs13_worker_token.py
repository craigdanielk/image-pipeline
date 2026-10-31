"""CS-13: worker storage credential refresh, readiness proof, deploy-file fixes.

Fake auth server (password grant) + fake storage over the real throwaway Postgres (real
storage RLS via cs_storage_allowed). No network beyond loopback.
"""
from __future__ import annotations

import dataclasses
import io
import json
import threading
import time
import unittest
import urllib.error
import uuid

from service import config
from service.api import create_app
from service.config import WorkerIdentityUnconfigured
from service.errors import ServiceError
from service.storage import StorageObjectStore, WorkerAuthError, WorkerTokenProvider, make_storage_store
from tests.creative_studio.fake_auth import FakeAuth
from tests.creative_studio.fake_storage import FakeStorage
from tests.creative_studio.pg_harness import REPO_ROOT, ThrowawayCluster
from tests.creative_studio.test_cs_b1_real_db import ISSUER, JWT_SECRET, call

EMAIL, PASSWORD = "cs-worker@example.test", "pw-Zx9-never-log-me"
ENV = "test"


class Clock:
    def __init__(self):
        self.offset = 0.0

    def __call__(self):
        return time.time() + self.offset


class CS13(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pg = ThrowawayCluster()
        cls.pg.init()
        cls.pg.install()
        with cls.pg.connect() as conn:
            cur = conn.cursor()
            for f in sorted((REPO_ROOT / "supabase" / "provisioning").glob("0[12]_*.sql")):
                cur.execute(f.read_text(encoding="utf-8"))
            cur.execute("update cs_private.cs_runtime_limits set admission_enabled = true, dispatch_enabled = true "
                        "where environment_id = 'test'")
            cls.worker_uid = str(uuid.uuid4())
            cur.execute("insert into auth.users (id, email) values (%s, %s)", (cls.worker_uid, EMAIL))
            cur.execute("insert into cs_private.cs_runtime_principals (user_id, environment_id) values (%s, %s)",
                        (cls.worker_uid, ENV))
        cls.auth = FakeAuth(JWT_SECRET, ISSUER).start()
        cls.auth.users[EMAIL] = (PASSWORD, cls.worker_uid)
        cls.storage = FakeStorage(cls.pg.dsn(), JWT_SECRET, ISSUER).start()
        cls.settings = config.Settings(
            api_database_url=cls.pg.dsn(user="cs_api"), worker_database_url=cls.pg.dsn(user="cs_worker"),
            environment_id=ENV, release_id="cs13", jwt_issuer=ISSUER, jwt_audience="authenticated",
            jwt_secret=JWT_SECRET, jwks_url=None, storage_url=cls.storage.base_url, storage_anon_key="anon-test-key",
            signed_download_seconds=300, download_grant_seconds=60, worker_secret="w-secret",
            runtime_principal="cs-worker:test", storage_worker_email=EMAIL, storage_worker_password=PASSWORD,
            auth_url=cls.auth.base_url)

    @classmethod
    def tearDownClass(cls):
        cls.auth.stop()
        cls.storage.stop()
        cls.pg.stop()

    def setUp(self):
        self.auth.ttl, self.auth.fail_status, self.auth.sign_ins = 3600, None, 0
        self.clock = Clock()
        self.provider = WorkerTokenProvider(self.settings, clock=self.clock)
        self.store = StorageObjectStore(self.settings, token_provider=self.provider)

    # -- caching / refresh ------------------------------------------------------------------
    def test_token_cached_then_refreshed_before_expiry(self):
        t1 = self.provider.token()
        self.assertEqual(self.provider.token(), t1)
        self.assertEqual(self.auth.sign_ins, 1)
        self.clock.offset = 3600 - 121          # still outside the 120 s margin
        self.assertEqual(self.provider.token(), t1)
        self.assertEqual(self.auth.sign_ins, 1)
        self.clock.offset = 3600 - 119          # inside the margin: refresh before expiry
        t2 = self.provider.token()
        self.assertNotEqual(t2, t1)
        self.assertEqual(self.auth.sign_ins, 2)

    def test_store_roundtrip_uses_one_signin(self):
        key = f"{ENV}/cs13/{uuid.uuid4()}.bin"
        self.store.put(key, b"bytes", "application/octet-stream")
        self.assertEqual(self.store.get(key), b"bytes")
        self.assertEqual(self.auth.sign_ins, 1)

    def test_concurrent_callers_share_one_signin(self):
        out = []
        threads = [threading.Thread(target=lambda: out.append(self.provider.token())) for _ in range(12)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(len(set(out)), 1)
        self.assertEqual(self.auth.sign_ins, 1)

    def test_rejected_expired_token_refreshes_once_and_retries_once(self):
        self.auth.ttl = -30                      # first token is already expired -> storage answers InvalidJWT
        real_sign_in = self.provider._sign_in

        def sign_in():
            tok = real_sign_in()
            self.auth.ttl = 3600                 # the refresh gets a good token
            return tok
        self.provider._sign_in = sign_in
        self.store.put(f"{ENV}/cs13/{uuid.uuid4()}.bin", b"x", "application/octet-stream")
        self.assertEqual(self.auth.sign_ins, 2)

    def test_literal_401_single_refresh_single_retry(self):
        calls = []

        class Resp:
            status = 200
            def __enter__(s): return s
            def __exit__(s, *a): return False
            def read(s): return b"ok"

        def opener(req, timeout=0):
            calls.append(req.get_header("Authorization"))
            if len(calls) == 1:
                raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(b"{}"))
            return Resp()
        store = StorageObjectStore(self.settings, token_provider=self.provider, opener=opener)
        self.assertEqual(store.get(f"{ENV}/a"), b"ok")
        self.assertEqual(len(calls), 2)
        self.assertNotEqual(calls[0], calls[1])
        self.assertEqual(self.auth.sign_ins, 2)

        calls.clear()

        def always_401(req, timeout=0):
            calls.append(1)
            raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(b"{}"))
        store = StorageObjectStore(self.settings, token_provider=self.provider, opener=always_401)
        with self.assertRaises(ServiceError) as cm:
            store.get(f"{ENV}/a")
        self.assertEqual(cm.exception.code, "storage_get_failed")
        self.assertEqual(len(calls), 2)          # exactly one retry, never a loop

    # -- failures are typed and never leak the secret --------------------------------------------
    def _assert_no_secret(self, err: ServiceError):
        text = repr(err) + json.dumps(err.body()) + str(err)
        self.assertNotIn(PASSWORD, text)
        self.assertNotIn(EMAIL, text)

    def test_signin_failure_typed_no_secret(self):
        bad = dataclasses.replace(self.settings, storage_worker_password="wrong-password-value")
        with self.assertRaises(WorkerAuthError) as cm:
            WorkerTokenProvider(bad).token()
        self.assertEqual(cm.exception.code, "worker_signin_failed")
        self._assert_no_secret(cm.exception)
        self.assertNotIn("wrong-password-value", repr(cm.exception) + str(cm.exception))
        self.auth.fail_status = 500
        with self.assertRaises(WorkerAuthError) as cm:
            self.provider.token()
        self.assertEqual(cm.exception.code, "worker_auth_unavailable")
        self._assert_no_secret(cm.exception)

    def test_settings_repr_hides_password(self):
        self.assertNotIn(PASSWORD, repr(self.settings))

    def test_config_fails_closed_in_hosted_mode(self):
        base = {"CS_ENVIRONMENT_ID": "test", "CS_API_DATABASE_URL": "postgres://x", "CS_RELEASE_ID": "r",
                "CS_JWT_ISSUER": ISSUER, "CS_JWT_SECRET": "s", "CS_STORAGE_URL": "https://p.supabase.co/storage/v1",
                "CS_STORAGE_ANON_KEY": "anon"}
        config.load(base)                                            # local/non-hosted: loads
        with self.assertRaises(WorkerIdentityUnconfigured):
            config.load({**base, "VERCEL": "1"})
        with self.assertRaises(WorkerIdentityUnconfigured):
            config.load({**base, "VERCEL": "1", "CS_STORAGE_WORKER_EMAIL": EMAIL})
        ok = config.load({**base, "VERCEL": "1", "CS_STORAGE_WORKER_EMAIL": EMAIL,
                          "CS_STORAGE_WORKER_PASSWORD": PASSWORD})
        self.assertEqual(ok.auth_base_url, ISSUER.rstrip("/"))
        self.assertFalse(hasattr(ok, "storage_worker_credential"))
        with self.assertRaises(WorkerIdentityUnconfigured):
            make_storage_store(config.load(base))

    # -- readiness proves the credential ------------------------------------------------------------
    def _readiness(self, settings):
        return call(create_app(settings), "GET", "/api/v1/readiness")

    def test_readiness_green_only_with_working_credential(self):
        st, body = self._readiness(self.settings)
        self.assertEqual((st, body.get("storage")), (200, "configured"))
        self.assertNotIn(PASSWORD, json.dumps(body))

    def test_readiness_red_when_credential_invalid(self):
        bad = dataclasses.replace(self.settings, storage_worker_password="not-the-password")
        st, body = self._readiness(bad)
        self.assertEqual((st, body["error"]), (503, "worker_signin_failed"))
        self.assertNotIn("not-the-password", json.dumps(body))
        self.assertNotIn("configured", json.dumps(body))

    def test_readiness_red_when_unconfigured(self):
        st, body = self._readiness(dataclasses.replace(self.settings, storage_worker_email=None,
                                                       storage_worker_password=None))
        self.assertEqual((st, body["error"]), (503, "worker_identity_unconfigured"))

    def test_readiness_red_when_storage_rejects_token(self):
        other = FakeAuth("a-different-signing-secret-of-32-bytes!!", ISSUER).start()
        try:
            other.users[EMAIL] = (PASSWORD, self.worker_uid)
            st, body = self._readiness(dataclasses.replace(self.settings, auth_url=other.base_url))
        finally:
            other.stop()
        self.assertEqual((st, body["error"]), (503, "storage_credential_rejected"))

    # -- provisioning script (idempotent, no secrets printed) ----------------------------------------
    def test_provisioning_script_idempotent_and_readback(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("p05", REPO_ROOT / "supabase/provisioning/05_worker_identity.py")
        p05 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(p05)
        email = "cs-worker-new@example.test"
        service_key = "service-key-SECRET-xyz"
        created = {}

        def admin_opener(req, timeout=0):
            assert req.get_header("Authorization") == f"Bearer {service_key}"
            body = json.loads(req.data or b"{}")

            class R:
                def __init__(s, status, data): s.status, s._d = status, json.dumps(data).encode()
                def __enter__(s): return s
                def __exit__(s, *a): return False
                def read(s): return s._d
            if req.get_method() == "POST":
                if email in created:
                    raise urllib.error.HTTPError(req.full_url, 422, "exists", {}, io.BytesIO(b"{}"))
                uid = str(uuid.uuid4())
                with self.pg.connect() as conn:
                    conn.cursor().execute("insert into auth.users (id, email) values (%s, %s)", (uid, body["email"]))
                created[email] = uid
                return R(200, {"id": uid})
            return R(200, {"id": created[email]})            # PUT: password converge
        env = {"PGDATABASE_ADMIN_URL": self.pg.dsn(), "CS_ADMIN_AUTH_URL": "http://auth.invalid/auth/v1",
               "CS_ADMIN_SERVICE_KEY": service_key, "CS_STORAGE_WORKER_EMAIL": email,
               "CS_STORAGE_WORKER_PASSWORD": "pw-secret-abc", "CS_ENVIRONMENT_ID": ENV}
        outputs = []
        for _ in range(2):                                   # second run proves idempotency
            buf = io.StringIO()
            self.assertEqual(p05.main(env, buf, admin_opener), 0, buf.getvalue())
            outputs.append(buf.getvalue())
        for text in outputs:
            self.assertEqual(text.count("[PASS]"), 6)
            self.assertNotIn("[FAIL]", text)
            self.assertNotIn(service_key, text)
            self.assertNotIn("pw-secret-abc", text)
        with self.pg.connect() as conn:
            cur = conn.cursor()
            cur.execute("select count(*), min(status) from cs_private.cs_runtime_principals where user_id = %s",
                        (created[email],))
            self.assertEqual(cur.fetchone(), (1, "active"))
        # revoked principal -> readback FAIL, nonzero exit
        with self.pg.connect() as conn:
            cur = conn.cursor()
            cur.execute("update cs_private.cs_runtime_principals set status='revoked' where user_id=%s",
                        (created[email],))
            results = dict(p05.readback(cur, created[email], ENV))
        self.assertFalse(results["worker may select under its environment prefix"])
        self.assertFalse(results["worker may insert under its environment prefix"])
        missing = io.StringIO()
        self.assertEqual(p05.main({"CS_ENVIRONMENT_ID": ENV}, missing, admin_opener), 2)
        self.assertNotIn(service_key, missing.getvalue())

    # -- deploy files ------------------------------------------------------------------------------
    def test_readback_sql_executes_and_shows_forced_rls(self):
        with self.pg.connect() as conn:
            cur = conn.cursor()
            cur.execute("select n.nspname, c.relname, c.relforcerowsecurity from pg_class c join pg_namespace n "
                        "on n.oid = c.relnamespace where n.nspname in ('public','cs_private') and c.relkind = 'r' "
                        "and c.relname like 'cs_%'")
            rows = cur.fetchall()
            self.assertTrue(rows and all(r[2] for r in rows))
            cur.execute((REPO_ROOT / "supabase/provisioning/04_readback_and_verification.sql").read_text())

    def test_deploy_files(self):
        vercel = json.loads((REPO_ROOT / "vercel.json").read_text())
        self.assertNotIn("crons", vercel)
        sql = (REPO_ROOT / "supabase/provisioning/04_readback_and_verification.sql").read_text()
        self.assertIn("relforcerowsecurity", sql)
        self.assertNotIn("forcerowsecurity as", sql.replace("relforcerowsecurity", ""))
        rb = (REPO_ROOT / "DEPLOY_RUNBOOK.md").read_text()
        self.assertIn("aws-1-eu-north-1.pooler.supabase.com", rb)
        self.assertNotIn("aws-0-eu-north-1", rb)
        self.assertNotIn("service/credentials.py", rb)
        self.assertNotIn("Zero-Grant", rb)
        self.assertIn("SELECT/INSERT", rb)
        self.assertIn("05_worker_identity", rb)
        self.assertNotIn("CS_STORAGE_WORKER_CREDENTIAL", rb)
        self.assertTrue((REPO_ROOT / "supabase/provisioning/05_worker_identity.md").exists())
        self.assertTrue((REPO_ROOT / "supabase/provisioning/05_worker_identity.py").exists())


if __name__ == "__main__":
    unittest.main()
