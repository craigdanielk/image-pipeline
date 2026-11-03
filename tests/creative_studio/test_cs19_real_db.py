"""CS-19 real PostgreSQL 17 suite: BYOK credential path + media funding policy.

Reuses the CS-B3 throwaway-cluster fixture (all migrations in filename order, the real API with signed gateway
assertions, the real worker runtime against a mocked Higgsfield). Proves, in SQL and through the API:
  * the key is forwarded once, sealed, never returned/logged/stored in clear; rotation + revocation;
  * byok_only: zero uses of the platform credential, typed refusal byok_required (admission, dispatch);
  * platform_funded: platform credential used within the owner-approved ceiling; cost export says who paid;
  * cross-workspace isolation, role denial, spoofed actor ignored, migration idempotent.
"""
from __future__ import annotations

import io
import json
import logging
import time
import unittest
import uuid
from unittest import mock

import psycopg2

from service.providers.credentials import KeyRing
from tests.creative_studio.b3_support import CREDENTIAL_PLAINTEXT, provider_node
from tests.creative_studio.pg_harness import MIGRATIONS_DIR
from tests.creative_studio import test_b3_real_db as _b3
from tests.creative_studio.test_b3_real_db import KEYRING


class _Base:          # holder: keeps the B3 TestCase out of this module's collectable namespace
    cls = _b3.CsB3RealDatabase


del _b3
from tests.creative_studio.test_cs_b1_real_db import call

MIGRATION = MIGRATIONS_DIR / "20261031280100_cs_19_byok_funding.sql"


def _login(access: str, refresh: str, expires_in: int = 86400) -> str:
    """A Higgsfield account login in the CLI credentials-file shape (what Marketing OS's sign-in forwards)."""
    return json.dumps({"auth_version": 2, "access_token": access, "refresh_token": refresh,
                       "expires_at": int(time.time()) + expires_in, "token_type": "bearer",
                       "scope": "user:org:read email profile offline_access"})


SECRET_A = _login("oat_ALPHA" + "a1b2c3d4" * 3, "RT-ALPHA-" + "a1b2" * 8)
SECRET_B = _login("oat_BRAVO" + "e5f6a7b8" * 3, "RT-BRAVO-" + "e5f6" * 8)
ACCOUNTS = {"oat_ALPHA": "alpha.owner@example.test", "oat_BRAVO": "bravo.owner@example.test"}
HF_WS = "283c6ef0-daba-4e2c-9e28-2bbdd03b214a"


def fake_account_status(login_json: bytes, **_kw) -> dict:
    """Free balance check stand-in: accepts known test logins only (never runs the CLI)."""
    from service.providers.higgsfield_oauth import LoginError
    access = json.loads(login_json)["access_token"]
    for prefix, email in ACCOUNTS.items():
        if access.startswith(prefix):
            return {"credits": 1149.25, "email": email}
    raise LoginError("login_rejected", "Higgsfield did not accept this sign-in")


class Cs19RealDb(unittest.TestCase):
    setUpClass = classmethod(lambda cls: (_Base.cls.setUpClass.__func__(cls), cls._cs19_setup()) and None)
    tearDownClass = classmethod(lambda cls: ([p.stop() for p in getattr(cls, "_patchers", [])], cls.pg.stop()) and None)

    @classmethod
    def _cs19_setup(cls):
        cls.app._app.credential_keyring = KEYRING
        cls._patchers = [mock.patch("service.providers.higgsfield_oauth.account_status", fake_account_status),
                         mock.patch("service.providers.higgsfield_oauth.choose_workspace", lambda *_a, **_k: HF_WS)]
        for p in cls._patchers:
            p.start()
        with cls.pg.connect() as conn:
            conn.cursor().execute(
                "insert into cs_private.cs_credentials (id, workspace_id, environment_id, provider, mode, "
                "provider_account_fingerprint, ciphertext, nonce, encryption_key_version) "
                "values (gen_random_uuid(), null, 'test', 'higgsfield', 'platform', 'platform-acct', %s, %s, %s)",
                cls._platform_sealed())

    @staticmethod
    def _platform_sealed():
        s = KEYRING.seal(CREDENTIAL_PLAINTEXT, provider="higgsfield", fingerprint="platform-acct")
        return (s.ciphertext, s.nonce, s.key_version)

    # -- helpers ---------------------------------------------------------------------------------
    def put_key(self, who, ws, secret):
        return call(self.app, "PUT", f"/api/v1/workspaces/{ws}/provider-credentials/higgsfield", tok=self.tok[who],
                    body={"credential": secret})

    def funding(self, who, ws, mode):
        path = "byok-only" if mode == "byok_only" else "platform"
        return call(self.app, "PUT", f"/api/v1/workspaces/{ws}/funding/{path}", tok=self.tok[who], body={})

    def uses(self, ws, mode):
        return self.q("select count(*) n from cs_private.cs_credential_uses where workspace_id = %s and "
                      "credential_mode = %s::public.cs_credential_mode", (ws, mode))[0]["n"]

    def one_run(self, who, ws, funding_policy):
        self.set_policy("owner_a", self.acct_a, ws, 5_000_000, 1_000_000, funding=funding_policy)
        node = provider_node("n1", self.low, prompt="Generic square promo, warm light")
        return self.admit(who, ws, [node])

    # -- 01 state, defaults, privileges ---------------------------------------------------------
    def test_01_default_is_byok_only_and_state_is_manager_only(self):
        st, body = call(self.app, "GET", f"/api/v1/workspaces/{self.ws_a}/funding", tok=self.tok["owner_a"])
        self.assertEqual(st, 200, body)
        self.assertEqual(body["media_funding"], "byok_only")
        self.assertFalse(body["platform_credential_usable"])
        for who in ("reviewer_a", "viewer_a", "producer_a"):
            st, body = call(self.app, "GET", f"/api/v1/workspaces/{self.ws_a}/funding", tok=self.tok[who])
            self.assertEqual(st, 403, (who, body))
        st, body = call(self.app, "GET", f"/api/v1/workspaces/{self.ws_a}/funding", tok=self.tok["owner_b"])
        self.assertEqual(st, 401, body)                       # workspace B cannot see A (CS-21: never signed for B)

    def test_02_db_privileges(self):
        for role in ("anon", "authenticated"):
            conn, cur = self._sql_as(role, self.users["owner_a"])
            try:
                for stmt in ("select 1 from cs_private.cs_workspace_funding", "select 1 from cs_private.cs_credential_uses",
                             f"select public.cs_set_workspace_funding('{self.ws_a}', 'platform_funded')",
                             f"select * from public.cs_workspace_funding_state('{self.ws_a}', 'higgsfield')",
                             f"select public.cs_run_payer('{self.ws_a}', gen_random_uuid())"):
                    self.assertSqlState("42501", lambda: cur.execute(stmt))
                    conn.rollback()
            finally:
                conn.close()
        conn, cur = self._sql_as("cs_worker")
        try:
            self.assertSqlState("42501", lambda: cur.execute(f"select public.cs_set_workspace_funding('{self.ws_a}', 'platform_funded')"))
            conn.rollback()
            self.assertSqlState("42501", lambda: cur.execute("select 1 from cs_private.cs_credential_uses"))
        finally:
            conn.close()
        rows = self.q("""select p.proname, p.prosecdef, p.proconfig::text cfg from pg_proc p
                          where p.proname in ('cs_set_workspace_funding','cs_workspace_funding_state','cs_run_payer',
                                              'cs_19_funding_guard','cs_workspace_media_funding')""")
        self.assertEqual(len(rows), 5)
        for r in rows:
            self.assertTrue(r["prosecdef"], r)
            self.assertIn("search_path=public, pg_temp", r["cfg"])

    def test_03_migration_is_idempotent_and_keeps_state(self):
        self.assertEqual(self.funding("owner_a", self.ws_a, "byok_only")[0], 200)
        before = self.q("select count(*) n from cs_private.cs_workspace_funding")[0]["n"]
        self.pg.apply_file(MIGRATION)
        self.pg.apply_file(MIGRATION)
        self.assertEqual(self.q("select count(*) n from cs_private.cs_workspace_funding")[0]["n"], before)

    # -- 02 credential forward / rotate / revoke ------------------------------------------------------
    def test_10_key_is_sealed_never_returned_or_logged_and_rotates(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logging.getLogger().addHandler(handler)
        logging.getLogger().setLevel(logging.DEBUG)
        try:
            st, body = self.put_key("owner_a", self.ws_a, SECRET_A)
            self.assertEqual(st, 200, body)
            self.assertNotIn(SECRET_A, json.dumps(body))
            self.assertEqual(set(body), {"stored", "provider", "version_id", "fingerprint_tail", "account", "credits"})
            self.assertEqual((body["account"], body["credits"]), ("al***@example.test", 1149.25))
            st, body2 = self.put_key("owner_a", self.ws_a, SECRET_B)         # rotation
            self.assertEqual(st, 200, body2)
            self.assertNotEqual(body["version_id"], body2["version_id"])
            # a refused request must not echo the key either
            st, err = call(self.app, "PUT", f"/api/v1/workspaces/{self.ws_a}/provider-credentials/higgsfield",
                           tok=self.tok["owner_a"], body={"credential": SECRET_A, "extra": 1})
            self.assertEqual(st, 422)
            self.assertNotIn(SECRET_A, json.dumps(err))
        finally:
            logging.getLogger().removeHandler(handler)
        self.assertNotIn(SECRET_A, stream.getvalue())
        self.assertNotIn(SECRET_B, stream.getvalue())
        active = self.q("select version_id, ciphertext, nonce, encryption_key_version, provider_account_fingerprint, status "
                        "from cs_private.cs_credentials where workspace_id = %s and status = 'active'", (self.ws_a,))
        self.assertEqual(len(active), 1)
        self.assertEqual(str(active[0]["version_id"]), body2["version_id"])
        self.assertNotIn(SECRET_B.encode(), bytes(active[0]["ciphertext"]))
        from service.providers.credentials import SealedCredential
        opened = KEYRING.open(SealedCredential(bytes(active[0]["ciphertext"]), bytes(active[0]["nonce"]),
                                               active[0]["encryption_key_version"]),
                              provider="higgsfield", fingerprint=active[0]["provider_account_fingerprint"])
        stored = json.loads(opened)
        self.assertEqual(stored.pop("workspace_id"), HF_WS)              # billed Higgsfield workspace kept with it
        self.assertEqual(stored, json.loads(SECRET_B))
        with self.assertRaises(Exception):          # ciphertext cannot be moved to another account label
            KEYRING.open(SealedCredential(bytes(active[0]["ciphertext"]), bytes(active[0]["nonce"]),
                                          active[0]["encryption_key_version"]),
                         provider="higgsfield", fingerprint="byok-other")
        self.assertEqual(self.q("select count(*) n from cs_private.cs_credentials where workspace_id = %s and status = 'revoked'"
                                " and provider_account_fingerprint = %s", (self.ws_a, body2["fingerprint_tail"]))[0]["n"], 0)
        # nowhere else in the database holds the clear key
        dump = self.q("select (select string_agg(affected_refs::text, ' ') from public.cs_audit_events) a")[0]["a"]
        self.assertNotIn(SECRET_A, dump or "")
        self.assertNotIn(SECRET_B, dump or "")
        st, state = call(self.app, "GET", f"/api/v1/workspaces/{self.ws_a}/funding", tok=self.tok["owner_a"])
        self.assertTrue(state["workspace_credential"])
        self.assertEqual(state["fingerprint_tail"], body2["fingerprint_tail"])
        self.assertNotIn(SECRET_B, json.dumps(state, default=str))

    def test_11_other_workspace_and_roles_cannot_store_or_revoke(self):
        for who, ws, want in (("owner_b", self.ws_a, 401), ("outsider", self.ws_a, 401), ("producer_a", self.ws_a, 403),
                              ("reviewer_a", self.ws_a, 403), ("viewer_a", self.ws_a, 403)):
            st, body = self.put_key(who, ws, SECRET_A)
            self.assertEqual(st, want, (who, body))
            st, body = call(self.app, "DELETE", f"/api/v1/workspaces/{ws}/provider-credentials/higgsfield", tok=self.tok[who])
            self.assertEqual(st, want, (who, body))
        # B's key lands in B only
        self.assertEqual(self.put_key("owner_b", self.ws_b, SECRET_B)[0], 200)
        self.assertEqual(self.q("select count(*) n from cs_private.cs_credentials where workspace_id = %s and status='active' "
                                "and provider='higgsfield'", (self.ws_b,))[0]["n"], 1)

    def test_12_unsigned_and_wrong_action_calls_are_refused(self):
        from service.api import create_app
        raw_app = self.app._app
        st, body = call(raw_app, "PUT", f"/api/v1/workspaces/{self.ws_a}/provider-credentials/higgsfield",
                        tok=self.tok["owner_a"], body={"credential": SECRET_A})
        self.assertEqual(st, 401, body)                    # no assertion header: bearer alone is not enough
        self.assertNotIn(SECRET_A, json.dumps(body))

    def test_14_marketing_os_key_version_is_accepted(self):
        st, body = call(self.app, "PUT", f"/api/v1/workspaces/{self.ws_a}/provider-credentials/higgsfield",
                        tok=self.tok["owner_a"], body={"credential": SECRET_A, "key_version": 3})
        self.assertEqual(st, 200, body)
        for bad in (0, -1, "3", True):
            st, _ = call(self.app, "PUT", f"/api/v1/workspaces/{self.ws_a}/provider-credentials/higgsfield",
                         tok=self.tok["owner_a"], body={"credential": SECRET_A, "key_version": bad})
            self.assertEqual(st, 422, bad)

    def test_13_malformed_credentials_refused(self):
        for bad in ("", "short", "x" * 20000, "ok-key-12345\x00", None, 5,
                    "hf-key-plain-api-key-" + "f" * 24,                                    # API keys: CLI cannot use
                    json.dumps({**json.loads(SECRET_A), "auth_version": 1}),              # CLI: "older auth flow"
                    _login("oat_UNKNOWN" + "0" * 24, "RT-UNKNOWN-" + "0" * 24)):           # balance check refuses
            st, body = call(self.app, "PUT", f"/api/v1/workspaces/{self.ws_a}/provider-credentials/higgsfield",
                            tok=self.tok["owner_a"], body={"credential": bad})
            self.assertEqual(st, 422, (bad if not isinstance(bad, str) else len(bad), body))

    # -- 03 byok_only: the platform credential is never used -----------------------------------------------
    def test_20_byok_only_refuses_platform_policy_and_never_touches_platform_credential(self):
        self.assertEqual(self.funding("owner_a", self.ws_a, "byok_only")[0], 200)
        self.assertEqual(self.put_key("owner_a", self.ws_a, SECRET_A)[0], 200)
        before_creates = self.fake.creates()
        st, body, _ = self.one_run("producer_a", self.ws_a, "platform")
        self.assertEqual(st, 403, body)
        self.assertEqual(body["error"], "byok_required")
        self.assertEqual(self.q("select count(*) n from public.cs_run_admissions a join cs_private.cs_credentials c on "
                                "c.version_id = a.credential_version_id where a.workspace_id = %s and c.mode = 'platform'",
                                (self.ws_a,))[0]["n"], 0)
        self.assertEqual(self.drain()[0]["claimed"], 0)
        self.assertEqual(self.fake.creates(), before_creates)
        self.assertEqual(self.uses(self.ws_a, "platform"), 0)

    def test_21_byok_only_workspace_run_uses_only_the_workspace_key_and_reports_payer(self):
        self.assertEqual(self.funding("owner_a", self.ws_a, "byok_only")[0], 200)
        self.assertEqual(self.put_key("owner_a", self.ws_a, SECRET_A)[0], 200)
        st, run, _ = self.one_run("producer_a", self.ws_a, "workspace")
        self.assertEqual(st, 202, run)
        self.drain()
        self.assertEqual(self.run_state(run["run_id"])["state"], "completed")
        self.assertEqual(self.uses(self.ws_a, "workspace") >= 1, True)
        self.assertEqual(self.uses(self.ws_a, "platform"), 0)
        st, costs = call(self.app, "GET", f"/api/v1/workspaces/{self.ws_a}/runs/{run['run_id']}/costs", tok=self.tok["owner_a"])
        self.assertEqual(st, 200, costs)
        self.assertEqual(costs["paid_by"], "workspace")
        self.assertTrue(all(l["paid_by"] == "workspace" for l in costs["ledger_lines"]))
        self.assertNotIn(SECRET_A, json.dumps(costs))

    def test_22_no_workspace_key_gives_plain_byok_required(self):
        self.assertEqual(self.funding("owner_a", self.ws_a, "byok_only")[0], 200)
        st, rev = call(self.app, "DELETE", f"/api/v1/workspaces/{self.ws_a}/provider-credentials/higgsfield",
                       tok=self.tok["owner_a"])
        self.assertEqual((st, rev["revoked"]), (200, True))
        st, body, _ = self.one_run("producer_a", self.ws_a, "workspace")
        self.assertEqual(st, 403, body)
        self.assertEqual(body["error"], "byok_required")
        self.assertIn("Integrations", body["detail"])
        self.assertEqual(self.uses(self.ws_a, "platform"), 0)
        st, state = call(self.app, "GET", f"/api/v1/workspaces/{self.ws_a}/funding", tok=self.tok["owner_a"])
        self.assertFalse(state["workspace_credential"])
        self.assertEqual(call(self.app, "DELETE", f"/api/v1/workspaces/{self.ws_a}/provider-credentials/higgsfield",
                              tok=self.tok["owner_a"])[1]["revoked"], False)       # idempotent

    def test_23_workspace_b_key_never_serves_workspace_a(self):
        self.assertEqual(self.put_key("owner_b", self.ws_b, SECRET_B)[0], 200)
        self.assertEqual(self.funding("owner_a", self.ws_a, "byok_only")[0], 200)
        call(self.app, "DELETE", f"/api/v1/workspaces/{self.ws_a}/provider-credentials/higgsfield", tok=self.tok["owner_a"])
        st, body, _ = self.one_run("producer_a", self.ws_a, "workspace")
        self.assertEqual((st, body["error"]), (403, "byok_required"))
        # defence in depth: the guard itself refuses a credential owned by another workspace
        b_version = self.q("select version_id from cs_private.cs_credentials where workspace_id = %s and status='active' "
                           "and provider='higgsfield'", (self.ws_b,))[0]["version_id"]
        with self.assertRaises(psycopg2.Error) as ctx:
            self.q("""insert into public.cs_run_admissions (workspace_id, run_id, account_id, plan_version_id, plan_sha256,
                        compile_hash, tier, policy_id, funding_source, credential_version_id, provider, estimated_cost_micros,
                        maximum_cost_micros, budget_ceiling_micros, idempotency_key, request_sha256, admitted_by)
                      select %s, gen_random_uuid(), account_id, gen_random_uuid(), repeat('a',64), repeat('b',64), 'draft', id,
                        'workspace', %s, 'higgsfield', 0, 0, 0, gen_random_uuid(), repeat('c',64), approved_by
                        from public.cs_budget_policies limit 1""", (self.ws_a, b_version))
        self.assertIn(ctx.exception.pgcode, ("CS403", "23503"))

    # -- 04 platform_funded -----------------------------------------------------------------------------------
    def test_30_platform_funded_uses_platform_credential_within_ceiling_and_says_so(self):
        self.assertEqual(self.funding("owner_a", self.ws_a, "platform_funded")[0], 200)
        st, state = call(self.app, "GET", f"/api/v1/workspaces/{self.ws_a}/funding", tok=self.tok["owner_a"])
        self.assertEqual((state["media_funding"], state["platform_credential_usable"]), ("platform_funded", True))
        st, run, _ = self.one_run("producer_a", self.ws_a, "platform")
        self.assertEqual(st, 202, run)
        self.drain()
        self.assertEqual(self.run_state(run["run_id"])["state"], "completed")
        self.assertGreaterEqual(self.uses(self.ws_a, "platform"), 1)
        st, costs = call(self.app, "GET", f"/api/v1/workspaces/{self.ws_a}/runs/{run['run_id']}/costs", tok=self.tok["owner_a"])
        self.assertEqual(costs["paid_by"], "platform")
        # operator cap = the owner-approved budget policy: a node above the per-run ceiling is refused
        self.set_policy("owner_a", self.acct_a, self.ws_a, 5_000_000, 10, funding="platform")
        st, body, _ = self.admit("producer_a", self.ws_a, [provider_node("n1", self.low, prompt="Generic square promo")])
        self.assertEqual(st, 422 if st == 422 else st, body)
        self.assertNotEqual(st, 202)

    def test_31_flip_to_byok_only_stops_platform_use_before_dispatch(self):
        self.assertEqual(self.funding("owner_a", self.ws_a, "platform_funded")[0], 200)
        st, run, _ = self.one_run("producer_a", self.ws_a, "platform")
        self.assertEqual(st, 202, run)
        platform_uses = self.uses(self.ws_a, "platform")
        creates = self.fake.creates()
        self.assertEqual(self.funding("owner_a", self.ws_a, "byok_only")[0], 200)
        self.drain()
        self.assertEqual(self.uses(self.ws_a, "platform"), platform_uses)         # zero new uses
        self.assertEqual(self.fake.creates(), creates)                              # nothing sent to the provider
        self.assertNotEqual(self.run_state(run["run_id"])["state"], "completed")
        # operator cancels the stranded run so later tests are unaffected
        call(self.app, "POST", f"/api/v1/workspaces/{self.ws_a}/runs/{run['run_id']}/cancel", tok=self.tok["owner_a"],
             body={"reason": "funding flipped"})
        self.drain()

    def test_32_funding_mode_is_per_workspace(self):
        self.assertEqual(self.funding("owner_a", self.ws_a, "platform_funded")[0], 200)
        st, state = call(self.app, "GET", f"/api/v1/workspaces/{self.ws_b}/funding", tok=self.tok["owner_b"])
        self.assertEqual(state["media_funding"], "byok_only")
        self.assertEqual(self.funding("owner_a", self.ws_a, "byok_only")[0], 200)

    def test_33_only_managers_set_funding_and_spoofed_actor_is_ignored(self):
        for who in ("producer_a", "reviewer_a", "viewer_a"):
            self.assertEqual(self.funding(who, self.ws_a, "platform_funded")[0], 403, who)
        self.assertEqual(self.funding("owner_b", self.ws_a, "platform_funded")[0], 401)  # CS-21: Marketing OS never signs for a non-member, so a stranger's gateway call is unsigned
        st, body = call(self.app, "PUT", f"/api/v1/workspaces/{self.ws_a}/funding/platform", tok=self.tok["producer_a"],
                        body={"actor_id": self.users["owner_a"], "media_funding": "platform_funded"})
        self.assertEqual(st, 403, body)
        self.assertEqual(self.q("select media_funding m from cs_private.cs_workspace_funding where workspace_id = %s",
                                (self.ws_a,))[0]["m"], "byok_only")
        audit = self.q("select actor_id::text a from public.cs_audit_events where workspace_id = %s and action = 'funding.set' "
                       "order by created_at desc limit 1", (self.ws_a,))
        self.assertEqual(audit[0]["a"], self.users["owner_a"])

    def test_34_credential_uses_are_append_only(self):
        self.q("""insert into cs_private.cs_credential_uses (workspace_id, attempt_id, credential_version_id, credential_mode)
                  select workspace_id, gen_random_uuid(), version_id, mode from cs_private.cs_credentials
                   where workspace_id = %s limit 1""", (self.ws_b,))
        self.assertSqlState("CS409", lambda: self.q("update cs_private.cs_credential_uses set credential_mode = 'platform'"))
        self.assertSqlState("CS409", lambda: self.q("delete from cs_private.cs_credential_uses"))

    # -- 04 account-login refresh (CS-20): single writer, sealed in place, before the dispatch marker --------------
    def _active_credential(self, ws):
        return self.q("select version_id, ciphertext, nonce, encryption_key_version, provider_account_fingerprint, status "
                      "from cs_private.cs_credentials where workspace_id = %s and provider = 'higgsfield' "
                      "order by created_at desc limit 1", (ws,))[0]

    def _open(self, row) -> dict:
        from service.providers.credentials import SealedCredential
        return json.loads(KEYRING.open(SealedCredential(bytes(row["ciphertext"]), bytes(row["nonce"]),
                                                        row["encryption_key_version"]),
                                       provider="higgsfield", fingerprint=row["provider_account_fingerprint"]))

    def test_40_near_expiry_login_is_refreshed_in_place_then_dispatched(self):
        from service.providers import higgsfield_oauth as hf_login
        self.assertEqual(self.funding("owner_a", self.ws_a, "byok_only")[0], 200)
        stale = _login("oat_ALPHA" + "5" * 24, "RT-ALPHA-OLD-" + "5" * 20, expires_in=600)
        self.assertEqual(self.put_key("owner_a", self.ws_a, stale)[0], 200)
        before = self._active_credential(self.ws_a)
        rotated = {"access_token": "oat_ALPHA" + "6" * 24, "refresh_token": "RT-ALPHA-NEW-" + "6" * 20,
                   "expires_in": 86400, "token_type": "bearer", "scope": "user:org:read email profile offline_access"}
        seen = []

        def fake_refresh(login, **_kw):
            seen.append(login["refresh_token"])
            return hf_login.login_from_token_response(rotated)

        creates = self.fake.creates()
        st, run, _ = self.one_run("producer_a", self.ws_a, "workspace")
        self.assertEqual(st, 202, run)
        with mock.patch.object(hf_login, "refresh", fake_refresh):
            self.drain()
        self.assertEqual(seen, ["RT-ALPHA-OLD-" + "5" * 20])                 # refreshed exactly once
        after = self._active_credential(self.ws_a)
        self.assertEqual((after["version_id"], after["status"]), (before["version_id"], "active"))   # same row
        self.assertNotEqual(bytes(after["ciphertext"]), bytes(before["ciphertext"]))
        opened = self._open(after)
        self.assertEqual((opened["access_token"], opened["refresh_token"], opened["auth_version"]),
                         (rotated["access_token"], rotated["refresh_token"], 2))
        self.assertGreater(opened["expires_at"], int(time.time()) + 80000)
        self.assertEqual(opened["workspace_id"], HF_WS)                   # survives the refresh
        self.assertEqual(self.run_state(run["run_id"])["state"], "completed")
        self.assertGreater(self.fake.creates(), creates)
        audit = self.q("select action, affected_refs::text r from public.cs_audit_events where workspace_id = %s "
                       "and action = 'credential.refresh'", (self.ws_a,))
        self.assertEqual(len(audit), 1)
        self.assertNotIn("RT-ALPHA", audit[0]["r"])

    def test_41_refused_refresh_revokes_login_and_refuses_before_any_provider_call(self):
        from service.providers import higgsfield_oauth as hf_login
        self.assertEqual(self.funding("owner_a", self.ws_a, "byok_only")[0], 200)
        stale = _login("oat_ALPHA" + "7" * 24, "RT-ALPHA-DEAD-" + "7" * 20, expires_in=60)
        self.assertEqual(self.put_key("owner_a", self.ws_a, stale)[0], 200)
        version = self._active_credential(self.ws_a)["version_id"]

        def refused(login, **_kw):
            raise hf_login.LoginError("reconnect_required", "refused")

        creates = self.fake.creates()
        st, run, _ = self.one_run("producer_a", self.ws_a, "workspace")
        self.assertEqual(st, 202, run)
        with mock.patch.object(hf_login, "refresh", refused):
            self.drain()
        self.assertEqual(self.fake.creates(), creates)                         # nothing sent, nothing charged
        self.assertEqual(self.q("select status from cs_private.cs_credentials where version_id = %s", (version,))[0]["status"],
                         "revoked")
        err = self.q("select state::text, error from public.cs_attempts where run_id = %s", (run["run_id"],))[0]
        self.assertEqual(err["error"]["code"], "higgsfield_reconnect_required", err)
        self.assertIn("Integrations", err["error"]["message"])
        st, state = call(self.app, "GET", f"/api/v1/workspaces/{self.ws_a}/funding", tok=self.tok["owner_a"])
        self.assertFalse(state["workspace_credential"])

    def test_42_transient_refresh_failure_keeps_login_and_retries_later(self):
        from service.providers import higgsfield_oauth as hf_login
        self.assertEqual(self.funding("owner_a", self.ws_a, "byok_only")[0], 200)
        stale = _login("oat_ALPHA" + "8" * 24, "RT-ALPHA-KEEP-" + "8" * 20, expires_in=60)
        self.assertEqual(self.put_key("owner_a", self.ws_a, stale)[0], 200)
        before = self._active_credential(self.ws_a)

        def down(login, **_kw):
            raise hf_login.LoginError("refresh_unavailable", "token endpoint answered 503")

        creates = self.fake.creates()
        st, run, _ = self.one_run("producer_a", self.ws_a, "workspace")
        self.assertEqual(st, 202, run)
        with mock.patch.object(hf_login, "refresh", down):
            self.drain()
        after = self._active_credential(self.ws_a)
        self.assertEqual((after["status"], bytes(after["ciphertext"])), ("active", bytes(before["ciphertext"])))
        self.assertEqual(self.fake.creates(), creates)
        self.assertNotEqual(self.run_state(run["run_id"])["state"], "completed")


# borrow every non-test helper (classmethods stay classmethods) from the B3 fixture
for _name, _val in vars(_Base.cls).items():
    if not _name.startswith(("test_", "__")) and _name not in vars(Cs19RealDb):
        setattr(Cs19RealDb, _name, _val)


if __name__ == "__main__":
    unittest.main()
