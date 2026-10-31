"""CS-B3 / NS-10805 / PT-9641 real PostgreSQL 17 acceptance suite.

Every migration in supabase/migrations is applied in filename order on a
private throwaway PG17 cluster (B1 harness). The real service code runs as the
restricted cs_api / cs_worker logins. The provider transport is mocked at the
adapter's process boundary (FakeHiggsfield replaces engine_higgsfield's
subprocess.run); no network, no provider, no production.

Accept (CS-B3): over-budget refused; worker killed mid-run resumes without
double charge; duplicate dispatch idempotent; cancel stops further attempts;
receipts reconcile to reservations; final tier refused without approved draft.
Plus ENG-03 §5: anon denied, authenticated direct write denied, workspace B
cannot read/act on A, spoofed actor ignored, idempotent migration re-run.

Run:  python3 -m pytest tests/creative_studio/test_b3_real_db.py -v
"""
from __future__ import annotations

import dataclasses

from tests.creative_studio.assertion_support import KEY, GatewayApp

import base64
import json
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import psycopg2
import psycopg2.extras

from service.providers.adapter import engine_higgsfield
from service.api import create_app
from service.config import Settings
from service.providers import catalogue as cat_mod
from service.providers.contracts import validate
from service import jcs
from service.providers.credentials import CredentialError, KeyRing, SealedCredential
from service.runtime.worker import ProviderRuntime
from tests.creative_studio.b3_support import (CREDENTIAL_PLAINTEXT, HEADLINE, ROOT, TEST_KEYS, DirStore, FakeFetcher,
                                              FakeHiggsfield, asset_input, load_source, make_plan,
                                              node_output_input, provider_node)
from tests.creative_studio.pg_harness import MIGRATIONS_DIR, ThrowawayCluster, claims_json
from tests.creative_studio.test_cs_b1_real_db import ISSUER, JWT_SECRET, call, token

B3_MIGRATION = MIGRATIONS_DIR / "20261031140300_cs_b3_provider_execution.sql"
KEYRING = KeyRing({k: base64.b64decode(v) for k, v in TEST_KEYS.items()})


class CsB3RealDatabase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pg = ThrowawayCluster()
        cls.pg.init()
        cls.applied = cls.pg.install()
        cls.tmp = Path(tempfile.mkdtemp(prefix="cs_b3_"))
        cls.fake = FakeHiggsfield(cls.tmp / "provider")
        cls.store = DirStore(cls.tmp / "store")
        cls.settings = Settings(
            api_database_url=cls.pg.dsn(user="cs_api"), worker_database_url=cls.pg.dsn(user="cs_worker"),
            environment_id="test", release_id="test-release", jwt_issuer=ISSUER, jwt_audience="authenticated",
            jwt_secret=JWT_SECRET, jwks_url=None, storage_url="http://127.0.0.1:9/storage/v1",
            storage_anon_key="anon-public-test-key", signed_download_seconds=300, download_grant_seconds=60,
            worker_secret="worker-test-secret", runtime_principal="cs-worker:test")
        cls.app = GatewayApp(create_app(dataclasses.replace(cls.settings, mos_assertion_key=KEY)), cls.pg.dsn())
        cls.fetcher = FakeFetcher(cls.tmp / "provider")
        cls.runtime = ProviderRuntime(cls.settings, cls.store, cls.fetcher, KEYRING)
        cls.users = {}
        with cls.pg.connect() as conn:
            cur = conn.cursor()
            cur.execute("update cs_private.cs_runtime_limits set admission_enabled = true, paid_provider_enabled = true, active_run_limit = 6 "
                        "where environment_id = 'test'")
            for name in ("owner_a", "producer_a", "producer2_a", "reviewer_a", "viewer_a", "owner_b", "outsider"):
                uid = str(uuid.uuid4())
                cur.execute("insert into auth.users (id, email) values (%s, %s)", (uid, f"{name}@example-studio.test"))
                cls.users[name] = uid
        cls.tok = {k: token(v) for k, v in cls.users.items()}
        cls.acct_a, cls.ws_a = cls._bootstrap("owner_a", "Example Home Services Co")
        cls.acct_b, cls.ws_b = cls._bootstrap("owner_b", "Example Bakery Ltd")
        for who, role in (("producer_a", "producer"), ("producer2_a", "producer"), ("reviewer_a", "reviewer"),
                          ("viewer_a", "viewer")):
            st, body = call(cls.app, "PUT", f"/api/v1/workspaces/{cls.ws_a}/members/{cls.users[who]}",
                            tok=cls.tok["owner_a"], body={"role": role}, idem=str(uuid.uuid4()))
            assert st == 200, (st, body)
        # catalogue: priced by the adapter's own cost quote through the mocked transport
        cls.entries, cls.refusals = cls._build_catalogue()
        with cls.pg.connect() as conn:
            cls.catalogue_ids = cat_mod.publish(conn.cursor(), cls.entries)
        cls.entry = {e.entry_id: e.entry for e in cls.entries}
        cls.low = cls.entry[f"hf-ms-headline-{HEADLINE[:8]}-low"]
        cls.high = cls.entry[f"hf-ms-headline-{HEADLINE[:8]}-high"]
        cls.classic = cls.entry["hf-example-image-draft"]
        cls._store_credential("owner_a", cls.ws_a)
        cls._store_credential("owner_b", cls.ws_b)

    @classmethod
    def tearDownClass(cls):
        cls.pg.stop()

    # -- helpers -------------------------------------------------------------------
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
    def _patched(cls):
        return mock.patch.object(engine_higgsfield, "subprocess", SimpleNamespace(run=cls.fake))

    @classmethod
    def _build_catalogue(cls):
        cred_file = cls.tmp / "operator-cred.json"
        cred_file.write_text("{}")
        cred = engine_higgsfield.WorkspaceCredential(workspace_id="platform", credential_id="operator",
                                                     credentials_path=str(cred_file))
        with cls._patched():
            return cat_mod.build_entries(load_source(), credential=cred, allow_fixture=True)

    @classmethod
    def _store_credential(cls, who, ws):
        sealed = KEYRING.seal(CREDENTIAL_PLAINTEXT, provider="higgsfield", fingerprint=f"acct-{ws[:8]}")
        conn, cur = cls._sql_as("cs_api", cls.users[who])
        try:
            cur.execute("select public.cs_store_workspace_credential(%s, 'test', 'higgsfield', %s, %s, %s, %s)",
                        (ws, f"acct-{ws[:8]}", sealed.ciphertext, sealed.nonce, sealed.key_version))
            conn.commit()
            return cur.fetchone()[0]
        finally:
            conn.close()

    @classmethod
    def _sql_as(cls, role, claims_user=None):
        conn = psycopg2.connect(cls.pg.dsn(user=role if role in ("cs_api", "cs_worker") else None))
        cur = conn.cursor()
        if role not in ("cs_api", "cs_worker"):
            cur.execute(f"set role {role}")
        if claims_user:
            cur.execute("select set_config('request.jwt.claims', %s, false)", (claims_json(claims_user),))
        conn.commit()
        return conn, cur

    def q(self, sql, args=()):
        with self.pg.connect() as conn:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(sql, args or None)
            return [dict(r) for r in cur.fetchall()] if cur.description else []

    def assertSqlState(self, state, fn):
        with self.assertRaises(psycopg2.Error) as ctx:
            fn()
        self.assertEqual(ctx.exception.pgcode, state, ctx.exception.pgerror)

    def set_policy(self, who, acct, ws, ceiling, per_run, funding="workspace"):
        st, body = call(self.app, "POST", f"/api/v1/accounts/{acct}/budget-policies", tok=self.tok[who],
                        idem=str(uuid.uuid4()),
                        body={"workspace_id": ws, "funding_source": funding, "window_seconds": 86400,
                              "ceiling_micros": ceiling, "per_run_ceiling_micros": per_run})
        self.assertEqual(st, 201, body)
        return body

    def admit(self, who, ws, nodes, tier="draft", approval=None, idem=None, extra=None, plan=None):
        plan = plan or make_plan(ws, nodes, actor=self.users[who])
        body = {"plan": plan, "tier": tier, "draft_approval_id": approval}
        body.update(extra or {})
        return call(self.app, "POST", f"/api/v1/workspaces/{ws}/runs", tok=self.tok[who], body=body,
                    idem=idem or str(uuid.uuid4())) + (plan,)

    def drain(self, limit=20):
        out = []
        with self._patched():
            for _ in range(limit):
                r = self.runtime.run_once()
                out.append(r)
                if r["claimed"] == 0 and not r.get("recovered"):
                    break
        return out

    def run_state(self, run_id):
        return self.q("select state::text, cost_state::text from public.cs_runs where id = %s", (run_id,))[0]

    def reservation(self, run_id, node="n1"):
        return self.q("select * from public.cs_budget_reservations where run_id = %s and node_id = %s",
                      (run_id, node))[0]

    def ledger(self, run_id):
        return self.q("select entry_type, amount_micros, provider_units::text as units from public.cs_cost_ledger "
                      "where run_id = %s order by created_at, id", (run_id,))

    def expire_leases(self, run_id):
        self.q("update public.cs_work_items set lease_until = now() - interval '1 second' "
               "where run_id = %s and state = 'leased'", (run_id,))

    def spawn_worker(self, *, hang_fetch=False):
        cfg = json.dumps({"dsn": self.pg.dsn(user="cs_worker"), "state_dir": str(self.tmp / "provider"),
                          "store_dir": str(self.tmp / "store"), "hang_fetch": hang_fetch})
        return subprocess.Popen([sys.executable, str(ROOT / "tests" / "creative_studio" / "b3_worker_proc.py"), cfg],
                                cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def wait_for(self, path: Path, proc, seconds=30):
        deadline = time.time() + seconds
        while time.time() < deadline:
            if path.exists():
                return
            if proc.poll() is not None:
                self.fail(f"worker exited early: {proc.stdout.read()} {proc.stderr.read()}")
            time.sleep(0.05)
        self.fail(f"worker never reached {path.name}")

    # -- 00 catalogue from data -------------------------------------------------------
    def test_00_catalogue_prices_come_from_adapter_cost_quote(self):
        self.assertEqual(self.refusals, [])
        # fixture cost quotes: dtc low = 1 credit, high = 6.5, classic = 2.5; fixture credit price 50 000 µUSD
        self.assertEqual(self.low["pricing"]["unit_cost_micros"], 50_000)
        self.assertEqual(self.high["pricing"]["unit_cost_micros"], 325_000)
        self.assertEqual(self.classic["pricing"]["unit_cost_micros"], 125_000)
        rows = self.q("select entry_id, tier::text, price_snapshot ->> 'quoted_credits' as credits, "
                      "price_snapshot ->> 'quote_source' as src from public.cs_provider_catalogue_versions order by 1")
        self.assertEqual({r["entry_id"]: (r["tier"], r["credits"]) for r in rows},
                         {f"hf-ms-headline-{HEADLINE[:8]}-high": ("final", "6.5"),
                          f"hf-ms-headline-{HEADLINE[:8]}-low": ("draft", "1"),
                          "hf-example-image-draft": ("draft", "2.5")})
        for e in self.entries:
            validate("provider-catalogue-v1.schema.json",
                     {"schema_version": "cs-provider-catalogue-v1", "id": str(uuid.uuid4()),
                      "version_id": str(uuid.uuid4()), "revision": 1, "workspace_id": self.ws_a,
                      "created_at": "2026-10-31T00:00:00Z", "created_by": self.users["owner_a"],
                      "sha256": "0" * 64, "entries": [e.entry]})
        self.assertNotIn("dtc.create", self.fake.ops())          # quoting never creates a job
        self.assertNotIn("generate.create", self.fake.ops())
        with self.pg.connect() as conn:                           # publish is idempotent
            self.assertEqual(cat_mod.publish(conn.cursor(), self.entries), self.catalogue_ids)
        with self.assertRaises(cat_mod.CatalogueSourceError):    # fixture prices never publish outside tests
            cat_mod.build_entries(load_source(), credential=None)

    # -- 01 privileges -----------------------------------------------------------------------
    def test_01_roles_and_direct_access_are_denied(self):
        conn, cur = self._sql_as("anon")
        try:
            for t in ("public.cs_attempts", "public.cs_provider_receipts", "public.cs_cost_ledger",
                      "public.cs_budget_reservations", "cs_private.cs_credentials"):
                self.assertSqlState("42501", lambda: cur.execute(f"select 1 from {t}"))
                conn.rollback()
        finally:
            conn.close()
        conn, cur = self._sql_as("authenticated", self.users["owner_a"])
        try:
            self.assertSqlState("42501", lambda: cur.execute(
                "insert into public.cs_cost_ledger (workspace_id, account_id, reservation_id, run_id, node_id, "
                "entry_type, amount_micros, source_event_key) values (%s, %s, gen_random_uuid(), gen_random_uuid(), "
                "'n1', 'settle', 0, 'x')", (self.ws_a, self.acct_a)))
            conn.rollback()
            self.assertSqlState("42501", lambda: cur.execute(
                "select public.cs_request_cancel(%s, gen_random_uuid(), 'x')", (self.ws_a,)))
            conn.rollback()
        finally:
            conn.close()
        conn, cur = self._sql_as("cs_api", self.users["owner_a"])
        try:
            for stmt in ("select public.cs_prepare_attempt(gen_random_uuid(), gen_random_uuid(), 1, 'x')",
                         "select public.cs_recover_expired_leases('test', 1, 'r')",
                         "select public.cs_reconcile_attempt(gen_random_uuid(), gen_random_uuid(), 0, null, 'x')",
                         "select 1 from cs_private.cs_credentials",
                         "insert into public.cs_budget_reservations (workspace_id, account_id, run_id, node_id, "
                         "policy_id, ceiling_micros) values (gen_random_uuid(), gen_random_uuid(), gen_random_uuid(), "
                         "'n', gen_random_uuid(), 1)"):
                self.assertSqlState("42501", lambda: cur.execute(stmt))
                conn.rollback()
        finally:
            conn.close()
        conn, cur = self._sql_as("cs_worker")
        try:
            for stmt in ("select public.cs_admit_run(gen_random_uuid(), 'test', 'r', gen_random_uuid(), 'x', 'x', 1, "
                         "'[]', 'draft', null, gen_random_uuid(), 'x')",
                         "select 1 from cs_private.cs_credentials",
                         "select public.cs_publish_catalogue_entry('a','b','c','d','generate','image_generate','draft',"
                         "true,'{workspace}','{}','{}','{}',1,1,now(),now(),'x')",
                         "update public.cs_cost_ledger set amount_micros = 0",
                         "select public.cs_set_budget_policy(gen_random_uuid(), null, 'workspace', 1, 1, 1, null, "
                         "gen_random_uuid(), 'x')"):
                self.assertSqlState("42501", lambda: cur.execute(stmt))
                conn.rollback()
        finally:
            conn.close()
        exposed = self.q("""select p.oid::regprocedure::text as fn from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                             where (n.nspname = 'cs_private' or (n.nspname = 'public' and p.proname like 'cs\\_%%'))
                               and p.proname <> 'cs_storage_allowed'
                               and (has_function_privilege('anon', p.oid, 'execute')
                                    or has_function_privilege('authenticated', p.oid, 'execute'))""")
        self.assertEqual(exposed, [])
        loose = self.q("""select p.proname from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                           where p.prosecdef and (n.nspname = 'cs_private' or p.proname like 'cs\\_%%')
                             and not exists (select 1 from unnest(coalesce(p.proconfig, '{}')) c
                                              where c = 'search_path=public, pg_temp')""")
        self.assertEqual(loose, [])
        # immutable evidence, even for the owner
        self.assertSqlState("CS409", lambda: self.q("update public.cs_provider_catalogue_versions set enabled = false"))

    # -- 02 over-budget refused -----------------------------------------------------------------
    def test_02_over_budget_is_refused_with_a_typed_refusal(self):
        node = provider_node("n1", self.low, prompt="Generic square promo, warm light")
        st, body, _ = self.admit("producer_a", self.ws_a, [node])
        self.assertEqual((st, body["error"]), (422, "budget_not_approved"))      # zero/no policy = refused
        st, body = call(self.app, "POST", f"/api/v1/accounts/{self.acct_a}/budget-policies", tok=self.tok["producer_a"],
                        idem=str(uuid.uuid4()), body={"workspace_id": self.ws_a, "funding_source": "workspace",
                                                      "window_seconds": 86400, "ceiling_micros": 10**9,
                                                      "per_run_ceiling_micros": 10**9})
        self.assertEqual(st, 404)                                                # only account admins set budgets
        self.set_policy("owner_a", self.acct_a, self.ws_a, ceiling=2_000_000, per_run=800_000)
        big = provider_node("n1", self.high, prompt="x", quality="high", batch=2, maximum=900_000)
        before = self.q("select count(*) as n from public.cs_runs")[0]["n"]
        st, body, _ = self.admit("producer_a", self.ws_a, [big], tier="final", approval=None)
        self.assertEqual((st, body["error"]), (422, "draft_approval_required"))
        big_draft = provider_node("n1", self.classic, prompt="x", batch=4, maximum=900_000)
        st, body, _ = self.admit("producer_a", self.ws_a, [big_draft])
        self.assertEqual((st, body["error"]), (422, "budget_exceeded"))          # per-run ceiling
        under = provider_node("n1", self.low, prompt="x", maximum=10)            # plan below current quote
        st, body, _ = self.admit("producer_a", self.ws_a, [under])
        self.assertEqual((st, body["error"]), (422, "plan_cost_below_quote"))
        unknown = dict(provider_node("n1", self.low, prompt="x"))
        unknown["provider_call"] = dict(unknown["provider_call"], catalogue_entry_id="hf-not-in-catalogue")
        st, body, _ = self.admit("producer_a", self.ws_a, [unknown])
        self.assertEqual((st, body["error"]), (422, "price_unknown"))
        self.assertEqual(self.q("select count(*) as n from public.cs_runs")[0]["n"], before)   # no run on refusal

        # rolling window on account B: holds count until settled; unknown is never zero
        self.set_policy("owner_b", self.acct_b, self.ws_b, ceiling=150_000, per_run=150_000)
        n = provider_node("n1", self.low, prompt="Bakery window display", maximum=100_000)
        st, first, _ = self.admit("owner_b", self.ws_b, [n])
        self.assertEqual((st, first["reserved_micros"]), (202, 100_000))
        st, body, _ = self.admit("owner_b", self.ws_b, [n])
        self.assertEqual((st, body["error"]), (422, "budget_exceeded"))          # 100k held + 100k > 150k
        self.assertIn("available 50000", body.get("detail", "") or self._last_refusal_detail(n))
        self.drain()
        self.assertEqual(self.run_state(first["run_id"])["state"], "completed")
        self.assertEqual(self.reservation(first["run_id"])["settled_micros"], 50_000)
        st, second, _ = self.admit("owner_b", self.ws_b, [provider_node("n1", self.low, prompt="y", maximum=100_000)])
        self.assertEqual(st, 202, second)                                        # 50k settled + 100k <= 150k
        self.drain()
        st, quote = call(self.app, "POST", f"/api/v1/workspaces/{self.ws_b}/quotes", tok=self.tok["owner_b"],
                         body={"plan": make_plan(self.ws_b, [n]), "tier": "draft"})
        self.assertEqual((st, quote["maximum_micros"], quote["budget"]["available_micros"]), (200, 100_000, 50_000))

    def _last_refusal_detail(self, node):
        conn, cur = self._sql_as("cs_api", self.users["owner_b"])
        try:
            cur.execute("select public.cs_admit_run(%s, 'test', 'r', gen_random_uuid(), %s, %s, 10000000, %s::jsonb, "
                        "'draft', null, gen_random_uuid(), %s)",
                        (self.ws_b, "a" * 64, "b" * 64, json.dumps([{"node_id": "n1", "dependencies": [], "inputs": [],
                         "provider_call": node["provider_call"], "estimated_cost_micros": 50_000,
                         "maximum_cost_micros": 100_000}]), "c" * 64))
        except psycopg2.Error as exc:
            return exc.diag.message_primary
        finally:
            conn.close()
        return ""

    # -- 03 happy path: draft run, receipts, settlement, export ----------------------------------
    def test_03_draft_run_settles_against_its_reservation(self):
        node = provider_node("n1", self.low, prompt="Generic square promo, warm light")
        st, run, plan = self.admit("producer_a", self.ws_a, [node], extra={"actor_id": self.users["owner_a"]})
        self.assertEqual(st, 202, run)
        creates = self.fake.creates()
        steps = self.drain()
        self.assertEqual([s.get("status") for s in steps if s["claimed"]], ["provider_completed", "ingested"])
        self.assertEqual(self.fake.creates(), creates + 1)
        self.assertEqual(self.run_state(run["run_id"]), {"state": "completed", "cost_state": "settled"})
        # spoofed actor in the body is ignored: requester is the verified token subject
        self.assertEqual(self.q("select requested_by::text from public.cs_runs where id = %s", (run["run_id"],))[0]
                         ["requested_by"], self.users["producer_a"])
        res = self.reservation(run["run_id"])
        self.assertEqual((res["state"], res["ceiling_micros"], res["settled_micros"]), ("settled", 100_000, 50_000))
        self.assertEqual([(l["entry_type"], l["amount_micros"]) for l in self.ledger(run["run_id"])],
                         [("reserve", 100_000), ("settle", 50_000), ("release", 50_000)])
        st, costs = call(self.app, "GET", f"/api/v1/workspaces/{self.ws_a}/runs/{run['run_id']}/costs",
                         tok=self.tok["viewer_a"])
        self.assertEqual(st, 200, costs)
        self.assertEqual({k: costs["cost"][k] for k in ("state", "reserved_micros", "settled_micros", "unresolved_micros")},
                         {"state": "settled", "reserved_micros": 100_000, "settled_micros": 50_000, "unresolved_micros": 0})
        kinds = sorted(r["kind"] for r in costs["receipts"])
        self.assertEqual(kinds, ["billing", "output"])
        billing = [r for r in costs["receipts"] if r["kind"] == "billing"][0]
        self.assertEqual(billing["charge"]["amount_micros"], 50_000)
        self.assertEqual(billing["charge"]["evidence"], "provider_billing")
        self.assertIn("certainty=balance_delta", billing["note"])
        self.assertEqual(costs["ledger_lines"][0]["provider_units"], "1.0")
        output = [r for r in costs["receipts"] if r["kind"] == "output"][0]
        self.assertEqual(len(output["output_assets"]), 1)
        for r in costs["receipts"]:
            validate("provider-receipt-v1.schema.json", r)
        events = [e["kind"] for e in self.q("select kind from public.cs_run_events where run_id = %s order by sequence",
                                            (run["run_id"],))]
        for kind in ("run.admitted", "attempt.prepared", "attempt.dispatched", "attempt.provider_bound",
                     "cost.reconciled", "asset.persisted", "run.state_changed"):
            self.assertIn(kind, events)
        self.assertLess(events.index("attempt.dispatched"), events.index("attempt.provider_bound"))
        # CS-INT F5: the frozen transport payload is encrypted at rest and bound to its attempt
        row = self.q("select p.* from cs_private.cs_attempt_payloads p join public.cs_attempts a "
                     "on a.id = p.attempt_id where a.run_id = %s", (run["run_id"],))[0]
        self.assertNotIn("envelope", row)
        self.assertNotIn(b"Generic square promo", bytes(row["ciphertext"]))
        sealed = SealedCredential(bytes(row["ciphertext"]), bytes(row["nonce"]), row["encryption_key_version"])
        payload = KEYRING.open_envelope(sealed, workspace_id=str(row["workspace_id"]),
                                        attempt_id=str(row["attempt_id"]), envelope_sha256=row["envelope_sha256"])
        validate("dispatch-envelope-v1.schema.json", payload)
        self.assertEqual((payload["sha256"], payload["dispatch_sha256"]), (row["envelope_sha256"], row["dispatch_sha256"]))
        self.assertIn("Generic square promo", json.dumps(payload))
        with self.assertRaises(CredentialError):                    # cannot be moved to another attempt
            KEYRING.open_envelope(sealed, workspace_id=str(row["workspace_id"]), attempt_id=str(uuid.uuid4()),
                                  envelope_sha256=row["envelope_sha256"])
        # CS-INT F5: node_input_sha256 is SHA-256 of the RFC 8785 preimage (contract.md §3), recomputed here
        att = self.q("select a.workspace_id, a.node_id, a.node_input_sha256, adm.compile_hash, s.provider_call "
                     "from public.cs_attempts a join public.cs_run_admissions adm on adm.run_id = a.run_id "
                     "join public.cs_run_node_specs s on s.run_id = a.run_id and s.node_id = a.node_id "
                     "where a.run_id = %s", (run["run_id"],))[0]
        inputs = self.q("select cs_private.cs_resolve_inputs(%s, %s, %s) as i",
                        (att["workspace_id"], run["run_id"], att["node_id"]))[0]["i"]
        self.assertEqual(att["node_input_sha256"], jcs.jcs_sha256({
            "compile_hash": att["compile_hash"], "node_id": att["node_id"], "inputs": inputs,
            "provider_call": att["provider_call"]}))
        self.__class__.draft_run = run["run_id"]

    # -- 03b CS-INT2: the kick can never start paid provider work on its own ---------------------------
    def test_03b_kick_runs_free_work_but_paid_stage_needs_admission_and_the_spend_switch(self):
        from tests.creative_studio.test_cs12_real_db import CRON_STUB, NET_STUB, VAULT_STUB
        self.q(NET_STUB + VAULT_STUB + CRON_STUB)
        self.q("insert into vault.decrypted_secrets values ('cs_worker_cron_secret', 'unit-test-cron-secret') "
               "on conflict (name) do nothing")
        for k, v in (("worker_kick_enabled", "true"), ("worker_base_url", "https://cs.example.test"),
                     ("worker_kick_window_seconds", "0")):
            self.q("insert into cs_private.cs_platform_settings (key, value) values (%s, %s) "
                   "on conflict (key) do update set value = excluded.value", (k, v))
        self.q("update cs_private.cs_runtime_limits set dispatch_enabled = true, paid_provider_enabled = false "
               "where environment_id = 'test'")
        self.q("truncate net._http_request_queue")
        try:
            # --- explicit admission (the Produce click) with the spend switch OFF (the production value)
            node = provider_node("n1", self.low, prompt="Generic square promo, warm light")
            st, run, _ = self.admit("producer_a", self.ws_a, [node])
            self.assertEqual(st, 202, run)
            rid = run["run_id"]
            self.assertGreaterEqual(self.q("select count(*) as n from net._http_request_queue")[0]["n"], 1,
                                    "enqueue must fire the kick")
            # --- a free stage in the same environment: the kicked worker runs it
            free = self.q("""with r as (insert into public.cs_runs (workspace_id, environment_id, release_id)
                                        values (%s, 'test', 'r') returning id),
                                  n as (insert into public.cs_run_nodes (workspace_id, run_id, node_id, state)
                                        select %s, id, 'n1', 'queued' from r returning run_id)
                             insert into public.cs_work_items (workspace_id, environment_id, run_id, node_id, stage,
                                                              dedupe_key, due_at)
                             select %s, 'test', run_id, 'n1', 'render_thumbnail', 'free-' || gen_random_uuid(), now()
                               from n returning id::text as id, run_id::text as run_id""",
                          (self.ws_a, self.ws_a, self.ws_a))[0]
            all_stages = ("{compile,prepare_reference,provider_submit,provider_reconcile_submission,provider_poll,"
                          "ingest_original,compose_image,render_thumbnail,author_treatment}")
            conn, cur = self._sql_as("cs_worker")
            try:
                cur.execute("select stage::text, work_item_id::text from public.cs_claim_work('test', 'cs-worker:test', "
                            "%s::public.cs_work_stage[], 1)", (all_stages,))
                claimed = cur.fetchall()
                conn.commit()
            finally:
                conn.close()
            self.assertEqual([c[0] for c in claimed], ["render_thumbnail"])          # free ran; paid not leased
            self.q("update public.cs_work_items set state='cancelled', lease_token=null, lease_until=null, "
                   "lease_owner=null where id = %s", (free["id"],))
            # --- the real worker finds the paid stage and refuses to start it
            creates = self.fake.creates()
            steps = self.drain()
            self.assertEqual([s["claimed"] for s in steps], [0])
            self.assertEqual(self.fake.creates(), creates)                           # provider never called
            item = self.q("select state::text, attempt_count from public.cs_work_items "
                          "where run_id = %s and stage = 'provider_submit'", (rid,))[0]
            self.assertEqual((item["state"], item["attempt_count"]), ("queued", 0))
            self.assertEqual(self.q("select count(*) as n from public.cs_attempts where run_id = %s", (rid,))[0]["n"], 0)
            self.assertEqual(self.reservation(rid)["state"], "reserved")
            # --- defence in depth: even an already-leased item cannot prepare an attempt or dispatch
            self.q("update cs_private.cs_runtime_limits set paid_provider_enabled = true where environment_id='test'")
            conn, cur = self._sql_as("cs_worker")
            try:
                cur.execute("select work_item_id, lease_token, fencing_token from public.cs_claim_work("
                            "'test', 'cs-worker:test', '{provider_submit}'::public.cs_work_stage[], 1)")
                wid, lease, fence = cur.fetchone()
                conn.commit()
                self.q("update cs_private.cs_runtime_limits set paid_provider_enabled = false where environment_id='test'")
                with self.assertRaises(psycopg2.Error) as ctx:
                    cur.execute("select * from public.cs_prepare_attempt(%s, %s, %s, 'adapter-test')", (wid, lease, fence))
                conn.rollback()
                self.assertEqual(ctx.exception.pgcode, "CS503")
                self.assertIn("paid_provider_disabled", ctx.exception.pgerror)
            finally:
                conn.close()
            self.assertEqual(self.q("select count(*) as n from public.cs_attempts where run_id = %s", (rid,))[0]["n"], 0)
            # --- operator flips the single spend switch: the SAME admitted run now proceeds and settles
            self.q("update cs_private.cs_runtime_limits set paid_provider_enabled = true where environment_id='test'")
            self.q("update public.cs_work_items set state='queued', lease_token=null, lease_until=null, "
                   "lease_owner=null, attempt_count=0 where id = %s", (wid,))
            self.drain()
            self.assertEqual(self.fake.creates(), creates + 1)
            self.assertEqual(self.run_state(rid), {"state": "completed", "cost_state": "settled"})
        finally:
            self.q("update cs_private.cs_runtime_limits set paid_provider_enabled = true where environment_id='test'")
            self.q("update cs_private.cs_platform_settings set value='false' where key='worker_kick_enabled'")

    # -- 04 duplicate dispatch idempotent --------------------------------------------------------------
    def test_04_duplicate_dispatch_is_idempotent(self):
        node = provider_node("n1", self.low, prompt="Duplicate safety check")
        key = str(uuid.uuid4())
        plan = make_plan(self.ws_a, [node])
        st, run, _ = self.admit("producer_a", self.ws_a, [node], idem=key, plan=plan)
        self.assertEqual(st, 202)
        st, again, _ = self.admit("producer_a", self.ws_a, [node], idem=key, plan=plan)
        self.assertEqual((st, again["run_id"], again["replayed"]), (200, run["run_id"], True))
        self.assertEqual(len(self.q("select 1 from public.cs_budget_reservations where run_id = %s", (run["run_id"],))), 1)
        st, conflict, _ = self.admit("producer_a", self.ws_a, [node], idem=key)   # same key, different body
        self.assertEqual((st, conflict["error"]), (409, "idempotency_conflict"))

        creates = self.fake.creates()
        self.drain()
        self.assertEqual(self.fake.creates(), creates + 1)
        # redelivery: the provider_submit item is put back in the queue (duplicate wake / operator error)
        self.q("update public.cs_work_items set state = 'queued', due_at = now(), attempt_count = 0 "
               "where run_id = %s and stage = 'provider_submit'", (run["run_id"],))
        out = self.drain()
        self.assertIn("duplicate_dispatch_refused", [o.get("status") for o in out])
        self.assertEqual(self.fake.creates(), creates + 1)                        # still exactly one paid call
        # stale worker: an old fence can never commit
        item = self.q("select id, fencing_token from public.cs_work_items where run_id = %s and stage = "
                      "'provider_submit'", (run["run_id"],))[0]
        conn, cur = self._sql_as("cs_worker")
        try:
            self.assertSqlState("CS409", lambda: cur.execute(
                "select public.cs_prepare_attempt(%s, gen_random_uuid(), %s, 'x')", (item["id"], item["fencing_token"])))
        finally:
            conn.close()
        self.assertEqual(len(self.q("select 1 from public.cs_attempts where run_id = %s", (run["run_id"],))), 1)
        self.assertEqual(len(self.q("select 1 from public.cs_cost_ledger where run_id = %s and entry_type = 'settle'",
                                    (run["run_id"],))), 1)

    # -- 05 worker killed mid-run --------------------------------------------------------------------------
    def test_05a_worker_killed_during_ingest_resumes_without_double_charge(self):
        st, run, _ = self.admit("producer_a", self.ws_a, [provider_node("n1", self.low, prompt="Kill during ingest")])
        self.assertEqual(st, 202)
        creates = self.fake.creates()
        p1 = self.spawn_worker()
        p1.wait(60)
        self.assertIn("provider_completed", p1.stdout.read())                   # paid call recorded + settled
        marker = self.tmp / "provider" / "fetch_hanging"
        p2 = self.spawn_worker(hang_fetch=True)
        self.wait_for(marker, p2)
        p2.kill()                                                                # SIGKILL mid-ingest
        p2.wait(30)
        marker.unlink()
        self.assertEqual(self.q("select state::text from public.cs_work_items where run_id = %s and stage = "
                                "'ingest_original'", (run["run_id"],))[0]["state"], "leased")
        self.expire_leases(run["run_id"])
        self.drain()
        self.assertEqual(self.run_state(run["run_id"]), {"state": "completed", "cost_state": "settled"})
        self.assertEqual(self.fake.creates(), creates + 1)
        self.assertEqual([l["entry_type"] for l in self.ledger(run["run_id"])].count("settle"), 1)
        self.assertEqual(len(self.q("select 1 from public.cs_asset_versions where run_id = %s", (run["run_id"],))), 1)

    def test_05b_worker_killed_during_provider_call_needs_reconciliation_never_resubmits(self):
        st, run, _ = self.admit("producer_a", self.ws_a, [provider_node("n1", self.low, prompt="Kill during dispatch")])
        self.assertEqual(st, 202)
        creates = self.fake.creates()
        self.fake.set(create_mode="hang")
        marker = self.tmp / "provider" / "hanging"
        p = self.spawn_worker()
        try:
            self.wait_for(marker, p)
            p.kill()
            p.wait(30)
        finally:
            self.fake.set(create_mode="ok")
            marker.unlink(missing_ok=True)
        self.assertEqual(self.fake.creates(), creates + 1)
        self.drain()                                     # lease not expired: nothing may touch the paid item
        self.assertEqual(self.q("select state::text from public.cs_attempts where run_id = %s",
                                (run["run_id"],))[0]["state"], "dispatching")
        self.expire_leases(run["run_id"])
        out = self.drain()
        self.assertIn("needs_reconciliation", [r["action"] for o in out for r in o.get("recovered", [])])
        self.assertEqual(self.fake.creates(), creates + 1)                       # never blindly resubmitted
        self.assertEqual(self.run_state(run["run_id"]), {"state": "needs_reconciliation", "cost_state": "unknown"})
        res = self.reservation(run["run_id"])
        self.assertEqual((res["state"], res["settled_micros"]), ("unknown", None))  # exposure held, not zero
        attempt = self.q("select id from public.cs_attempts where run_id = %s", (run["run_id"],))[0]["id"]
        # operator reconciliation names an actor and a rationale
        self.assertSqlState("CS403", lambda: self.q("select public.cs_reconcile_attempt(%s, %s, 50000, null, 'x')",
                                                     (attempt, self.users["producer_a"])))
        self.q("select public.cs_reconcile_attempt(%s, %s, 50000, 'higgsfield-call:operator-lookup', "
               "'Provider transaction log shows one charge of 1 credit; no output was delivered.')",
               (attempt, self.users["owner_a"]))
        self.assertEqual(self.run_state(run["run_id"]), {"state": "failed", "cost_state": "settled"})
        self.assertEqual([(l["entry_type"], l["amount_micros"]) for l in self.ledger(run["run_id"])],
                         [("reserve", 100_000), ("unknown", 100_000), ("settle", 50_000), ("release", 50_000)])

    def test_05c_worker_lost_before_dispatch_marker_resumes_and_dispatches_once(self):
        st, run, _ = self.admit("producer_a", self.ws_a, [provider_node("n1", self.low, prompt="Die after prepare")])
        self.assertEqual(st, 202)
        conn, cur = self._sql_as("cs_worker")
        cur.execute("select * from public.cs_claim_work('test', 'doomed-worker', '{provider_submit}', 1)")
        item = cur.fetchone()
        cur.execute("select attempt_id from public.cs_prepare_attempt(%s, %s, %s, 'x')", (item[0], item[8], item[9]))
        attempt = cur.fetchone()[0]
        conn.commit()
        conn.close()                                                             # worker gone, nothing dispatched
        creates = self.fake.creates()
        self.expire_leases(run["run_id"])
        out = self.drain()
        self.assertIn("requeued_before_dispatch", [r["action"] for o in out for r in o.get("recovered", [])])
        self.assertEqual(self.fake.creates(), creates + 1)
        self.assertEqual(self.run_state(run["run_id"])["state"], "completed")
        self.assertEqual([str(r["id"]) for r in self.q("select id from public.cs_attempts where run_id = %s",
                                                       (run["run_id"],))], [str(attempt)])   # same attempt resumed

    # -- 06 cancel stops further attempts ------------------------------------------------------------------
    def test_06_cancel_stops_further_attempts(self):
        # chain n1 -> n2: n1 runs, cancel arrives, n2 must never dispatch
        n1 = provider_node("n1", self.low, prompt="Step one")
        n2 = provider_node("n2", self.low, prompt="Step two", deps=["n1"], inputs=[node_output_input("ref", "n1")],
                           media_bindings=[{"field": "media", "role": "image", "input_port": "ref", "ordinal": 0}])
        st, run, _ = self.admit("producer_a", self.ws_a, [n1, n2])
        self.assertEqual(st, 202, run)
        creates = self.fake.creates()
        with self._patched():
            self.runtime.run_once()                                               # n1 provider call
        st, body = call(self.app, "POST", f"/api/v1/workspaces/{self.ws_a}/runs/{run['run_id']}/cancel",
                        tok=self.tok["producer_a"], body={"reason": "Brief changed"})
        self.assertEqual((st, body["state"]), (202, "cancel_requested"))         # n1 ingest still in flight
        self.drain()
        self.assertEqual(self.fake.creates(), creates + 1)
        self.assertEqual(self.run_state(run["run_id"])["state"], "cancelled")
        nodes = {r["node_id"]: r["state"] for r in self.q("select node_id, state::text from public.cs_run_nodes "
                                                          "where run_id = %s", (run["run_id"],))}
        self.assertEqual(nodes, {"n1": "succeeded", "n2": "cancelled"})          # paid output kept
        self.assertEqual(self.reservation(run["run_id"], "n2")["state"], "released")
        self.assertEqual(self.reservation(run["run_id"], "n1")["state"], "settled")
        self.assertEqual(len(self.q("select 1 from public.cs_attempts where run_id = %s and node_id = 'n2'",
                                    (run["run_id"],))), 0)

        # cancel between claim and dispatch: the dispatch checkpoint refuses
        st, run2, _ = self.admit("producer_a", self.ws_a, [provider_node("n1", self.low, prompt="Leased then cancelled")])
        orig = self.runtime._envelope
        def cancel_then_envelope(item, att, refs):
            call(self.app, "POST", f"/api/v1/workspaces/{self.ws_a}/runs/{run2['run_id']}/cancel",
                 tok=self.tok["producer_a"], body={})
            return orig(item, att, refs)
        with mock.patch.object(self.runtime, "_envelope", cancel_then_envelope):
            self.drain(limit=1)
        self.drain()
        self.assertEqual(self.fake.creates(), creates + 1)
        self.assertEqual(self.run_state(run2["run_id"])["state"], "cancelled")
        self.assertEqual(self.q("select state::text from public.cs_attempts where run_id = %s",
                                (run2["run_id"],))[0]["state"], "cancelled")
        self.assertEqual(self.reservation(run2["run_id"])["state"], "released")
        # viewer cannot cancel; another workspace cannot see the run
        st, _ = call(self.app, "POST", f"/api/v1/workspaces/{self.ws_a}/runs/{run2['run_id']}/cancel",
                     tok=self.tok["viewer_a"], body={})
        self.assertEqual(st, 403)
        st, _ = call(self.app, "POST", f"/api/v1/workspaces/{self.ws_a}/runs/{run2['run_id']}/cancel",
                     tok=self.tok["owner_b"], body={})
        self.assertEqual(st, 404)

    # -- 07 final tier ------------------------------------------------------------------------------------------
    def test_07_final_tier_refused_without_approved_draft(self):
        draft_run = self.__class__.draft_run
        out = self.q("select id::text, asset_id::text, sha256, revision from public.cs_asset_versions where run_id = %s",
                     (draft_run,))[0]
        ref = {"authority": "creative-studio", "workspace_id": self.ws_a, "asset_id": out["asset_id"],
               "asset_version_id": out["id"], "revision": out["revision"], "sha256": out["sha256"],
               "mime_type": "image/png"}
        final = provider_node("n1", self.high, prompt="Final headline ad", quality="high",
                              inputs=[asset_input("draft", ref)],
                              media_bindings=[{"field": "media", "role": "image", "input_port": "draft", "ordinal": 0}])
        st, body, _ = self.admit("producer_a", self.ws_a, [final], tier="final")
        self.assertEqual((st, body["error"]), (422, "draft_approval_required"))
        st, body, _ = self.admit("producer_a", self.ws_a, [final], tier="draft")
        self.assertEqual((st, body["error"]), (422, "tier_mismatch"))
        st, body = call(self.app, "POST", f"/api/v1/workspaces/{self.ws_a}/runs/{draft_run}/nodes/n1/draft-approvals",
                        tok=self.tok["producer_a"], body={"asset_version_id": out["id"]})
        self.assertEqual(st, 403)                                                # producers cannot approve
        st, appr = call(self.app, "POST", f"/api/v1/workspaces/{self.ws_a}/runs/{draft_run}/nodes/n1/draft-approvals",
                        tok=self.tok["reviewer_a"], body={"asset_version_id": out["id"], "rationale": "Layout works"})
        self.assertEqual(st, 201, appr)
        no_ref = provider_node("n1", self.high, prompt="Final without the draft", quality="high")
        st, body, _ = self.admit("producer_a", self.ws_a, [no_ref], tier="final", approval=appr["approval_id"])
        self.assertEqual((st, body["error"]), (422, "draft_reference_required"))
        st, body, _ = self.admit("owner_b", self.ws_b, [no_ref], tier="final", approval=appr["approval_id"])
        self.assertIn(st, (404, 422))                                            # B cannot use A's approval
        st, run, _ = self.admit("producer_a", self.ws_a, [final], tier="final", approval=appr["approval_id"])
        self.assertEqual(st, 202, run)
        ops_before = len(self.fake.ops())
        self.drain()
        self.assertEqual(self.run_state(run["run_id"])["state"], "completed")
        new_ops = self.fake.ops()[ops_before:]
        self.assertIn("upload.create", new_ops)                                  # approved draft sent as reference
        dtc = [c for c in self.fake.log()[ops_before:] if c["op"] == "dtc.create"][0]
        self.assertEqual(dtc["args"][dtc["args"].index("--quality") + 1], "high")
        self.assertTrue(any(a.startswith("--media") for a in dtc["args"]))
        self.assertTrue(dtc["credential_env"])                                   # workspace credential, not CLI login
        self.assertEqual(self.reservation(run["run_id"])["settled_micros"], 325_000)

    # -- 08 eligibility and credential rechecked before paid dispatch --------------------------------------------
    def test_08_revoked_member_or_credential_blocks_dispatch_and_releases(self):
        st, run, _ = self.admit("producer2_a", self.ws_a, [provider_node("n1", self.low, prompt="Revoked requester")])
        self.assertEqual(st, 202)
        call(self.app, "DELETE", f"/api/v1/workspaces/{self.ws_a}/members/{self.users['producer2_a']}",
             tok=self.tok["owner_a"])
        creates = self.fake.creates()
        self.drain()
        self.assertEqual(self.fake.creates(), creates)
        self.assertEqual(self.run_state(run["run_id"])["state"], "authorization_blocked")
        self.assertEqual(self.reservation(run["run_id"])["state"], "released")

        st, run, _ = self.admit("producer_a", self.ws_a, [provider_node("n1", self.low, prompt="Revoked credential")])
        conn, cur = self._sql_as("cs_api", self.users["owner_a"])
        cur.execute("select public.cs_revoke_workspace_credential(%s, 'higgsfield')", (self.ws_a,))
        conn.commit()
        conn.close()
        try:
            self.drain()
            self.assertEqual(self.fake.creates(), creates)
            self.assertEqual(self.run_state(run["run_id"])["state"], "failed")
            self.assertEqual(self.reservation(run["run_id"])["state"], "released")
            st, body, _ = self.admit("producer_a", self.ws_a, [provider_node("n1", self.low, prompt="x")])
            self.assertEqual((st, body["error"]), (422, "provider_credentials_missing"))
        finally:
            self._store_credential("owner_a", self.ws_a)

    # -- 09 workspace isolation -----------------------------------------------------------------------------------
    def test_09_workspace_b_cannot_read_or_act_on_a(self):
        run_a = self.__class__.draft_run
        st, _ = call(self.app, "GET", f"/api/v1/workspaces/{self.ws_a}/runs/{run_a}/costs", tok=self.tok["owner_b"])
        self.assertEqual(st, 404)
        st, _ = call(self.app, "GET", f"/api/v1/workspaces/{self.ws_b}/runs/{run_a}/costs", tok=self.tok["owner_b"])
        self.assertEqual(st, 404)
        st, body, _ = self.admit("outsider", self.ws_a, [provider_node("n1", self.low, prompt="x")])
        self.assertEqual(st, 404)
        st, body, _ = self.admit("viewer_a", self.ws_a, [provider_node("n1", self.low, prompt="x")])
        self.assertEqual(st, 403)
        conn, cur = self._sql_as("cs_api", self.users["owner_b"])
        try:
            for t in ("cs_provider_receipts", "cs_attempts", "cs_budget_reservations", "cs_cost_ledger",
                      "cs_run_admissions", "cs_tier_approvals", "cs_budget_policies"):
                self.assertSqlState("42501", lambda: cur.execute(f"select count(*) from public.{t}"))
                conn.rollback()
            for fn in ("cs_get_run_cost", "cs_list_run_receipts"):
                self.assertSqlState("CS404", lambda: cur.execute(f"select * from public.{fn}(%s, %s)",
                                                                 (self.ws_a, run_a)))
                conn.rollback()
            self.assertSqlState("CS404", lambda: cur.execute("select public.cs_request_cancel(%s, %s, 'x')",
                                                             (self.ws_a, run_a)))
            conn.rollback()
            self.assertSqlState("CS404", lambda: cur.execute(
                "select public.cs_approve_draft(%s, %s, 'n1', gen_random_uuid(), null)", (self.ws_a, run_a)))
        finally:
            conn.close()

    # -- 10 receipts reconcile to reservations (all charge certainties) ------------------------------------------
    def test_10_receipts_reconcile_to_reservations(self):
        runs = {}
        for mode, balance_mode in (("charged", "ok"), ("ok", "broken"), ("failed", "ok"), ("nonzero", "ok")):
            self.fake.set(create_mode=mode, balance_mode=balance_mode)
            try:
                st, run, _ = self.admit("producer_a", self.ws_a, [provider_node("n1", self.low, prompt=f"mode {mode}")])
                self.assertEqual(st, 202, run)
                self.drain()
            finally:
                self.fake.set(create_mode="ok", balance_mode="ok")
            runs[mode] = run["run_id"]
        self.assertEqual(self.run_state(runs["charged"]), {"state": "completed", "cost_state": "settled"})
        cert = self.q("select charge_certainty from public.cs_provider_receipts where run_id = %s and kind = 'billing'",
                      (runs["charged"],))[0]["charge_certainty"]
        self.assertEqual(cert, "provider_reported")
        # balance unreadable -> adapter falls back to its estimate -> NOT settled, exposure held
        self.assertEqual(self.run_state(runs["ok"]), {"state": "completed", "cost_state": "unknown"})
        est = self.q("select charge_certainty, charge_amount_micros, provider_units::text as u "
                     "from public.cs_provider_receipts where run_id = %s and kind = 'billing'", (runs["ok"],))[0]
        self.assertEqual((est["charge_certainty"], est["charge_amount_micros"], est["u"]), ("estimated", None, "1"))
        self.assertEqual(self.run_state(runs["failed"]), {"state": "failed", "cost_state": "unknown"})
        self.assertEqual(self.run_state(runs["nonzero"]), {"state": "needs_reconciliation", "cost_state": "unknown"})

        # global invariant over every reservation in this database
        for r in self.q("select * from public.cs_budget_reservations"):
            led = self.q("select entry_type, sum(amount_micros)::bigint as s from public.cs_cost_ledger "
                         "where reservation_id = %s group by 1", (r["id"],))
            led = {x["entry_type"]: x["s"] for x in led}
            billed = self.q("select coalesce(sum(charge_amount_micros), 0)::bigint as s, count(*) as n "
                            "from public.cs_provider_receipts where attempt_id = %s and kind in "
                            "('billing', 'reconciliation') and charge_amount_micros is not null",
                            (r["attempt_id"],))[0] if r["attempt_id"] else {"s": 0, "n": 0}
            self.assertEqual(led["reserve"], r["ceiling_micros"], r)
            if r["state"] == "settled":
                self.assertEqual(led["settle"], r["settled_micros"])
                self.assertEqual(billed["s"], r["settled_micros"])               # receipts == settlement
                self.assertEqual(led.get("settle", 0) + led.get("release", 0) - led.get("adjust", 0),
                                 r["ceiling_micros"])
            elif r["state"] == "released":
                self.assertEqual((led.get("release"), led.get("settle"), billed["s"]), (r["ceiling_micros"], None, 0))
            elif r["state"] == "unknown":
                self.assertEqual((led.get("unknown"), led.get("settle")), (r["ceiling_micros"], None))
            else:
                self.assertEqual(r["state"], "reserved")

    # -- 11 billing exception blocks new spend -------------------------------------------------------------------
    def test_11_overcharge_records_exception_and_blocks_new_spend(self):
        self.set_policy("owner_b", self.acct_b, self.ws_b, ceiling=10_000_000, per_run=10_000_000)
        self.fake.set(create_mode="overcharge")
        try:
            st, run, _ = self.admit("owner_b", self.ws_b, [provider_node("n1", self.low, prompt="Overcharge")])
            self.assertEqual(st, 202)
            self.drain()
        finally:
            self.fake.set(create_mode="ok")
        res = self.reservation(run["run_id"])
        self.assertEqual((res["state"], res["settled_micros"], res["billing_exception"]), ("settled", 5_000_000, True))
        self.assertEqual(self.run_state(run["run_id"])["state"], "needs_reconciliation")
        st, body, _ = self.admit("owner_b", self.ws_b, [provider_node("n1", self.low, prompt="next")])
        self.assertEqual((st, body["error"]), (409, "billing_exception_open"))

    # -- 12 retries with backoff by error class (never a new paid call) -----------------------------------------
    def test_12_ingest_retries_with_backoff_then_fails_without_redispatch(self):
        st, run, _ = self.admit("producer_a", self.ws_a, [provider_node("n1", self.low, prompt="Flaky CDN")])
        self.assertEqual(st, 202)
        creates = self.fake.creates()
        self.fetcher.fail = True
        try:
            out = self.drain(limit=3)
            retry = [o for o in out if o.get("failure_code") == "output_unavailable"][0]
            self.assertEqual((retry["status"], retry["retry_in_seconds"]), ("retry_wait", 15))
            item = self.q("select state::text, due_at > now() + interval '10 seconds' as later "
                          "from public.cs_work_items where run_id = %s and stage = 'ingest_original'", (run["run_id"],))[0]
            self.assertEqual(item, {"state": "retry_wait", "later": True})   # backoff respected: not claimable yet
            for expected in (60, None):
                self.q("update public.cs_work_items set due_at = now() where run_id = %s and stage = 'ingest_original'",
                       (run["run_id"],))
                out = self.drain(limit=2)
                if expected:
                    self.assertEqual([o.get("retry_in_seconds") for o in out if o.get("failure_code")], [expected])
        finally:
            self.fetcher.fail = False
        self.drain()
        self.assertEqual(self.q("select state::text from public.cs_work_items where run_id = %s and stage = "
                                "'ingest_original'", (run["run_id"],))[0]["state"], "dead")
        self.assertEqual(self.run_state(run["run_id"]), {"state": "failed", "cost_state": "settled"})
        self.assertEqual(self.q("select state::text from public.cs_attempts where run_id = %s",
                                (run["run_id"],))[0]["state"], "failed")
        self.assertEqual(self.fake.creates(), creates + 1)                      # charge stands, no re-dispatch
        self.assertEqual(self.reservation(run["run_id"])["settled_micros"], 50_000)

    # -- 99 migration re-run ------------------------------------------------------------------------------------------
    def test_99_migration_rerun_is_idempotent(self):
        tables = ("cs_provider_catalogue_versions", "cs_budget_policies", "cs_run_admissions", "cs_attempts",
                  "cs_provider_receipts", "cs_cost_ledger", "cs_budget_reservations")
        before = {t: self.q(f"select count(*) as n from public.{t}")[0]["n"] for t in tables}
        pol = self.q("select count(*) as n from pg_policies where policyname like 'cs\\_b3\\_%'")[0]["n"]
        self.pg.reapply_cs_b1()                     # replaying the B1 file must not strip B3 access
        st, _ = call(self.app, "GET", f"/api/v1/workspaces/{self.ws_a}/runs/{self.__class__.draft_run}/costs",
                     tok=self.tok["viewer_a"])
        self.assertEqual(st, 200)
        with self.pg.connect() as conn:
            conn.cursor().execute(B3_MIGRATION.read_text(encoding="utf-8"))
            conn.cursor().execute(B3_MIGRATION.read_text(encoding="utf-8"))
        self.assertEqual({t: self.q(f"select count(*) as n from public.{t}")[0]["n"] for t in tables}, before)
        self.assertEqual(self.q("select count(*) as n from pg_policies where policyname like 'cs\\_b3\\_%'")[0]["n"], pol)
        self.assertIn(B3_MIGRATION.name, self.applied)          # applied, in filename order (CS-INT)
        self.assertEqual(self.applied, sorted(self.applied))
        st, _ = call(self.app, "GET", f"/api/v1/workspaces/{self.ws_a}/runs/{self.__class__.draft_run}/costs",
                     tok=self.tok["viewer_a"])
        self.assertEqual(st, 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
