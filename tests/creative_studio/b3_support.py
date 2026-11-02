"""CS-B3 test support: mocked Higgsfield CLI transport, output fetcher, object
store and contract-shaped plan builder. No network, no real provider process:
`subprocess.run` inside the adapter is replaced by FakeHiggsfield, which answers
from tests/fixtures/higgsfield and keeps cross-process state in a directory so
a killed worker process and its successor see the same provider account."""
from __future__ import annotations

import base64
import json
import os
import struct
import subprocess
import time
import uuid
import zlib
from decimal import Decimal
from pathlib import Path

from service.providers.canonical import artifact_digest, digest

ROOT = Path(__file__).resolve().parents[2]
HF_FIX = ROOT / "tests" / "fixtures" / "higgsfield"
B3_FIX = ROOT / "tests" / "fixtures" / "providers" / "csb3"
HEADLINE = "0f6c2a7e-1d3b-4c55-9a10-5b7e8c9d0a11"
TEST_KEYS = {"k1": base64.b64encode(b"k" * 32).decode()}   # test-only AES key, not a credential
CREDENTIAL_PLAINTEXT = b'{"token":"test-only-provider-credential"}'
# The adapter refuses before dispatch when no CLI binary exists; point it at a test stub (never executed: tests
# replace subprocess.run) so suites do not depend on a developer's local install.
os.environ.setdefault("CS_HIGGSFIELD_BIN", str(ROOT / "tests" / "fixtures" / "bin" / "higgsfield"))


def fx(name: str):
    return json.loads((HF_FIX / name).read_text(encoding="utf-8"))


def load_source() -> dict:
    src = json.loads((B3_FIX / "catalogue-source.fixture.json").read_text(encoding="utf-8"))
    src["marketing_studio"]["catalogue"] = json.loads((ROOT / src["marketing_studio"]["catalogue"]).read_text())
    return src


def png(seed: str, w: int = 64, h: int = 64) -> bytes:
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + bytes([(i + len(seed)) % 256]) * (w * 3) for i in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"tEXt", b"seed\x00" + seed.encode()) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


class FakeHiggsfield:
    """Stands in for subprocess.run inside engine_higgsfield. Modes (state file):
    create_mode: ok | charged | failed | nonzero | hang | overcharge
    balance_mode: ok | broken (account status fails -> adapter falls back to the estimate)."""

    COST = {"dtc.low": Decimal("1"), "dtc.medium": Decimal("1"), "dtc.high": Decimal("6.5"),
            "generate": Decimal("2.5")}

    def __init__(self, state_dir: str | Path):
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        if not (self.dir / "state.json").exists():
            self.set(balance="500", create_mode="ok", balance_mode="ok")

    # -- shared state ----------------------------------------------------------
    def state(self) -> dict:
        return json.loads((self.dir / "state.json").read_text())

    def set(self, **kw) -> None:
        st = self.state() if (self.dir / "state.json").exists() else {}
        st.update(kw)
        (self.dir / "state.json").write_text(json.dumps(st))

    def log(self) -> list[dict]:
        p = self.dir / "calls.jsonl"
        return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []

    def ops(self) -> list[str]:
        return [c["op"] for c in self.log()]

    def creates(self) -> int:
        return sum(1 for op in self.ops() if op.endswith(".create"))

    def _record(self, op, args, env):
        with open(self.dir / "calls.jsonl", "a") as fh:
            fh.write(json.dumps({"op": op, "args": [a if len(a) < 120 else f"<{len(a)} chars>" for a in args],
                                 "credential_env": bool(env and env.get("HIGGSFIELD_CREDENTIALS_PATH")),
                                 "pid": os.getpid()}) + "\n")

    # -- transport ---------------------------------------------------------------
    def __call__(self, cmd, capture_output=False, text=False, env=None, **_kw):
        if not isinstance(cmd, list) or os.path.basename(cmd[0]) != "higgsfield":
            raise AssertionError(f"unexpected process {cmd!r}")
        args = cmd[1:]
        st = self.state()
        if args[:3] == ["marketing-studio", "dtc-ads", "generate"]:
            q = args[args.index("--quality") + 1]
            key = f"dtc.{q}"
            if "--cost-only" in args:
                self._record("dtc.cost", args, env)
                return self._ok(fx(f"dtc_ads.cost.{'high' if q == 'high' else 'low'}.response.json"))
            return self._create("dtc.create", key, args, env, st, surface="dtc")
        if args[:2] == ["generate", "cost"]:
            self._record("generate.cost", args, env)
            return self._ok(fx("generate.cost.response.json"))
        if args[:2] == ["generate", "create"]:
            return self._create("generate.create", "generate", args, env, st, surface="generate")
        if args[:2] == ["upload", "create"]:
            self._record("upload.create", args, env)
            return self._ok({"id": str(uuid.uuid4()), "type": "image"})
        if args[:2] == ["account", "status"]:
            self._record("account.status", args, env)
            if st.get("balance_mode") == "broken":
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="account endpoint unavailable")
            return self._ok({"email": "ops@example-studio.test", "plan": "example-plan", "credits": float(st["balance"])})
        raise AssertionError(f"unrouted higgsfield argv {args!r}")

    def _create(self, op, key, args, env, st, surface):
        self._record(op, args, env)
        mode = st.get("create_mode", "ok")
        if mode == "hang":
            (self.dir / "hanging").write_text(str(os.getpid()))
            time.sleep(3600)
        if mode == "nonzero":
            return subprocess.CompletedProcess(["higgsfield"] + args, 1, stdout="", stderr="connection reset")
        cost = self.COST[key]
        if mode == "overcharge":
            cost = Decimal("100")
        self.set(balance=str(Decimal(st["balance"]) - cost))
        job_id = str(uuid.uuid4())
        batch = int(args[args.index("--batch-size") + 1]) if "--batch-size" in args else (
            int(args[args.index("--batch_size") + 1]) if "--batch_size" in args else 1)
        job = {"id": job_id, "status": "failed" if mode == "failed" else "completed", "job_type": surface}
        if mode != "failed":
            urls = [f"https://cdn.provider.test/jobs/{job_id}/result-{i}.png" for i in range(batch)]
            if batch == 1:
                job["result_url"] = urls[0]
            else:
                job["results"] = [{"url": u} for u in urls]
        else:
            job["error"] = "nsfw_rejected"
        if mode in ("charged", "overcharge"):
            job["credits_charged"] = float(cost)
        return self._ok(job)

    @staticmethod
    def _ok(payload):
        return subprocess.CompletedProcess(["higgsfield"], 0, stdout=json.dumps(payload), stderr="")


class FakeFetcher:
    """Provider CDN stand-in. hang_dir: write a marker and block (kill test)."""

    def __init__(self, state_dir: str | Path, *, hang: bool = False, fail: bool = False):
        self.dir = Path(state_dir)
        self.hang = hang
        self.fail = fail
        self.calls = []

    def fetch(self, url, allowed_origins):
        from service.runtime.worker import Retryable
        self.calls.append(url)
        if not url.startswith(tuple(o + "/" for o in allowed_origins)):
            raise Retryable("output_unavailable", "origin not allowed")
        if self.hang:
            (self.dir / "fetch_hanging").write_text(str(os.getpid()))
            time.sleep(3600)
        if self.fail:
            raise Retryable("output_unavailable", "simulated CDN outage")
        return png(url)


class DirStore:
    """Object store stand-in (exact keys, no overwrite)."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def put(self, key, data, mime):
        p = self.root / key
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists():
            raise FileExistsError(key)
        p.write_bytes(data)

    def get(self, key):
        return (self.root / key).read_bytes()


# -- contract-shaped execution plan (execution-plan-v1) -----------------------------
def _vref(ws, kind):
    return {"authority": "creative-studio", "workspace_id": ws, "kind": kind, "id": str(uuid.uuid4()),
            "version_id": str(uuid.uuid4()), "revision": 1, "sha256": digest({"kind": kind, "ws": ws})}


def provider_node(node_id: str, entry: dict, *, prompt: str, batch: int = 1, quality: str | None = None,
                  inputs: list | None = None, deps: list | None = None, media_bindings: list | None = None,
                  maximum: int | None = None, estimated: int | None = None) -> dict:
    unit = entry["pricing"]["unit_cost_micros"]
    params = {"batch_size": batch}
    if entry["endpoint_key"] == "marketing-studio-dtc-ads":
        params["format_id"] = entry["model_id"]
        params["quality"] = quality or entry["id"].rsplit("-", 1)[1]
    elif quality:
        params["quality"] = quality
    return {
        "id": node_id, "kind": "provider_generate", "implementation_version": "cs-b3-test-1",
        "dependencies": deps or [], "inputs": inputs or [],
        "provider_call": {
            "provider": entry["provider"], "model_id": entry["model_id"], "catalogue_entry_id": entry["id"],
            "endpoint_key": entry["endpoint_key"], "adapter_version": entry["adapter_version"],
            "request_schema_sha256": entry["request_schema_sha256"], "positive_prompt": prompt,
            "negative_prompt": None, "parameters": params, "media_bindings": media_bindings or [], "seed": None,
            "retries": {"automatic_resubmits": 0, "transport_before_dispatch_max": 2, "same_intent_replay_max": 0}},
        "transform": None,
        "output_contract": [{"port": "image", "media_kind": "image", "mime_type": "image/png", "width_px": None,
                             "height_px": None, "duration_frames": None}],
        "estimated_cost_micros": estimated if estimated is not None else unit * batch,
        "maximum_cost_micros": maximum if maximum is not None else 2 * unit * batch,
    }


def make_plan(ws: str, nodes: list[dict], *, budget_ceiling: int | None = None, actor: str | None = None) -> dict:
    total = sum(n["maximum_cost_micros"] for n in nodes)
    ceiling = budget_ceiling if budget_ceiling is not None else total
    program = {
        "schema_version": "cs-program-v1", "compiler_version": "cs-b2-provisional", "lowering_version": "1",
        "node_registry_sha256": digest({"registry": "provisional"}), "catalogue_sha256": digest({"catalogue": "fixture"}),
        "template_sha256": None,
        "source_semantics": {"request_sha256": digest({"r": ws}), "treatment_sha256": digest({"t": ws}),
                             "recipe_graph_sha256": digest({"g": [n["id"] for n in nodes]}), "composition_sha256": None},
        "asset_inputs": [i["source"]["asset"] for n in nodes for i in n["inputs"] if i["source"]["kind"] == "asset"],
        "nodes": nodes,
        "outputs": [{"id": f"out-{n['id']}", "node_id": n["id"], "port": "image",
                     "format": {"id": "square", "kind": "image", "preset": "square_1080", "width_px": 1080,
                                "height_px": 1080, "mime_type": "image/png", "duration_frames": None,
                                "frame_rate": None, "count": 1, "channel": "feed"}} for n in nodes],
        "budget_ceiling_micros": ceiling, "currency": "USD",
    }
    plan = {
        "schema_version": "cs-execution-plan-v1", "id": str(uuid.uuid4()), "version_id": str(uuid.uuid4()),
        "revision": 1, "workspace_id": ws, "created_at": "2026-10-31T09:00:00Z",
        "created_by": actor or str(uuid.uuid4()), "sha256": "",
        "compile_hash_algorithm": "sha256-rfc8785-cs-program-v1", "compile_hash": digest(program),
        "program": program,
        "sources": {"request": _vref(ws, "creative_request"), "treatment": _vref(ws, "treatment"),
                    "recipe": _vref(ws, "recipe"), "composition": None, "brand_package": _vref(ws, "brand_package"),
                    "catalogue": _vref(ws, "provider_catalogue")},
        "admission": {"budget": {"currency": "USD", "max_cost_micros": ceiling, "funding_source": "workspace",
                                 "credential_binding_id": str(uuid.uuid4()), "policy": _vref(ws, "budget_policy"),
                                 "expires_at": "2099-01-01T00:00:00Z"},
                      "estimated_cost_micros": sum(n["estimated_cost_micros"] for n in nodes),
                      "maximum_cost_micros": total, "expires_at": "2099-01-01T00:00:00Z"},
        "legacy_artifacts": [],
    }
    plan["sha256"] = artifact_digest(plan)
    return plan


def asset_input(port: str, ref: dict) -> dict:
    return {"port": port, "source": {"kind": "asset", "asset": ref}}


def node_output_input(port: str, node_id: str, ordinal: int = 0) -> dict:
    return {"port": port, "source": {"kind": "node_output", "node_id": node_id, "port": "image", "ordinal": ordinal}}
