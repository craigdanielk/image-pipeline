"""CS-17b real PostgreSQL 17: provider run -> compose -> export -> MOS handoff + actual cost.

Every migration applied in order and CS-17b applied twice more. The provider is the adapter's mocked transport
(FakeHiggsfield); MOS is a fake HTTP endpoint behind the REAL HttpMarketingOsGateway that verifies the signed
assertion exactly as the ENG-15 format defines. No network beyond loopback, no production."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import psycopg2

from service import assertion
from service.api import create_app
from service.brand.importer import StorageObjectWriter
from service.composition.canonical import artifact_sha256
from service.config import Settings
from service.postrun import layout
from service.postrun.runtime import PostRunRuntime
from service.providers import catalogue as cat_mod
from service.providers.adapter import engine_higgsfield
from service.providers.credentials import KeyRing
from service.runtime.gateway import HttpMarketingOsGateway
from service.runtime.worker import ProviderRuntime
from service.storage import StorageObjectStore
from tests.creative_studio import b2_mos_fixtures as fx
from tests.creative_studio import test_b2_real_db as _b2
from tests.creative_studio.assertion_support import KEY, GatewayApp
from tests.creative_studio.b3_support import (CREDENTIAL_PLAINTEXT, TEST_KEYS, FakeFetcher, FakeHiggsfield, load_source)
from tests.creative_studio.b4_support import asset_bytes
from tests.creative_studio.fake_storage import FakeStorage
from tests.creative_studio.pg_harness import MIGRATIONS_DIR, ThrowawayCluster, claims_json
from tests.creative_studio.test_b2_real_db import call
from tests.creative_studio.test_cs_b1_real_db import ISSUER, JWT_SECRET, token

KEYRING = KeyRing({k: base64.b64decode(v) for k, v in TEST_KEYS.items()})
MIGRATION = "20261031240100_cs_17b_post_run_pipeline.sql"
EVIDENCE = Path(__file__).parent / "evidence" / "cs17b_post_run_handoff.json"


def real_assets(biz) -> None:
    """The MOS fixture's logo/font files are stub bytes; the post-run renderer needs decodable files. Swap in the B4
    fixtures' real files BEFORE anything is exported (hashes in the BrandKit content and the file list follow)."""
    logo = asset_bytes("logo")
    font = asset_bytes("font")
    logo_sha = hashlib.sha256(logo).hexdigest()
    for f in biz.files:
        if f["kind"] not in ("logo", "font"):
            continue
        data = logo if f["kind"] == "logo" else font
        f["ref"]["sha256"] = hashlib.sha256(data).hexdigest()
        f["size_bytes"] = len(data)
        biz.blobs[f["ref"]["asset_version_id"]] = data
        if f["kind"] == "logo":
            f["width_px"], f["height_px"] = 480, 192
    for entry in biz.content["logo_assets"]["value"]:
        entry["sha256"] = logo_sha


class FakeMos:
    """Marketing OS behind HttpMarketingOsGateway(opener=...): verifies the CS->MOS service assertion, then answers."""

    def __init__(self, key: str, mos_ws: str):
        self.key, self.mos_ws = key, mos_ws
        self.mode = "ok"                      # ok | pending | down | refuse
        self.requests: list[dict] = []
        self.nonces: set[str] = set()
        self.expect_sub: str | None = None

    def verify(self, req) -> dict:
        header = dict(req.header_items()).get("X-cs-service-assertion")
        assert header, "assertion header missing"
        raw_b64, sig_b64 = header.split(".")
        pad = lambda s: s + "=" * (-len(s) % 4)       # noqa: E731
        raw = base64.urlsafe_b64decode(pad(raw_b64))
        assert hmac.compare_digest(base64.urlsafe_b64decode(pad(sig_b64)),
                                   hmac.new(self.key.encode(), raw, hashlib.sha256).digest()), "bad signature"
        p = json.loads(raw)
        # Exactly the claim set MOS `verifyServiceAssertion` requires, including the method+target binding.
        assert set(p) == {"aud", "sub", "act", "bnd", "bsha", "nonce", "iat", "exp", "pth"}
        assert p["pth"] == f"{req.get_method()} {HttpMarketingOsGateway.service_target(req.full_url)}", "pth"
        assert p["aud"] == "marketing-os" and p["act"] == "handoff.deliver" and p["bnd"] == self.mos_ws
        assert p["bsha"] == hashlib.sha256(req.data).hexdigest(), "body digest"
        assert 0 < p["exp"] - p["iat"] <= 60 and p["iat"] <= time.time() + 30 and time.time() <= p["exp"] + 30
        assert len(base64.urlsafe_b64decode(pad(p["nonce"]))) >= 16
        assert p["nonce"] not in self.nonces, "replayed nonce"
        self.nonces.add(p["nonce"])
        if self.expect_sub:
            assert p["sub"] == self.expect_sub
        # Real MOS refuses a non-user bearer (401) before reading the assertion: signed callbacks carry none.
        assert dict(req.header_items()).get("Authorization") is None, "signed callback must not carry a bearer"
        return p

    def export_of(self, version_id):
        return self.exports.get(version_id) or self.lookup(version_id)

    def receipt(self, handoff: dict, exports_by_ref: dict | None = None) -> dict:
        items = []
        for e in handoff["exports"]:
            items.append({"export": e, "export_asset": self.export_of(e["version_id"])["file"]["ref"],
                          "mos_asset_version": {"authority": "marketing-os", "workspace_id": self.mos_ws,
                                                "kind": "asset_version", "id": str(uuid.uuid4()),
                                                "version_id": str(uuid.uuid4()), "revision": 1, "sha256": "a" * 64},
                          "stored_bytes_sha256": self.export_of(e["version_id"])["file"]["ref"]["sha256"],
                          "mos_rights_receipt_ids": [str(uuid.uuid4())],
                          "mos_copy_versions": handoff["target"]["copy_versions"]})
        doc = {"schema_version": "cs-mos-ingestion-receipt-v1", "id": str(uuid.uuid4()), "workspace_id": self.mos_ws,
               "created_at": "2026-10-31T12:00:00Z", "created_by": handoff["created_by"], "sha256": "",
               "handoff_id": handoff["id"], "handoff_sha256": handoff["sha256"],
               "idempotency_key": handoff["idempotency_key"], "items": items, "review_status": "pending",
               "publication_eligible": False}
        doc["sha256"] = artifact_sha256(doc)
        return doc

    exports: dict = {}
    lookup = None            # version_id -> export manifest (the MOS pull of GET /exports/{id})

    def opener(self, req, timeout=0):
        self.requests.append({"url": req.full_url, "body": json.loads(req.data)})
        if self.mode == "down":
            import urllib.error
            raise urllib.error.URLError("connection refused")
        if self.mode == "refuse":
            import urllib.error
            raise urllib.error.HTTPError(req.full_url, 403, "forbidden", {}, None)
        self.verify(req)
        handoff = json.loads(req.data)["handoff"]
        body = {"pending": True} if self.mode == "pending" else self.receipt(handoff)

        class Resp:
            def __enter__(s):
                return s

            def __exit__(s, *a):
                return False

            def read(s):
                return json.dumps(body).encode()
        return Resp()


class FaultStore:
    """Wraps the real Storage store; selected keys return other bytes (simulates a missing/changed brand file)."""

    def __init__(self, inner):
        self.inner, self.corrupt = inner, set()

    def get(self, key):
        return b"corrupted" if key in self.corrupt else self.inner.get(key)

    def put(self, key, data, mime):
        return self.inner.put(key, data, mime)


class StaticTokens:
    def __init__(self, tok):
        self.tok = tok

    def token(self):
        return self.tok

    def invalidate(self, _):
        pass


class CsPostRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        B2 = _b2.CsB2RealDatabase
        cls.pg = ThrowawayCluster()
        cls.pg.init()
        cls.applied = cls.pg.install()
        cls.tmp = Path(tempfile.mkdtemp(prefix="cs17b_"))
        cls.fake = FakeHiggsfield(cls.tmp / "provider")
        with cls.pg.connect() as conn:
            conn.cursor().execute("update cs_private.cs_runtime_limits set admission_enabled = true, "
                                  "dispatch_enabled = true, paid_provider_enabled = false, active_run_limit = 20 "
                                  "where environment_id = 'test'")
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
        cls.writer = StorageObjectWriter(cls.settings, lambda: cls.tok["machine"])
        cls.home, cls.home_camp = fx.home_services()
        real_assets(cls.home)
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
        cls.set_policy_and_credential()
        # runtime: real Storage-backed store, mocked provider fetcher, real gateway against the fake MOS
        cls.store = FaultStore(StorageObjectStore(cls.settings, token_provider=StaticTokens(cls.tok["machine"])))
        cls.mos = FakeMos(KEY, cls.home.mos_ws)
        cls.mos.lookup = lambda vid: cls.q_(cls, "select manifest from public.cs_exports where version_id = %s", (vid,))[0][0]
        cls.mos_gateway = HttpMarketingOsGateway("https://mos.example.test", "svc-token", opener=cls.mos.opener)
        cls.fetcher = FakeFetcher(cls.tmp / "fetch")
        cls.post = PostRunRuntime(cls.settings, cls.store, gateway=cls.mos_gateway, assertion_key=KEY)
        cls.runtime = ProviderRuntime(cls.settings, cls.store, cls.fetcher, KEYRING, postrun=cls.post)
        cls.provider_only = ProviderRuntime(cls.settings, cls.store, cls.fetcher, KEYRING)
        cls.slot = 0

    @classmethod
    def tearDownClass(cls):
        cls.storage.stop()
        cls.pg.stop()

    # -- helpers -------------------------------------------------------------------
    @classmethod
    def set_policy_and_credential(cls):
        st, body = call(cls.app, "POST", f"/api/v1/accounts/{cls.acct}/budget-policies", tok=cls.tok["owner_a"],
                        idem=str(uuid.uuid4()),
                        body={"workspace_id": cls.ws, "funding_source": "workspace", "window_seconds": 86400,
                              "ceiling_micros": 50_000_000, "per_run_ceiling_micros": 10_000_000})
        assert st == 201, body
        sealed = KEYRING.seal(CREDENTIAL_PLAINTEXT, provider="higgsfield", fingerprint=f"acct-{cls.ws[:8]}")
        conn = psycopg2.connect(cls.pg.dsn(user="cs_api"))
        try:
            cur = conn.cursor()
            cur.execute("select set_config('request.jwt.claims', %s, false)", (claims_json(cls.users["owner_a"]),))
            cur.execute("select public.cs_store_workspace_credential(%s, 'test', 'higgsfield', %s, %s, %s, %s)",
                        (cls.ws, f"acct-{cls.ws[:8]}", sealed.ciphertext, sealed.nonce, sealed.key_version))
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def q_(cls, sql, args=()):
        with cls.pg.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, args or None)
            return cur.fetchall() if cur.description else []

    def q(self, sql, args=()):
        with self.pg.connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, args or None)
            return cur.fetchall() if cur.description else []

    def switch(self, on: bool):
        self.q("update cs_private.cs_runtime_limits set paid_provider_enabled = %s where environment_id = 'test'", (on,))

    def drain(self, runtime=None, limit=40, make_due=False):
        out = []
        rt = runtime or self.runtime
        with mock.patch.object(engine_higgsfield, "subprocess", SimpleNamespace(run=self.fake)):
            for _ in range(limit):
                if make_due:
                    self.q("update public.cs_work_items set due_at = now() where state = 'retry_wait'")
                r = rt.run_once()
                out.append(r)
                if r["claimed"] == 0 and not r.get("recovered") and not (make_due and self.q(
                        "select 1 from public.cs_work_items where state = 'retry_wait'")):
                    break
        return out

    def prepared_run(self, admit=True):
        """intake -> accept -> compile -> quote -> admit (switch on only for the admit call)."""
        type(self).slot += 1
        st, b = call(self.app, "POST", f"/api/v1/workspaces/{self.ws}/creative-requests/from-marketing-os",
                     tok=self.tok["producer_a"], idem=str(uuid.uuid4()),
                     body={"connection_id": self.conn_id, "visual_request": self.home_camp.visual_request(slot=self.slot)})
        self.assertEqual(st, 201, b)
        tref = b["treatment"]
        st, acc = call(self.app, "POST", f"/api/v1/workspaces/{self.ws}/treatments/{tref['id']}/accept",
                       tok=self.tok["producer_a"], idem=str(uuid.uuid4()), body={"version": tref},
                       headers={"HTTP_IF_MATCH": f'"{tref["sha256"]}"'})
        self.assertEqual(st, 201, acc)
        acc = acc["treatment"]
        st, plan = call(self.app, "POST", f"/api/v1/workspaces/{self.ws}/creative-requests/{b['request']['id']}/compile",
                        tok=self.tok["producer_a"],
                        body={"treatment": {"id": acc["id"], "version_id": acc["version_id"]}, "tier": "draft"})
        self.assertIn(st, (200, 201), plan)
        st, q = call(self.app, "POST", f"/api/v1/workspaces/{self.ws}/quotes", tok=self.tok["producer_a"],
                     body={"plan_id": plan["plan_id"], "tier": "draft"})
        self.assertEqual((st, q["status"]), (200, "quoted"), q)
        if not admit:
            return plan, None
        self.switch(True)
        try:
            st, run = call(self.app, "POST", f"/api/v1/workspaces/{self.ws}/runs", tok=self.tok["producer_a"],
                           body={"plan_id": plan["plan_id"], "tier": "draft"}, idem=str(uuid.uuid4()))
        finally:
            self.switch(False)
        self.assertEqual(st, 202, run)
        return plan, run["run_id"]

    def produce(self, **kw):
        """A finished run (provider stages need the switch on while they run; post-run stages never do)."""
        plan, rid = self.prepared_run()
        self.switch(True)
        try:
            self.drain(self.provider_only)
        finally:
            self.switch(False)
        self.assertEqual(self.q("select state::text from public.cs_runs where id = %s", (rid,)), [("completed",)])
        return plan, rid

    def status(self, rid, who="producer_a", ws=None):
        return call(self.app, "GET", f"/api/v1/workspaces/{ws or self.ws}/runs/{rid}", tok=self.tok[who])

    def post_state(self, rid):
        return self.q("select state, reason from public.cs_post_run_state where run_id = %s", (rid,))[0]

    # -- 01 install ----------------------------------------------------------------
    def test_01_migration_ordered_idempotent_and_locked_down(self):
        self.assertIn(MIGRATION, self.applied)
        self.assertEqual(self.applied, sorted(self.applied))
        self.assertGreater(MIGRATION, "20261031220100_cs_16_execution_plans.sql")
        self.pg.apply_file(MIGRATIONS_DIR / MIGRATION)
        self.pg.apply_file(MIGRATIONS_DIR / MIGRATION)
        self.assertEqual(self.q("select relrowsecurity and relforcerowsecurity from pg_class where relname = "
                                "'cs_post_run_state'"), [(True,)])
        for role in ("cs_api", "cs_worker"):
            conn = psycopg2.connect(self.pg.dsn(user=role))
            try:
                with self.assertRaises(psycopg2.Error) as c:
                    conn.cursor().execute("select * from public.cs_post_run_state")
                self.assertEqual(c.exception.pgcode, "42501")
            finally:
                conn.close()
        for fn in ("cs_post_context(uuid,uuid,bigint)", "cs_post_finish(uuid,uuid,bigint,text,text,jsonb,jsonb,text,text)",
                   "cs_post_enqueue_handoff(uuid,uuid,bigint,jsonb,text,text)"):
            self.assertEqual(self.q("select has_function_privilege('cs_api', %s, 'execute'), "
                                    "has_function_privilege('anon', %s, 'execute'), "
                                    "has_function_privilege('cs_worker', %s, 'execute')",
                                    (f"public.{fn}",) * 3), [(False, False, True)], fn)

    # -- 02 the whole chain ----------------------------------------------------------------
    def test_02_run_completes_then_compose_export_handoff_delivered_once_with_actual_cost(self):
        plan, rid = self.produce()
        # the provider stage finished; the post-run chain is queued, FREE, and has not run yet
        self.assertEqual(self.post_state(rid), ("queued", None))
        before_creates = self.fake.creates()
        before = self.q("select count(*) from public.cs_budget_reservations")
        self.drain()
        self.assertEqual(self.fake.creates(), before_creates, "post-run stages never call the provider")
        self.assertEqual(self.q("select count(*) from public.cs_budget_reservations"), before, "and reserve nothing")
        self.assertEqual(self.post_state(rid), ("handoff_delivered", None))
        # one composition, one export, one handoff, delivered once with a verified receipt
        self.assertEqual(self.q("select count(*) from public.cs_exports where run_id = %s", (rid,)), [(1,)])
        (state, receipt, payload, n_exports), = self.q(
            "select h.state, h.receipt_ref, h.payload, (select count(*) from public.cs_handoff_exports e "
            "where e.handoff_id = h.id) from public.cs_handoffs h join public.cs_post_run_state s "
            "on s.handoff_id = h.id where s.run_id = %s", (rid,))
        self.assertEqual((state, n_exports), ("received", 1))
        self.assertEqual(receipt["review_status"], "pending")
        self.assertEqual(len([r for r in self.mos.requests if r["body"]["handoff"]["id"] == payload["id"]]), 1)
        # export bytes: stored, hashed, text composed (never model drawn), logo is the exact brand file
        (manifest,) = self.q("select manifest from public.cs_exports where run_id = %s", (rid,))[0]
        data = self.store.inner.get(self.q("select storage_key from public.cs_asset_versions where id = %s",
                                           (manifest["file"]["ref"]["asset_version_id"],))[0][0])
        self.assertEqual(hashlib.sha256(data).hexdigest(), manifest["file"]["ref"]["sha256"])
        self.assertEqual((manifest["format"]["preset"], manifest["file"]["width_px"], manifest["file"]["height_px"]),
                         ("square_1080", 1080, 1080))
        self.assertTrue(manifest["exact_copy"] and all(c["source_copy"] for c in manifest["exact_copy"]))
        (comp,) = self.q("select payload from public.cs_composition_versions c join public.cs_post_run_state s "
                         "on s.composition_version_id = c.version_id where s.run_id = %s", (rid,))[0]
        layers = {l["id"]: l for l in comp["artboards"][0]["layers"]}
        self.assertEqual(layers["logo"]["content"]["asset"]["sha256"],
                         hashlib.sha256(asset_bytes("logo")).hexdigest())
        self.assertTrue(all(l["content"]["copy_id"] for l in layers.values() if l["type"] == "text"))
        (detail,) = self.q("select detail from public.cs_post_run_state where run_id = %s", (rid,))[0]
        # the last stage's detail is the handoff one; the composed render detail lives in the run events
        self.assertFalse(any("model_drawn_text\": true" in json.dumps(e[0]) for e in self.q(
            "select payload from public.cs_run_events where run_id = %s", (rid,))))
        # run status: states, handoff reference, settled actual cost (never an estimate)
        st, body = self.status(rid)
        run = body["run"]
        self.assertEqual((st, run["state"], run["post_run"][0]["state"]), (200, "completed", "handoff_delivered"))
        self.assertEqual(run["handoff"]["version_id"], payload["version_id"])
        self.assertEqual(run["actual_cost"], {"currency": "USD", "micros": 50000, "certainty": "settled",
                                              "unresolved_micros": 0, "receipt_ids": run["actual_cost"]["receipt_ids"]})
        self.assertEqual(run["actual_usd"], "0.05")
        self.assertEqual(payload["cost"]["state"], "settled")
        self.assertEqual((payload["cost"]["settled_micros"], payload["cost"]["currency"]), (50000, "USD"))
        self.assertEqual(run["charge"]["certainty"] in ("provider_reported", "balance_delta", "estimated", "unknown"), True)
        EVIDENCE.parent.mkdir(exist_ok=True)
        EVIDENCE.write_text(json.dumps({"handoff": payload, "run_status": {k: run[k] for k in (
            "state", "post_run", "handoff", "actual_cost", "actual_usd", "charge")}}, indent=1, sort_keys=True,
            default=str) + "\n")

    def test_03_replay_adds_no_duplicate_stage_or_delivery(self):
        plan, rid = self.produce()
        self.drain()
        calls = len(self.mos.requests)
        items = self.q("select count(*) from public.cs_work_items where run_id = %s", (rid,))
        self.q("select cs_private.cs_enqueue_post_run(workspace_id, run_id, canvas_node_id) "
               "from public.cs_post_run_state where run_id = %s", (rid,))
        self.assertEqual(self.q("select count(*) from public.cs_work_items where run_id = %s", (rid,)), items)
        # a duplicate deliver_handoff item (e.g. a re-queued lease) replays: no second handoff, no second MOS call
        canvas = self.q("select canvas_node_id from public.cs_post_run_state where run_id = %s", (rid,))[0][0]
        self.q("insert into public.cs_work_items (workspace_id, environment_id, run_id, node_id, stage, dedupe_key, "
               "max_attempts, payload_ref) select workspace_id, environment_id, id, 'post.handoff.' || %s, "
               "'deliver_handoff', 'dup:' || gen_random_uuid(), 3, jsonb_build_object('canvas_node_id', %s) "
               "from public.cs_runs where id = %s", (canvas, canvas, rid))
        self.drain()
        self.assertEqual(len(self.mos.requests), calls)
        self.assertEqual(self.q("select count(*) from public.cs_handoffs h join public.cs_post_run_state s on "
                                "s.handoff_id = h.id where s.run_id = %s", (rid,)), [(1,)])
        self.assertEqual(self.post_state(rid), ("handoff_delivered", None))

    def test_04_blocked_states_are_typed_visible_and_never_hand_off(self):
        calls = len(self.mos.requests)
        # (a) a brand logo whose stored bytes no longer match the pinned hash
        plan, rid = self.produce()
        logo_key = self.q("select av.storage_key from public.cs_source_asset_mirrors m join public.cs_asset_versions av "
                          "on av.id = m.asset_version_id where av.mime_type = 'image/png' limit 1")[0][0]
        self.store.corrupt = {logo_key}
        try:
            self.drain()
        finally:
            self.store.corrupt = set()
        self.assertEqual(self.post_state(rid), ("compose_blocked", "logo_asset_missing"))
        # (b) an unusable font file
        plan, rid2 = self.produce()
        font_key = self.q("select av.storage_key from public.cs_source_asset_mirrors m join public.cs_asset_versions av "
                          "on av.id = m.asset_version_id where av.mime_type = 'font/ttf' limit 1")[0][0]
        self.store.corrupt = {font_key}
        try:
            self.drain()
        finally:
            self.store.corrupt = set()
        self.assertEqual(self.post_state(rid2)[0], "compose_blocked")
        self.assertIn(self.post_state(rid2)[1], ("font_format_unsupported", "font_asset_missing"))
        # (c) copy that cannot fit the layout (no truncation, no auto-shrink beyond the fixed ladder)
        plan, rid3 = self.produce()
        with mock.patch.object(layout, "LADDER", (1.0,)), mock.patch.object(layout, "HEADLINE_SIZE_RATIO", 0.6):
            self.drain()
        self.assertEqual(self.post_state(rid3), ("compose_blocked", "text_overflow"))
        for r in (rid, rid2, rid3):
            self.assertEqual(self.q("select state::text from public.cs_runs where id = %s", (r,)), [("completed",)])
            self.assertEqual(self.q("select count(*) from public.cs_handoffs h join public.cs_post_run_state s on "
                                    "s.handoff_id = h.id where s.run_id = %s", (r,)), [(0,)])
            self.assertEqual(self.q("select count(*) from public.cs_exports where run_id = %s", (r,)), [(0,)])
            st, body = self.status(r)
            self.assertEqual(body["run"]["state"], "completed")
            self.assertTrue(body["run"]["post_run"][0]["state"] == "compose_blocked" and body["run"]["post_run"][0]["reason"])
            self.assertIsNone(body["run"]["handoff"])
            self.assertEqual(body["run"]["actual_cost"]["certainty"], "settled", "the provider cost is still reported")
        self.assertEqual(len(self.mos.requests), calls, "blocked canvases are never sent")

    def test_05_gateway_failure_retries_with_backoff_then_handoff_failed_never_delivered(self):
        plan, rid = self.produce()
        self.mos.mode = "down"
        calls = len(self.mos.requests)
        try:
            self.drain(make_due=False)
            self.assertEqual(self.post_state(rid)[0], "exported", "compose+export done; deliver is waiting to retry")
            (item,) = self.q("select state::text, attempt_count, failure_code, due_at > now() from public.cs_work_items "
                             "where run_id = %s and stage = 'deliver_handoff'", (rid,))
            self.assertEqual(item, ("retry_wait", 1, "upstream_unavailable", True), "bounded backoff, not immediate")
            self.drain(make_due=True)
        finally:
            self.mos.mode = "ok"
        self.assertEqual(len(self.mos.requests) - calls, 3, "three bounded attempts")
        self.assertEqual(self.post_state(rid), ("handoff_failed", "upstream_unavailable"))
        (hstate, receipt), = self.q("select h.state, h.receipt_ref from public.cs_handoffs h join "
                                    "public.cs_post_run_state s on s.handoff_id = h.id where s.run_id = %s", (rid,))
        self.assertEqual(hstate, "failed")
        self.assertNotEqual(hstate, "received")
        self.assertIsNone(receipt)
        st, body = self.status(rid)
        self.assertEqual(body["run"]["post_run"][0]["state"], "handoff_failed")
        self.assertEqual(body["run"]["state"], "completed")

    def test_06_mos_refusal_and_pending_are_truthful(self):
        plan, rid = self.produce()
        self.mos.mode = "refuse"
        try:
            self.drain()
        finally:
            self.mos.mode = "ok"
        self.assertEqual(self.post_state(rid), ("handoff_failed", "mos_refused_403"))
        plan, rid2 = self.produce()
        self.mos.mode = "pending"
        try:
            self.drain()
        finally:
            self.mos.mode = "ok"
        self.assertEqual(self.post_state(rid2), ("handoff_offered", "awaiting_mos_receipt"))
        (hstate, receipt), = self.q("select h.state, h.receipt_ref from public.cs_handoffs h join "
                                    "public.cs_post_run_state s on s.handoff_id = h.id where s.run_id = %s", (rid2,))
        self.assertEqual((hstate, receipt), ("delivering", None))
        # MOS later pulls the handoff and reports its receipt: only then is it received
        st, body = self.status(rid2)
        href = body["run"]["handoff"]
        st, got = call(self.app, "GET", f"/api/v1/workspaces/{self.ws}/handoffs/marketing-os/{href['version_id']}",
                       tok=self.tok["producer_a"])
        self.assertEqual((st, got["data"]["handoff"]["sha256"], got["data"]["state"]), (200, href["sha256"], "delivering"))
        handoff = got["data"]["handoff"]
        self.mos.exports = {e["version_id"]: self.q("select manifest from public.cs_exports where version_id = %s",
                                                    (e["version_id"],))[0][0] for e in handoff["exports"]}
        receipt = self.mos.receipt(handoff)
        bad = dict(receipt, handoff_sha256="b" * 64)
        bad["sha256"] = artifact_sha256(bad)
        path = f"/api/v1/workspaces/{self.ws}/handoffs/marketing-os/{href['version_id']}/receipt"
        self.assertEqual(call(self.app, "POST", path, tok=self.tok["viewer_a"], body=receipt)[0], 403)
        self.assertEqual(call(self.app, "POST", path, tok=self.tok["producer_a"], body=bad)[0], 422)
        st, out = call(self.app, "POST", path, tok=self.tok["producer_a"], body=receipt)
        self.assertEqual((st, out["data"]["state"]), (201, "received"), out)
        self.assertEqual(self.post_state(rid2), ("handoff_delivered", None))
        self.assertEqual(call(self.app, "POST", path, tok=self.tok["producer_a"], body=receipt)[0], 200)   # replay
        self.assertEqual(call(self.raw_app, "POST", path, tok=self.tok["producer_a"], body=receipt)[1],
                         {"error": "assertion_missing"}, "gateway-only route")

    def test_06b_server_callback_reads_with_a_route_bound_assertion_and_no_session(self):
        """Marketing OS ingests a worker-delivered handoff with no user session: for handoff/export reads and the
        receipt only, a verified, route-bound, single-use assertion is the authentication (2026-11-05)."""
        import io, json as _json
        from tests.creative_studio.assertion_support import header_for, KEY as _KEY
        plan, rid = self.produce()
        self.mos.mode = "pending"
        try:
            self.drain()
        finally:
            self.mos.mode = "ok"
        href = self.status(rid)[1]["run"]["handoff"]
        path = f"/api/v1/workspaces/{self.ws}/handoffs/marketing-os/{href['version_id']}"
        binding = self.q("select external_workspace_id from public.cs_external_bindings where workspace_id = %s "
                         "and status = 'active'", (self.ws,))[0][0]
        user = self.users["producer_a"]

        def raw_call(method, p, header, body=b""):
            env = {"REQUEST_METHOD": method, "PATH_INFO": p, "QUERY_STRING": "", "wsgi.input": io.BytesIO(body),
                   "CONTENT_LENGTH": str(len(body)), "SERVER_NAME": "t", "SERVER_PORT": "80", "wsgi.url_scheme": "http"}
            if header:
                env["HTTP_X_CS_SERVICE_ASSERTION"] = header
            cap = {}
            out = b"".join(self.raw_app(env, lambda st, h: cap.setdefault("s", int(st.split()[0]))))
            return cap["s"], _json.loads(out)

        def signed(action, method, p, body=b"", sub=user, pth=True, nonce=None):
            pl = __import__("tests.creative_studio.assertion_support", fromlist=["payload_for"]).payload_for(
                user_id=sub, action=action, binding=binding, body=body, nonce=nonce)
            if pth:
                pl["pth"] = f"{method} {p}"
            return assertion.sign(_KEY, pl)

        st, got = raw_call("GET", path, signed("handoff.read", "GET", path))
        self.assertEqual((st, got["data"]["handoff"]["sha256"]), (200, href["sha256"]), got)
        self.assertEqual(raw_call("GET", path, None)[0], 401, "no session and no assertion")
        self.assertEqual(raw_call("GET", path, signed("handoff.read", "GET", path, pth=False))[0], 401, "route-bound only")
        fixed = assertion.b64e(b"n" * 16)
        h = signed("handoff.read", "GET", path, nonce=fixed)
        self.assertEqual(raw_call("GET", path, h)[0], 200)
        self.assertEqual(raw_call("GET", path, h)[0], 401, "single use")
        # trust is Marketing OS's key (CS-21: MOS authorizes the member before signing): another key never admits
        forged_payload = __import__("tests.creative_studio.assertion_support", fromlist=["payload_for"]).payload_for(
            user_id=user, action="handoff.read", binding=binding, body=b"")
        forged_payload["pth"] = f"GET {path}"
        self.assertEqual(raw_call("GET", path, assertion.sign("x" * 48, forged_payload))[0], 401, "wrong key")
        # any other gateway action still needs a real session
        rpath = f"/api/v1/workspaces/{self.ws}/runs/{rid}"
        self.assertEqual(raw_call("GET", rpath, signed("run.read", "GET", rpath))[0], 401)

    def test_07_undeliverable_without_credentials_stays_offered_not_delivered(self):
        plan, rid = self.produce()
        offline = PostRunRuntime(self.settings, self.store, gateway=None)
        self.drain(ProviderRuntime(self.settings, self.store, self.fetcher, KEYRING, postrun=offline))
        self.assertEqual(self.post_state(rid), ("handoff_offered", "mos_delivery_unconfigured"))
        self.assertEqual(self.q("select h.state from public.cs_handoffs h join public.cs_post_run_state s on "
                                "s.handoff_id = h.id where s.run_id = %s", (rid,)), [("queued",)])

    def test_08_spend_switch_gates_the_provider_stage_only(self):
        # switch off: admission is refused, nothing runs, no provider call
        plan, _ = self.prepared_run(admit=False)
        creates = self.fake.creates()
        st, out = call(self.app, "POST", f"/api/v1/workspaces/{self.ws}/runs", tok=self.tok["producer_a"],
                       body={"plan_id": plan["plan_id"], "tier": "draft"}, idem=str(uuid.uuid4()))
        self.assertEqual((st, out["error"]), (503, "paid_provider_disabled"))
        self.assertEqual(self.fake.creates(), creates)
        # a run admitted while on, provider stage run while on; after the switch goes off the FREE chain still completes
        plan, rid = self.produce()
        self.switch(False)
        self.drain()
        self.assertEqual(self.post_state(rid), ("handoff_delivered", None))
        self.assertEqual(self.fake.creates(), creates + 1, "exactly the one admitted provider call")
        # and a provider stage that was never admitted cannot start with the switch off
        plan, rid2 = self.prepared_run()
        self.drain()
        self.assertEqual(self.fake.creates(), creates + 1)
        self.assertEqual(self.q("select state::text from public.cs_runs where id = %s", (rid2,)), [("queued",)])
        self.assertEqual(self.q("select count(*) from public.cs_post_run_state where run_id = %s", (rid2,)), [(0,)])

    def test_09_actual_cost_is_unknown_until_settled(self):
        plan, rid = self.prepared_run()
        st, body = self.status(rid)
        self.assertEqual(body["run"]["actual_cost"]["certainty"], "unknown")
        self.assertIsNone(body["run"]["actual_cost"]["micros"])
        self.assertIsNone(body["run"]["actual_usd"])
        self.assertGreater(body["run"]["actual_cost"]["unresolved_micros"], 0)
        self.assertEqual((body["run"]["post_run"], body["run"]["handoff"]), ([], None))

    def test_10_workspace_isolation_and_authority(self):
        plan, rid = self.produce()
        self.drain()
        self.assertEqual(self.status(rid, who="owner_b", ws=self.ws)[0], 404)
        self.assertEqual(self.status(rid, who="owner_b", ws=self.ws_b)[0], 404)
        (export_version,) = self.q("select version_id::text from public.cs_exports where run_id = %s", (rid,))[0]
        ticket = f"/api/v1/workspaces/{self.ws_b}/exports/{export_version}/download-ticket"
        self.assertEqual(call(self.app, "POST", ticket, tok=self.tok["owner_b"], body={})[0], 404)
        # the worker context of one workspace never carries another's rows
        (ctx_ws,) = self.q("select count(*) from public.cs_post_run_state where workspace_id = %s", (self.ws_b,))[0]
        self.assertEqual(ctx_ws, 0)
        # download ticket: exact file hash required; viewers can read, wrong hash refused
        path = f"/api/v1/workspaces/{self.ws}/exports/{export_version}/download-ticket"
        (sha,) = self.q("select file_sha256 from public.cs_exports where version_id = %s", (export_version,))[0]
        st, out = call(self.app, "POST", path, tok=self.tok["producer_a"], body={"expected_sha256": sha})
        self.assertEqual(st, 200, out)
        self.assertEqual((out["sha256"], out["mime_type"]), (sha, "image/png"))
        self.assertEqual(call(self.app, "POST", path, tok=self.tok["producer_a"], body={"expected_sha256": "0" * 64})[0], 409)
        # a worker lease from a post stage cannot be used for another stage's mutation
        conn = psycopg2.connect(self.pg.dsn(user="cs_worker"))
        try:
            with self.assertRaises(psycopg2.Error):
                conn.cursor().execute("select public.cs_post_finish(gen_random_uuid(), gen_random_uuid(), 1, "
                                      "'exported', null, '{}', '{}', null, 'x')")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
