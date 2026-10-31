"""CS-16 real PostgreSQL 17: hosted compile -> quote -> admit. Every migration applied in order, then re-applied.
Mocked provider transport only (FakeHiggsfield at the adapter's process boundary); no network, no production."""
from __future__ import annotations

import base64
import dataclasses
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import psycopg2

from service import assertion
from service.api import create_app
from service.config import Settings
from service.plans import store as plan_store
from service.providers import catalogue as cat_mod
from service.providers.adapter import engine_higgsfield
from service.providers.credentials import KeyRing
from tests.creative_studio import b2_mos_fixtures as fx
from tests.creative_studio.assertion_support import KEY, GatewayApp, header_for
from tests.creative_studio.b3_support import CREDENTIAL_PLAINTEXT, TEST_KEYS, FakeHiggsfield, load_source
from tests.creative_studio.fake_storage import FakeStorage
from tests.creative_studio.pg_harness import MIGRATIONS_DIR, ThrowawayCluster, claims_json
from tests.creative_studio import test_b2_real_db as _b2
from tests.creative_studio.test_b2_real_db import call
from tests.creative_studio.test_cs_b1_real_db import ISSUER, JWT_SECRET, token

KEYRING = KeyRing({k: base64.b64decode(v) for k, v in TEST_KEYS.items()})
MIGRATION = "20261031220100_cs_16_execution_plans.sql"


class CsHostedCompile(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        B2 = _b2.CsB2RealDatabase       # helper methods only; not collected as a suite here
        cls.pg = ThrowawayCluster()
        cls.pg.init()
        cls.applied = cls.pg.install()
        cls.tmp = Path(tempfile.mkdtemp(prefix="cs16_"))
        cls.fake = FakeHiggsfield(cls.tmp / "provider")
        with cls.pg.connect() as conn:
            conn.cursor().execute("update cs_private.cs_runtime_limits set admission_enabled = true, "
                                  "paid_provider_enabled = false, active_run_limit = 6 where environment_id = 'test'")
        cls.storage = FakeStorage(cls.pg.dsn(), JWT_SECRET, ISSUER).start()
        cls.settings = Settings(
            api_database_url=cls.pg.dsn(user="cs_api"), worker_database_url=cls.pg.dsn(user="cs_worker"),
            environment_id="test", release_id="test-release", jwt_issuer=ISSUER, jwt_audience="authenticated",
            jwt_secret=JWT_SECRET, jwks_url=None, storage_url=cls.storage.base_url, storage_anon_key="anon-test",
            signed_download_seconds=300, download_grant_seconds=60, worker_secret="w", runtime_principal="cs-worker:test",
            mos_assertion_key=KEY)
        cls.users = {}
        with cls.pg.connect() as conn:
            cur = conn.cursor()
            for name in ("owner_a", "producer_a", "viewer_a", "owner_b", "machine"):
                uid = str(uuid.uuid4())
                cur.execute("insert into auth.users (id, email) values (%s, %s)", (uid, f"{name}@example-studio.test"))
                cls.users[name] = uid
            cur.execute("insert into cs_private.cs_runtime_principals (user_id, environment_id) values (%s, 'test')",
                        (cls.users["machine"],))
        cls.tok = {k: token(v) for k, v in cls.users.items()}
        from service.brand.importer import StorageObjectWriter
        cls.writer = StorageObjectWriter(cls.settings, lambda: cls.tok["machine"])
        cls.home, cls.home_camp = fx.home_services()
        cls.gw = fx.FakeMarketingOs((cls.home, cls.home_camp))
        cls.raw_app = create_app(cls.settings)
        cls.raw_app.mos_gateway = cls.gw
        cls.app = GatewayApp(cls.raw_app, cls.pg.dsn())
        B2.app, B2.tok, B2.users, B2.settings, B2.gw, B2.writer, B2.pg = (cls.app, cls.tok, cls.users, cls.settings,
                                                                       cls.gw, cls.writer, cls.pg)
        acct = B2._ok(call(cls.app, "POST", "/api/v1/accounts", tok=cls.tok["owner_a"], body={"name": "Example Agency"},
                           idem=str(uuid.uuid4())))["account_id"]
        acct_b = B2._ok(call(cls.app, "POST", "/api/v1/accounts", tok=cls.tok["owner_b"], body={"name": "Other"},
                             idem=str(uuid.uuid4())))["account_id"]
        cls.acct = acct
        cls.ws = B2._workspace("owner_a", acct, "Home studio")
        cls.ws_b = B2._workspace("owner_b", acct_b, "Other studio")
        for who, role in (("producer_a", "producer"), ("viewer_a", "viewer")):
            B2._ok(call(cls.app, "PUT", f"/api/v1/workspaces/{cls.ws}/members/{cls.users[who]}", tok=cls.tok["owner_a"],
                        body={"role": role}, idem=str(uuid.uuid4())))
        cls.conn_id = B2._connect("owner_a", cls.ws, cls.home.mos_ws)
        cls.pkg = B2._import("producer_a", cls.ws, cls.conn_id, cls.home)
        cred = Path(cls.tmp / "operator-cred.json")
        cred.write_text("{}")
        wc = engine_higgsfield.WorkspaceCredential(workspace_id="platform", credential_id="operator",
                                                   credentials_path=str(cred))
        with mock.patch.object(engine_higgsfield, "subprocess", SimpleNamespace(run=cls.fake)):
            entries, _ = cat_mod.build_entries(load_source(), credential=wc, allow_fixture=True)
        with cls.pg.connect() as conn:
            cat_mod.publish(conn.cursor(), entries)
        cls.published = {e.entry_id: e for e in entries}

    @classmethod
    def tearDownClass(cls):
        cls.storage.stop()
        cls.pg.stop()

    # -- helpers ------------------------------------------------------------------
    def intake(self, **vr):
        st, b = call(self.app, "POST", f"/api/v1/workspaces/{self.ws}/creative-requests/from-marketing-os",
                     tok=self.tok["producer_a"], idem=str(uuid.uuid4()),
                     body={"connection_id": self.conn_id, "visual_request": self.home_camp.visual_request(**vr)})
        self.assertEqual(st, 201, b)
        return b

    def accept(self, tref):
        st, acc = call(self.app, "POST", f"/api/v1/workspaces/{self.ws}/treatments/{tref['id']}/accept",
                       tok=self.tok["producer_a"], idem=str(uuid.uuid4()), body={"version": tref},
                       headers={"HTTP_IF_MATCH": f'"{tref["sha256"]}"'})
        self.assertEqual(st, 201, acc)
        return acc["treatment"]

    def compile(self, rid, tref, who="producer_a", ws=None, headers=None, tier="draft", app=None):
        return call(app or self.app, "POST", f"/api/v1/workspaces/{ws or self.ws}/creative-requests/{rid}/compile",
                    tok=self.tok[who], body={"treatment": {"id": tref["id"], "version_id": tref["version_id"]},
                                             "tier": tier}, headers=headers)

    def prepared(self, **vr):
        b = self.intake(**vr)
        acc = self.accept(b["treatment"])
        return b, acc

    def q(self, sql, args=()):
        with self.pg.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, args or None)
            return cur.fetchall() if cur.description else []

    def provider_ops(self):
        return len(self.fake.log())

    def set_policy(self, funding):
        st, body = call(self.app, "POST", f"/api/v1/accounts/{self.acct}/budget-policies", tok=self.tok["owner_a"],
                        idem=str(uuid.uuid4()),
                        body={"workspace_id": self.ws, "funding_source": funding, "window_seconds": 86400,
                              "ceiling_micros": 50_000_000, "per_run_ceiling_micros": 10_000_000})
        self.assertEqual(st, 201, body)

    def store_credential(self):
        sealed = KEYRING.seal(CREDENTIAL_PLAINTEXT, provider="higgsfield", fingerprint=f"acct-{self.ws[:8]}")
        conn = psycopg2.connect(self.pg.dsn(user="cs_api"))
        try:
            cur = conn.cursor()
            cur.execute("select set_config('request.jwt.claims', %s, false)", (claims_json(self.users["owner_a"]),))
            cur.execute("select public.cs_store_workspace_credential(%s, 'test', 'higgsfield', %s, %s, %s, %s)",
                        (self.ws, f"acct-{self.ws[:8]}", sealed.ciphertext, sealed.nonce, sealed.key_version))
            conn.commit()
        finally:
            conn.close()

    # -- tests (ordered: later ones use state from earlier ones) ----------------------
    def test_01_migration_applied_in_order_and_idempotent(self):
        self.assertIn(MIGRATION, self.applied)
        self.assertEqual(self.applied, sorted(self.applied))
        self.assertGreater(MIGRATION, "20261031210100_cs_15_service_assertion.sql")
        self.pg.apply_file(MIGRATIONS_DIR / MIGRATION)              # applied twice: no error, no change
        self.pg.apply_file(MIGRATIONS_DIR / MIGRATION)
        for table in ("cs_execution_plans", "cs_execution_plan_supersessions", "cs_plan_quotes"):
            self.assertEqual(self.q("select relrowsecurity and relforcerowsecurity from pg_class where relname = %s",
                                    (table,)), [(True,)])
            conn = psycopg2.connect(self.pg.dsn(user="cs_api"))
            try:
                with self.assertRaises(psycopg2.Error) as c:
                    conn.cursor().execute(f"select * from public.{table}")
                self.assertEqual(c.exception.pgcode, "42501")
            finally:
                conn.close()

    def test_02_compile_produces_immutable_hash_pinned_plan_and_media_pack(self):
        b, acc = self.prepared()
        st, out = self.compile(b["request"]["id"], acc)
        self.assertEqual(st, 201, out)
        plan, pack = out["plan"], out["media_pack"]
        self.assertEqual(out["status"], "current")
        self.assertEqual(pack["plan_sha256"], plan["sha256"])
        self.assertEqual(pack["pinned"]["treatment"]["version_id"], acc["version_id"])
        self.assertEqual(pack["pinned"]["brand_package"]["version_id"], self.pkg["ref"]["version_id"])
        self.assertEqual(pack["quote_status"], "unquoted")
        self.assertIsNone(pack["estimated_credits"])
        c = pack["canvases"][0]
        self.assertTrue(c["prompt"]["positive"] and c["prompt"]["negative"])
        self.assertTrue(pack["text_layers"] and all(not t["model_drawn"] for t in pack["text_layers"]))
        self.assertTrue(pack["brand_elements"]["logos"])
        # replay: same inputs => same plan, not a new row
        st2, again = self.compile(b["request"]["id"], acc)
        self.assertEqual((st2, again["replayed"], again["plan_sha256"]), (200, True, plan["sha256"]))
        self.assertEqual(self.q("select count(*) from public.cs_execution_plans where request_object_id = %s",
                                (b["request"]["id"],)), [(1,)])
        # immutable even for the table owner
        with self.pg.connect() as conn:
            with self.assertRaises(psycopg2.Error):
                conn.cursor().execute("update public.cs_execution_plans set tier = 'final' where plan_id = %s",
                                      (plan["id"],))
        # readable
        st, got = call(self.app, "GET", f"/api/v1/workspaces/{self.ws}/execution-plans/{plan['id']}",
                       tok=self.tok["viewer_a"])
        self.assertEqual((st, got["plan_sha256"]), (200, plan["sha256"]))
        # the compiler makes no provider call
        self.assertEqual(self.fake.creates(), 0)

    def test_03_refusals_and_authority(self):
        b, acc = self.prepared()
        rid = b["request"]["id"]
        st, out = self.compile(rid, b["treatment"])                       # still a draft
        self.assertEqual((st, out["error"]), (409, "treatment_not_frozen"))
        self.assertEqual(self.compile(rid, acc, who="viewer_a")[0], 403)
        self.assertEqual(self.compile(rid, acc, who="owner_b")[0], 404)
        self.assertEqual(self.compile(rid, acc, ws=self.ws_b, who="owner_b")[0], 404)   # isolation
        with mock.patch.object(plan_store, "catalogue", lambda cur: []):
            st, out = self.compile(rid, acc)
        self.assertEqual((st, out["error"]), (409, "catalogue_unavailable"))
        with mock.patch("service.requests.store.get_package", lambda *a: None):
            st, out = self.compile(rid, acc)
        self.assertEqual((st, out["error"]), (409, "brand_package_missing"))
        self.assertEqual(self.q("select count(*) from public.cs_execution_plans where request_object_id = %s", (rid,)),
                         [(0,)])

    def test_04_changed_input_gets_a_new_plan_and_old_one_is_superseded(self):
        b, acc = self.prepared()
        rid = b["request"]["id"]
        first = self.compile(rid, acc)[1]
        st, rq = call(self.app, "GET", f"/api/v1/workspaces/{self.ws}/creative-requests/{rid}/versions/"
                      f"{b['request']['version_id']}", tok=self.tok["producer_a"])
        fields = {k: v for k, v in rq["request"].items() if k in
                  ("origin", "brand_package", "offering", "audiences", "objective", "hook", "locale", "exact_copy",
                   "formats", "references", "provider_selection", "budget", "constraints")}
        fields["exact_copy"][0]["text"] = "A changed headline"
        st, rev = call(self.app, "POST", f"/api/v1/workspaces/{self.ws}/creative-requests/{rid}/revisions",
                       tok=self.tok["producer_a"], idem=str(uuid.uuid4()), body=fields,
                       headers={"HTTP_IF_MATCH": f'"{b["request"]["sha256"]}"'})
        self.assertEqual(st, 201, rev)
        # the old plan is stale the moment the request moves, even before recompiling
        self.assertEqual(self.quote(first["plan_id"])[1]["error"], "plan_stale")
        # the old accepted treatment pins the old request revision -> a new treatment is required
        st, tr = call(self.app, "GET", f"/api/v1/workspaces/{self.ws}/treatments/{acc['id']}/versions/{acc['version_id']}",
                      tok=self.tok["producer_a"])
        tf = {k: v for k, v in tr["treatment"].items() if k in ("source_request", "authorship", "direction", "shots",
                                                                  "sources")}
        tf["source_request"] = {"authority": "creative-studio", "workspace_id": self.ws, "kind": "creative-request",
                                "id": rid, "version_id": rev["version_id"], "revision": 2, "sha256": rev["sha256"]}
        st, nt = call(self.app, "POST", f"/api/v1/workspaces/{self.ws}/treatments", tok=self.tok["producer_a"],
                      idem=str(uuid.uuid4()), body=tf)
        self.assertEqual(st, 201, nt)
        new_acc = self.accept({"id": nt["id"], "version_id": nt["version_id"], "sha256": nt["sha256"],
                               "authority": "creative-studio", "workspace_id": self.ws, "kind": "treatment",
                               "revision": 1})
        st, second = self.compile(rid, new_acc)
        self.assertEqual(st, 201, second)
        self.assertNotEqual(second["plan_sha256"], first["plan_sha256"])
        self.assertEqual(second["revision"], 2)
        self.assertEqual(call(self.app, "GET", f"/api/v1/workspaces/{self.ws}/execution-plans/{first['plan_id']}",
                              tok=self.tok["viewer_a"])[1]["status"], "superseded")
        self.assertEqual(call(self.app, "GET", f"/api/v1/workspaces/{self.ws}/execution-plans/{first['plan_id']}",
                              tok=self.tok["viewer_a"])[1]["plan"]["sha256"], first["plan_sha256"], "old plan intact")
        self.assertEqual(self.quote(first["plan_id"])[1]["error"], "plan_stale")
        self.assertEqual(second["media_pack"]["text_layers"][0]["text"], "A changed headline")

    def quote(self, plan_id, tier="draft", who="producer_a"):
        return call(self.app, "POST", f"/api/v1/workspaces/{self.ws}/quotes", tok=self.tok[who],
                    body={"plan_id": plan_id, "tier": tier})

    def admit(self, plan_id, tier="draft", idem=None, who="producer_a"):
        return call(self.app, "POST", f"/api/v1/workspaces/{self.ws}/runs", tok=self.tok[who],
                    body={"plan_id": plan_id, "tier": tier}, idem=idem or str(uuid.uuid4()))

    def test_05_quote_unavailable_without_credential_then_quoted(self):
        b, acc = self.prepared()
        plan = self.compile(b["request"]["id"], acc)[1]
        funding = plan["plan"]["admission"]["budget"]["funding_source"]
        before = self.provider_ops()
        st, q = self.quote(plan["plan_id"])
        self.assertEqual((st, q["status"], q["reason"]), (200, "unavailable", "provider_credentials_missing"))
        self.assertIsNone(q["credits"])
        self.assertIsNone(q["usd"])
        self.assertEqual(self.admit(plan["plan_id"])[1]["error"], "quote_required")
        self.assertEqual(funding, "workspace", "fixture request funds from the workspace credential")
        self.store_credential()
        st, q = self.quote(plan["plan_id"])
        self.assertEqual((st, q["status"]), (200, "quoted"), q)
        entry = self.published["hf-ms-headline-" + "0f6c2a7e" + "-low"]
        self.assertEqual(q["maximum_micros"], entry.unit_cost_micros)
        self.assertEqual(q["certainty"], "catalogue_dry_run_snapshot")
        self.assertEqual(q["credits"], str(int(float(entry.price_snapshot["quoted_credits"])))
                         if float(entry.price_snapshot["quoted_credits"]).is_integer()
                         else entry.price_snapshot["quoted_credits"])
        self.assertEqual(q["usd"], format(__import__("decimal").Decimal(entry.unit_cost_micros) / 1_000_000, "f"))
        self.assertEqual(self.provider_ops(), before, "quoting spends nothing and makes no provider call")
        self.assertEqual(self.q("select count(*) from public.cs_runs"), [(0,)])
        self.assertEqual(self.quote(plan["plan_id"], tier="final")[0] in (409, 422), True, "tier mismatch is refused")
        self.assertEqual(self.quote(plan["plan_id"], who="viewer_a")[0], 403)

    def test_06_admission_gated_by_spend_switch_and_replay_idempotent(self):
        b, acc = self.prepared()
        plan = self.compile(b["request"]["id"], acc)[1]
        self.set_policy("workspace")
        self.assertEqual(self.quote(plan["plan_id"])[1]["status"], "quoted")
        before = self.provider_ops()
        st, out = self.admit(plan["plan_id"])
        self.assertEqual((st, out["error"]), (503, "paid_provider_disabled"))
        self.assertEqual(self.provider_ops(), before)
        self.assertEqual(self.q("select count(*) from public.cs_runs"), [(0,)])
        with self.pg.connect() as conn:
            conn.cursor().execute("update cs_private.cs_runtime_limits set paid_provider_enabled = true "
                                  "where environment_id = 'test'")
        try:
            key = str(uuid.uuid4())
            st, run = self.admit(plan["plan_id"], idem=key)
            self.assertEqual(st, 202, run)
            self.assertEqual(run["state"], "queued")
            st2, again = self.admit(plan["plan_id"], idem=key)
            self.assertEqual((st2, again["run_id"], again["replayed"]), (200, run["run_id"], True))
            self.assertEqual(self.q("select count(*) from public.cs_runs"), [(1,)])
            self.assertEqual(self.provider_ops(), before, "admission reserves budget; no provider call yet")
            self.assertEqual(self.q("select plan_version_id::text from public.cs_runs")[0][0],
                             plan["plan"]["version_id"])
        finally:
            with self.pg.connect() as conn:
                conn.cursor().execute("update cs_private.cs_runtime_limits set paid_provider_enabled = false "
                                      "where environment_id = 'test'")

    def test_07_gateway_assertions_on_compile_quote_admit(self):
        b, acc = self.prepared()
        rid = b["request"]["id"]
        path = f"/api/v1/workspaces/{self.ws}/creative-requests/{rid}/compile"
        body = json.dumps({"treatment": {"id": acc["id"], "version_id": acc["version_id"]}, "tier": "draft"})

        def sign(action, raw=body, **kw):
            return header_for(user_id=self.users["producer_a"], action=action, binding=self.home.mos_ws,
                              body=raw.encode(), **kw)

        def go(h):
            environ_body = json.loads(body)
            return call(self.raw_app, "POST", path, tok=self.tok["producer_a"], body=environ_body,
                        headers={assertion.HEADER_ENV_KEY: h} if h else None)
        # call() serialises with json.dumps default separators, so sign exactly those bytes
        raw = json.dumps(json.loads(body))
        self.assertEqual(go(sign("plan.compile", raw=raw))[0], 201)
        h = sign("plan.compile", raw=raw)
        self.assertEqual(go(h)[0], 200)
        self.assertEqual(go(h)[1], {"error": "assertion_replayed"})
        self.assertEqual(go(sign("quote.create", raw=raw))[1], {"error": "assertion_invalid"})
        self.assertEqual(go(sign("plan.compile", raw="{}"))[1], {"error": "assertion_invalid"})
        self.assertEqual(go(None)[0], 200, "native membership call needs no assertion")
        # quote / run refuse the gateway without an assertion (raw app, no signing wrapper)
        for p in ("quotes", "runs"):
            st, out = call(self.raw_app, "POST", f"/api/v1/workspaces/{self.ws}/{p}", tok=self.tok["producer_a"],
                           body={"plan_id": str(uuid.uuid4()), "tier": "draft"}, idem=str(uuid.uuid4()))
            self.assertEqual((st, out), (401, {"error": "assertion_missing"}))

    def test_08_workspace_isolation_of_quotes_and_runs(self):
        b, acc = self.prepared()
        plan = self.compile(b["request"]["id"], acc)[1]
        st, out = call(self.app, "POST", f"/api/v1/workspaces/{self.ws_b}/quotes", tok=self.tok["owner_b"],
                       body={"plan_id": plan["plan_id"], "tier": "draft"})
        self.assertIn(st, (403, 404), out)
        self.assertEqual(call(self.app, "GET", f"/api/v1/workspaces/{self.ws_b}/execution-plans/{plan['plan_id']}",
                              tok=self.tok["owner_b"])[0], 404)


if __name__ == "__main__":
    unittest.main()
