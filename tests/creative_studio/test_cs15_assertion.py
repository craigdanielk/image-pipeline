"""CS-15 (ENG-15 Part CS): MOS gateway service assertion — unit + real PostgreSQL 17."""
from __future__ import annotations

import dataclasses
import hashlib
import io
import json
import threading
import time
import unittest
import uuid

import psycopg2

from service import assertion
from service.api import create_app
from service.config import ConfigError, Settings, load
from service.errors import ServiceError
from tests.creative_studio.assertion_support import KEY, bearer_sub, header_for, payload_for
from tests.creative_studio.fake_storage import FakeStorage
from tests.creative_studio.pg_harness import ThrowawayCluster
from tests.creative_studio.test_b2_real_db import call as b2_call
from tests.creative_studio.test_cs_b1_real_db import ISSUER, JWT_SECRET, token

HDR = assertion.HEADER_ENV_KEY
BODY = b'{"x":1}'
USER = str(uuid.uuid4())
NOW = 1_800_000_000


class Unit(unittest.TestCase):
    def mk(self, **kw):
        base = dict(user_id=USER, action="run.admit", binding="ws", body=BODY, now=NOW)
        base.update(kw)
        return header_for(**base)

    def v(self, header, **kw):
        args = dict(key=KEY, user_id=USER, action="run.admit", raw_body=BODY, now=NOW, skew=30)
        args.update(kw)
        return assertion.verify(header, **args)

    def code(self, header, **kw):
        with self.assertRaises(ServiceError) as c:
            self.v(header, **kw)
        return c.exception.code

    def test_valid(self):
        self.assertEqual(self.v(self.mk())["act"], "run.admit")

    def test_refusals(self):
        self.assertEqual(self.code(self.mk(key="z" * 40)), "assertion_invalid")
        self.assertEqual(self.code(self.mk(aud="other")), "assertion_invalid")
        self.assertEqual(self.code(self.mk(user_id=str(uuid.uuid4()))), "assertion_invalid")
        self.assertEqual(self.code(self.mk(action="run.cancel")), "assertion_invalid")
        self.assertEqual(self.code(self.mk(body=b"{}")), "assertion_invalid")
        self.assertEqual(self.code(self.mk(ttl=61)), "assertion_invalid")
        self.assertEqual(self.code(self.mk(ttl=0)), "assertion_invalid")
        self.assertEqual(self.code(self.mk(nonce="AAAA")), "assertion_invalid")
        self.assertEqual(self.code(self.mk(now=NOW - 200)), "assertion_expired")
        self.assertEqual(self.code(self.mk(now=NOW + 200)), "assertion_invalid")
        self.assertEqual(self.code("garbage"), "assertion_invalid")
        self.assertEqual(self.code(self.mk() + "x"), "assertion_invalid")

    def test_skew_boundary(self):
        self.v(self.mk(now=NOW - 45 - 30))            # exp = NOW - 30 -> within skew
        self.assertEqual(self.code(self.mk(now=NOW - 45 - 31)), "assertion_expired")

    def test_unconfigured_or_short_key_fails_closed(self):
        with self.assertRaises(ServiceError) as c:
            self.v(self.mk(), key="short")
        self.assertEqual(c.exception.status, 503)

    def test_extra_or_missing_fields_refused(self):
        p = payload_for(user_id=USER, action="run.admit", binding="ws", body=BODY, now=NOW)
        p["extra"] = 1
        self.assertEqual(self.code(assertion.sign(KEY, p)), "assertion_invalid")
        del p["extra"], p["bnd"]
        self.assertEqual(self.code(assertion.sign(KEY, p)), "assertion_invalid")

    def test_config(self):
        env = {"CS_ENVIRONMENT_ID": "test", "CS_JWT_SECRET": "s", "CS_STORAGE_URL": "http://x", "CS_API_DATABASE_URL": "d",
               "CS_RELEASE_ID": "r", "CS_JWT_ISSUER": "i", "CS_STORAGE_ANON_KEY": "a"}
        self.assertIsNone(load(env).mos_assertion_key)
        self.assertEqual(load({**env, "CS_MOS_ASSERTION_KEY": KEY}).assertion_skew_seconds, 30)
        with self.assertRaises(ConfigError):
            load({**env, "CS_MOS_ASSERTION_KEY": "too-short"})
        self.assertNotIn(KEY, repr(load({**env, "CS_MOS_ASSERTION_KEY": KEY})))

    def test_policy_covers_every_gateway_family_and_no_static_key(self):
        acts = {p.action for p in assertion.POLICY.values() if p.required}
        for need in ("connection.bind", "brand_package.import", "request.intake", "treatment.accept", "quote.create",
                     "run.admit"):
            self.assertIn(need, acts)
        from pathlib import Path
        root = Path(__file__).resolve().parents[2] / "service"
        for f in root.rglob("*.py"):
            self.assertNotIn("integration-key", f.read_text().lower().replace("_", "-"), f.name)


class GatewayStub:
    def authorize_binding(self, *, user_id, mos_workspace_id):
        return {"authority": "marketing-os", "external_workspace_id": mos_workspace_id, "subject_user_id": user_id,
                "decision": "allowed", "role": "owner", "checked_at": "2026-10-31T00:00:00Z"}


class RealDb(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pg = ThrowawayCluster()
        cls.pg.init()
        cls.pg.install()
        cls.storage = FakeStorage(cls.pg.dsn(), JWT_SECRET, ISSUER).start()
        cls.settings = Settings(
            api_database_url=cls.pg.dsn(user="cs_api"), worker_database_url=cls.pg.dsn(user="cs_worker"),
            environment_id="test", release_id="cs15", jwt_issuer=ISSUER, jwt_audience="authenticated",
            jwt_secret=JWT_SECRET, jwks_url=None, storage_url=cls.storage.base_url, storage_anon_key="anon-test",
            signed_download_seconds=300, download_grant_seconds=60, worker_secret="w", runtime_principal="cs-worker:test",
            mos_assertion_key=KEY)
        cls.app = create_app(cls.settings)
        cls.app.mos_gateway = GatewayStub()
        cls.users = {}
        with cls.pg.connect() as conn:
            for name in ("owner", "other"):
                uid = str(uuid.uuid4())
                conn.cursor().execute("insert into auth.users (id, email) values (%s, %s)", (uid, f"{name}@example-studio.test"))
                cls.users[name] = uid
        cls.tok = {k: token(v) for k, v in cls.users.items()}
        acct = cls.call("POST", "/api/v1/accounts", "owner", body={"name": "Example Agency"}, idem=str(uuid.uuid4()))[1]["account_id"]
        cls.ws = cls.call("POST", f"/api/v1/accounts/{acct}/workspaces", "owner", body={"name": "A"},
                          idem=str(uuid.uuid4()))[1]["workspace_id"]
        cls.ws2 = cls.call("POST", f"/api/v1/accounts/{acct}/workspaces", "owner", body={"name": "B"},
                           idem=str(uuid.uuid4()))[1]["workspace_id"]
        cls.mos, cls.mos2 = str(uuid.uuid4()), str(uuid.uuid4())
        cls.conn_url = f"/api/v1/workspaces/{cls.ws}/connections"
        cls.bind(cls.ws, cls.mos)
        cls.bind(cls.ws2, cls.mos2)
        cls.cid = cls.call("GET", cls.conn_url, "owner")[1]["connections"][0]["connection_id"]
        cls.refresh_url = f"{cls.conn_url}/{cls.cid}/refresh"

    @classmethod
    def tearDownClass(cls):
        cls.storage.stop()
        cls.pg.stop()

    @classmethod
    def call(cls, method, path, who, body=None, idem=None, header=None, app=None):
        raw = json.dumps(body).encode() if body is not None else b""
        environ = {"REQUEST_METHOD": method, "PATH_INFO": path, "QUERY_STRING": "", "wsgi.input": io.BytesIO(raw),
                   "CONTENT_LENGTH": str(len(raw)), "SERVER_NAME": "t", "SERVER_PORT": "80", "wsgi.url_scheme": "http",
                   "HTTP_AUTHORIZATION": f"Bearer {cls.tok[who]}"}
        if idem:
            environ["HTTP_IDEMPOTENCY_KEY"] = idem
        if header:
            environ[HDR] = header
        cap = {}
        out = b"".join((app or cls.app)(environ, lambda s, h: cap.update(status=int(s.split()[0]))))
        return cap["status"], json.loads(out)

    @classmethod
    def bind(cls, ws, mos):
        body = {"authority": "marketing-os", "external_workspace_id": mos}
        h = header_for(user_id=cls.users["owner"], action="connection.bind", binding=mos,
                       body=json.dumps(body).encode())
        st, out = cls.call("POST", f"/api/v1/workspaces/{ws}/connections", "owner", body=body, idem=str(uuid.uuid4()),
                           header=h)
        assert st in (200, 201), (st, out)

    def sign(self, **kw):
        base = dict(user_id=self.users["owner"], action="connection.refresh", binding=self.mos, body=b"")
        base.update(kw)
        return header_for(**base)

    def refresh(self, header, who="owner"):
        return self.call("POST", self.refresh_url, who, header=header)

    def nonces(self):
        with self.pg.connect() as conn:
            cur = conn.cursor()
            cur.execute("select count(*) from cs_private.cs_assertion_nonces")
            return cur.fetchone()[0]

    # ---------------------------------------------------------------------------------
    def test_01_valid_accepted_and_nonce_recorded(self):
        before = self.nonces()
        st, body = self.refresh(self.sign())
        self.assertEqual(st, 200, body)
        self.assertEqual(self.nonces(), before + 1)

    def test_02_replay_refused(self):
        h = self.sign()
        self.assertEqual(self.refresh(h)[0], 200)
        st, body = self.refresh(h)
        self.assertEqual((st, body), (401, {"error": "assertion_replayed"}))

    def test_03_concurrent_same_nonce_exactly_one_wins(self):
        h = self.sign()
        results = []
        barrier = threading.Barrier(8)

        def go():
            barrier.wait()
            results.append(self.refresh(h)[0])
        ts = [threading.Thread(target=go) for _ in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(sorted(results), [200] + [401] * 7)

    def test_04_expired_refused(self):
        st, body = self.refresh(self.sign(now=int(time.time()) - 300))
        self.assertEqual((st, body), (401, {"error": "assertion_expired"}))

    def test_05_wrong_body_user_action_binding(self):
        self.assertEqual(self.refresh(self.sign(body=b"{}"))[1], {"error": "assertion_invalid"})
        self.assertEqual(self.refresh(self.sign(user_id=self.users["other"]))[1], {"error": "assertion_invalid"})
        self.assertEqual(self.refresh(self.sign(action="run.admit"))[1], {"error": "assertion_invalid"})
        st, body = self.refresh(self.sign(binding=self.mos2))      # binding of another workspace
        self.assertEqual((st, body), (403, {"error": "binding_inactive"}))
        st, body = self.refresh(self.sign(binding=str(uuid.uuid4())))
        self.assertEqual((st, body), (403, {"error": "binding_inactive"}))

    def test_06_wrong_key_and_no_echo(self):
        st, body = self.refresh(self.sign(key="q" * 40))
        self.assertEqual((st, body), (401, {"error": "assertion_invalid"}))
        self.assertNotIn(KEY, json.dumps(body))

    def test_07_missing_refused_on_gateway_routes(self):
        for method, path in (("POST", self.refresh_url), ("POST", self.conn_url),
                             ("POST", f"/api/v1/workspaces/{self.ws}/quotes"),
                             ("POST", f"/api/v1/workspaces/{self.ws}/runs"),
                             ("POST", f"/api/v1/workspaces/{self.ws}/brand-packages/imports"),
                             ("POST", f"/api/v1/workspaces/{self.ws}/creative-requests/from-marketing-os"),
                             ("POST", f"/api/v1/workspaces/{self.ws}/treatments/{uuid.uuid4()}/accept")):
            st, body = self.call(method, path, "owner", body={}, idem=str(uuid.uuid4()))
            self.assertEqual((st, body), (401, {"error": "assertion_missing"}), path)

    def test_08_bearer_still_required_and_assertion_cannot_replace_it(self):
        raw = b""
        environ = {"REQUEST_METHOD": "POST", "PATH_INFO": self.refresh_url, "QUERY_STRING": "", "wsgi.input": io.BytesIO(raw),
                   "CONTENT_LENGTH": "0", HDR: self.sign()}
        cap = {}
        out = json.loads(b"".join(self.app(environ, lambda s, h: cap.update(status=int(s.split()[0])))))
        self.assertEqual((cap["status"], out), (401, {"error": "authentication_required"}))

    def test_09_native_routes_unaffected(self):
        self.assertEqual(self.call("GET", "/api/v1/me/workspaces", "owner")[0], 200)
        self.assertEqual(self.call("GET", f"/api/v1/workspaces/{self.ws}/runs", "owner")[0], 200)
        self.assertEqual(self.call("GET", self.conn_url, "owner")[0], 200)
        # shared route without header stays native (UI handoff path): refused by its own validation, not the assertion
        st, body = self.call("POST", f"/api/v1/workspaces/{self.ws}/handoffs/marketing-os", "owner", body={},
                             idem=str(uuid.uuid4()))
        self.assertNotIn(body.get("error"), ("assertion_missing", "assertion_invalid"))
        # ...but when a header IS sent it must be valid
        st, body = self.call("POST", f"/api/v1/workspaces/{self.ws}/handoffs/marketing-os", "owner", body={},
                             idem=str(uuid.uuid4()), header="bad.header")
        self.assertEqual(body, {"error": "assertion_invalid"})

    def test_10_bind_requires_bnd_equal_body_and_no_prior_binding(self):
        ws3 = self.call("POST", f"/api/v1/accounts/{self._acct()}/workspaces", "owner", body={"name": "C"},
                        idem=str(uuid.uuid4()))[1]["workspace_id"]
        new_mos = str(uuid.uuid4())
        body = {"authority": "marketing-os", "external_workspace_id": new_mos}
        raw = json.dumps(body).encode()
        bad = header_for(user_id=self.users["owner"], action="connection.bind", binding=self.mos, body=raw)
        st, out = self.call("POST", f"/api/v1/workspaces/{ws3}/connections", "owner", body=body, idem=str(uuid.uuid4()),
                            header=bad)
        self.assertEqual((st, out), (403, {"error": "binding_inactive"}))
        good = header_for(user_id=self.users["owner"], action="connection.bind", binding=new_mos, body=raw)
        st, out = self.call("POST", f"/api/v1/workspaces/{ws3}/connections", "owner", body=body, idem=str(uuid.uuid4()),
                            header=good)
        self.assertEqual(st, 201, out)

    def _acct(self):
        return self.call("GET", "/api/v1/me/workspaces", "owner")[1]["workspaces"][0]["account_id"]

    def test_11_revoked_binding_refused(self):
        acct = self._acct()
        ws = self.call("POST", f"/api/v1/accounts/{acct}/workspaces", "owner", body={"name": "R"},
                       idem=str(uuid.uuid4()))[1]["workspace_id"]
        mos = str(uuid.uuid4())
        self.bind(ws, mos)
        with self.pg.connect() as conn:
            conn.cursor().execute("update public.cs_external_bindings set status='revoked', revoked_at=now() "
                                  "where workspace_id=%s", (ws,))
        cid = self.call("GET", f"/api/v1/workspaces/{ws}/connections", "owner")[1]["connections"][0]["connection_id"]
        h = header_for(user_id=self.users["owner"], action="connection.refresh", binding=mos, body=b"")
        st, body = self.call("POST", f"/api/v1/workspaces/{ws}/connections/{cid}/refresh", "owner", header=h)
        self.assertEqual((st, body), (403, {"error": "binding_inactive"}))

    def test_12_nonce_table_insert_only_unreadable_and_swept(self):
        with self.pg.connect() as conn:
            cur = conn.cursor()
            with self.assertRaises(psycopg2.Error):
                cur.execute("update cs_private.cs_assertion_nonces set expires_at = now()")
        api = psycopg2.connect(self.pg.dsn(user="cs_api"))
        api.autocommit = True
        with self.assertRaises(psycopg2.Error):
            api.cursor().execute("select * from cs_private.cs_assertion_nonces")
        with self.assertRaises(psycopg2.Error):
            api.cursor().execute("insert into cs_private.cs_assertion_nonces(nonce, expires_at) values ('x'||repeat('y',20), now())")
        api.close()
        with self.pg.connect() as conn:
            cur = conn.cursor()
            cur.execute("insert into cs_private.cs_assertion_nonces(nonce, expires_at) values (%s, now() - interval '1 hour')",
                        ("old" + "n" * 20,))
            cur.execute("select cs_private.cs_safety_net_sweep()")
            cur.execute("select count(*) from cs_private.cs_assertion_nonces where nonce = %s", ("old" + "n" * 20,))
            self.assertEqual(cur.fetchone()[0], 0)

    def test_13_unconfigured_key_fails_closed(self):
        app = create_app(dataclasses.replace(self.settings, mos_assertion_key=None))
        app.mos_gateway = GatewayStub()
        st, body = self.call("POST", self.refresh_url, "owner", header=self.sign(), app=app)
        self.assertEqual(st, 503)

    # CS-21: Marketing OS is the authority for a workspace bound to it --------------------------------------
    def _as_other(self, method, path, action, binding, body=None):
        raw = json.dumps(body).encode() if body is not None else b""
        h = header_for(user_id=self.users["other"], action=action, binding=binding, body=raw)
        return self.call(method, path, "other", body=body, header=h)

    def test_14_signed_member_admitted_on_bound_workspace_only(self):
        funding = f"/api/v1/workspaces/{self.ws}/funding"
        # "other" is a Marketing OS member but not in the Studio account that owns the bound workspace.
        runs = f"/api/v1/workspaces/{self.ws}/runs"
        self.assertEqual(self.call("GET", runs, "other")[0], 404)                    # native: still no access
        st, body = self._as_other("GET", funding, "funding.read", self.mos)
        self.assertEqual(st, 200, body)
        # manage-level action (credential revoke) is admitted too: Marketing OS decided the role before signing
        st, body = self._as_other("DELETE", f"/api/v1/workspaces/{self.ws}/provider-credentials/higgsfield",
                                  "credential.revoke", self.mos)
        self.assertEqual(st, 200, body)
        # the grant names one workspace: signing for mos against ws2 is refused, and nothing leaks afterwards
        st, body = self._as_other("GET", f"/api/v1/workspaces/{self.ws2}/funding", "funding.read", self.mos)
        self.assertEqual((st, body), (403, {"error": "binding_inactive"}))
        self.assertEqual(self.call("GET", f"/api/v1/workspaces/{self.ws2}/runs", "other")[0], 404)
        self.assertEqual(self.call("GET", runs, "other")[0], 404)

    def test_15_grant_requires_active_binding(self):
        acct = self._acct()
        ws = self.call("POST", f"/api/v1/accounts/{acct}/workspaces", "owner", body={"name": "G"},
                       idem=str(uuid.uuid4()))[1]["workspace_id"]
        mos = str(uuid.uuid4())
        self.bind(ws, mos)
        self.assertEqual(self._as_other("GET", f"/api/v1/workspaces/{ws}/funding", "funding.read", mos)[0], 200)
        with self.pg.connect() as conn:
            conn.cursor().execute("update public.cs_external_bindings set status='revoked', revoked_at=now() "
                                  "where workspace_id=%s", (ws,))
        st, body = self._as_other("GET", f"/api/v1/workspaces/{ws}/funding", "funding.read", mos)
        self.assertEqual((st, body), (403, {"error": "binding_inactive"}))

    def test_16_resolve_binding_for_any_signed_member(self):
        url = f"/api/v1/bindings/marketing-os/{self.mos}"
        st, body = self._as_other("GET", url, "connection.resolve", self.mos)
        self.assertEqual(st, 200, body)
        self.assertEqual(body["workspace_id"], self.ws)
        self.assertEqual(body["connection_id"], self.cid)
        self.assertEqual(self.call("GET", url, "other"), (401, {"error": "assertion_missing"}))
        self.assertEqual(self._as_other("GET", url, "connection.resolve", self.mos2), (403, {"error": "binding_inactive"}))
        unknown = str(uuid.uuid4())
        st, body = self._as_other("GET", f"/api/v1/bindings/marketing-os/{unknown}", "connection.resolve", unknown)
        self.assertEqual((st, body), (404, {"error": "binding_not_found"}))

    def test_17_grant_setting_unavailable_to_database_clients(self):
        # Only the API sets cs.mos_grant after verification; an authenticated database client cannot use it to
        # reach the resolver or widen its own access.
        api = psycopg2.connect(self.pg.dsn(user="cs_api"))
        api.autocommit = True
        cur = api.cursor()
        cur.execute("select cs_private.cs_role_allows(%s, %s, 'read')", (self.users["other"], self.ws))
        self.assertFalse(cur.fetchone()[0])
        api.close()


if __name__ == "__main__":
    unittest.main()
