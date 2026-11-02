#!/usr/bin/env python3
"""ENGINE adapter: higgsfield — drive the higgsfield CLI. The single Higgsfield adapter.

Classic generation:
  create: higgsfield generate create <model> --prompt <p> [--image <ref>]
          [--aspect_ratio ..] [--resolution ..] [--batch_size N]
          [--model_type ..] [--<engine.params>..] --wait --json
  cost:   higgsfield generate cost <model> --prompt <p> [same params] --json

Marketing Studio (DTC Ads Engine), selected by plan method `marketing_studio.dtc_ads`:
  create: higgsfield marketing-studio dtc-ads generate --prompt <p> --format-id <id>
          --quality low|medium|high [--brand-kit-id ..] [--product ..]
          [--media <media_input_id>:<role> ..≤14] [--aspect-ratio ..] [--resolution ..]
          [--batch-size N] --wait --json
  cost:   the same with --cost-only --json (Higgsfield's own metering; no job is created)

Output requirements map to CLI params here — this is where 'output varies case
to case' becomes real flags. count -> batch_size. Unknown/extra flags ride in
engine.params (escape hatch). --image is passed ONLY when a reference exists;
text-to-image models (e.g. recraft_v4_1) reject it and the error surfaces clean.

Credentials (same function signatures either way; `credential=` is keyword-only):
  * workspace path — the caller resolves a WorkspaceCredential whose
    `credentials_path` is a CLI credentials file materialised from the workspace
    secret store; it reaches the CLI as HIGGSFIELD_CREDENTIALS_PATH (and the
    billing workspace as HIGGSFIELD_WORKSPACE_ID). The adapter never reads or logs it.
  * dev path — no credential and IP_HIGGSFIELD_DEV_CLI_LOGIN=1 uses the machine's
    `higgsfield auth login`. Refused when CS_HOSTED_RUNTIME=1.
  * neither -> ProviderRefusal('provider_credentials_missing').

Charged credits: every create records a `charge` on each result — credits
reported in the job JSON (certainty 'provider_reported'); else the
`account status` balance before/after delta ('balance_delta'); else the
`generate cost` estimate, labelled 'estimated'; else 'unknown' with the reason.
E10 reconciles on `charge.call_id` (one charge per CLI call, not per image,
unless the provider reported it per job).

Marketing Studio format ids, quality->tier map and limits come from a catalogue
file (CS_HIGGSFIELD_MARKETING_CATALOGUE), never from literals here.

SaaS swap: a fal/replicate adapter exposes the same generate() signature and
returns the same normalized list of {result_url}.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import urllib.request
import uuid
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Optional

PROVIDER_ID = "higgsfield"
MARKETING_STUDIO_METHOD = "marketing_studio.dtc_ads"
MARKETING_STUDIO_COST_METHOD = "marketing_studio.dtc_ads.cost_estimate"
CATALOGUE_ENV = "CS_HIGGSFIELD_MARKETING_CATALOGUE"
CATALOGUE_SCHEMA = "cs-higgsfield-marketing-catalogue-v1"
DEV_LOGIN_ENV = "IP_HIGGSFIELD_DEV_CLI_LOGIN"
HOSTED_RUNTIME_ENV = "CS_HOSTED_RUNTIME"
CLI_BIN_ENV = "CS_HIGGSFIELD_BIN"
# Hosted runtimes get the CLI from tools/hf-cli (npm postinstall verifies the release checksum); Vercel ships it
# into the Python functions via includeFiles. Local machines fall back to `higgsfield` on PATH.
_BUNDLED_BIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools", "hf-cli", "node_modules",
                            "@higgsfield", "cli", "vendor", "hf")


_PRIVATE_CLI: dict = {}


def _private_copy(source: str) -> str:
    """Executable copy of `source` inside a per-process private temp dir; created once, never reused from disk."""
    cached = _PRIVATE_CLI.get(source)
    if cached and os.access(cached, os.X_OK):
        return cached
    path = os.path.join(tempfile.mkdtemp(prefix="cs-hf-cli-"), "hf")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700)
    with os.fdopen(fd, "wb") as dst, open(source, "rb") as src:
        dst.write(src.read())
    _PRIVATE_CLI[source] = path
    return path


def cli_binary() -> Optional[str]:
    """Absolute path of the Higgsfield CLI binary this runtime will execute, or None when there is none."""
    import shutil
    for candidate in (os.environ.get(CLI_BIN_ENV), os.path.normpath(_BUNDLED_BIN)):
        if not candidate or not os.path.isfile(candidate):
            continue
        if os.access(candidate, os.X_OK):
            return candidate
        # Function bundles can drop the execute bit. Run an executable copy from a directory this process created
        # (mkdtemp: 0700, unpredictable name), never from a shared, guessable path another user could pre-place.
        return _private_copy(candidate)
    return shutil.which("higgsfield")

_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


class ProviderRefusal(RuntimeError):
    """Typed, user-readable refusal. `code` is stable; `detail` is for humans.
    Subclasses RuntimeError so existing `except RuntimeError` callers still see it."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail

    def to_dict(self) -> dict:
        return {"provider": PROVIDER_ID, "refusal": self.code, "detail": self.detail}


# ------------------------------------------------------------------ credentials

@dataclass(frozen=True)
class WorkspaceCredential:
    """A workspace-scoped Higgsfield credential resolved by the caller (hosted runtime)."""

    workspace_id: str
    credential_id: str
    credentials_path: str
    provider_workspace_id: Optional[str] = None
    mode: str = "workspace_api"


def _cli_env(credential: Optional[WorkspaceCredential]) -> Optional[dict]:
    """Return the subprocess env for this call (None = inherit: dev machine login)."""
    if credential is None:
        if os.environ.get(DEV_LOGIN_ENV) == "1" and os.environ.get(HOSTED_RUNTIME_ENV) != "1":
            return None
        raise ProviderRefusal(
            "provider_credentials_missing",
            "Connect a workspace Higgsfield credential; the machine CLI login is a dev-only path "
            f"({DEV_LOGIN_ENV}=1, never in a hosted runtime).",
        )
    if not isinstance(credential, WorkspaceCredential) or credential.mode != "workspace_api":
        raise ProviderRefusal("provider_credentials_missing",
                              "Credential must be a resolved workspace_api WorkspaceCredential.")
    if not (credential.workspace_id and credential.credential_id and credential.credentials_path):
        raise ProviderRefusal("provider_credentials_missing", "Credential is incomplete.")
    path = credential.credentials_path
    if not os.path.isabs(path) or not os.path.isfile(path):
        raise ProviderRefusal("provider_credentials_missing",
                              f"Workspace credential {credential.credential_id} has no materialised credentials file.")
    if cli_binary() is None:
        raise ProviderRefusal("provider_runtime_unavailable",
                              "The Higgsfield CLI is not installed in this runtime; nothing was sent to the provider.")
    env = dict(os.environ)
    env["HIGGSFIELD_CREDENTIALS_PATH"] = path
    # The CLI keeps config/cache under $HOME; hosted function homes are read-only, so give it the private temp dir
    # that already holds the materialised credential (removed after the call).
    env["HOME"] = os.path.dirname(path)
    env["HIGGSFIELD_DISABLE_TELEMETRY"] = "1"
    if credential.provider_workspace_id:
        env["HIGGSFIELD_WORKSPACE_ID"] = credential.provider_workspace_id
    else:
        env.pop("HIGGSFIELD_WORKSPACE_ID", None)
    env["HIGGSFIELD_NO_UPDATE_CHECK"] = "1"
    return env

# --------------------------------------------------------------- CLI plumbing

# output-field -> CLI param name. Only universally-accepted scalars are sent
# unconditionally; model-specific params (batch_size, model_type, colors) are
# emitted ONLY when explicitly present, so text-only vs image models don't choke
# on params they don't declare (hardening: nano_banana_2 has no batch_size).
_OUTPUT_PARAM = {
    "aspect_ratio": "aspect_ratio",
    "resolution": "resolution",
}


def _image_ref_args(imgs):
    """Current CLI (0.2.3+) takes --image-references <path|uuid>, not --input_images JSON."""
    args = []
    for it in imgs:
        if isinstance(it, str):
            args += ["--image-references", it]
        elif isinstance(it, dict):
            rid = it.get("id") or it.get("image")
            if rid:
                args += ["--image-references", str(rid)]
    return args


def _param_args(output, engine):
    args = []
    for out_key, cli_key in _OUTPUT_PARAM.items():
        val = output.get(out_key)
        if val not in (None, ""):
            args += [f"--{cli_key}", str(val)]
    count = output.get("count")
    if count and int(count) > 1:
        args += ["--batch_size", str(count)]
    if engine.get("model_type"):
        args += ["--model_type", str(engine["model_type"])]
    colors = output.get("colors")
    if colors:
        args += ["--colors", json.dumps(colors)]
    params = dict(engine.get("params") or {})
    imgs = params.pop("input_images", None) or params.pop("image_references", None)
    for k, v in params.items():
        if k.startswith("_") or k in _OUTPUT_PARAM:
            continue
        args += [f"--{k}", v if isinstance(v, str) else json.dumps(v)]
    if isinstance(imgs, list):
        args += _image_ref_args(imgs)
    return args


def _run(args, env=None):
    shown = []
    i = 0
    while i < len(args):
        if args[i] == "--prompt" and i + 1 < len(args):
            shown += ["--prompt", "<%d chars>" % len(args[i + 1])]
            i += 2
            continue
        shown.append(args[i])
        i += 1
    cmd = [cli_binary() or "higgsfield"] + args
    print("  $ higgsfield " + " ".join(shown))
    r = subprocess.run(cmd, capture_output=True, text=True, env=env)
    if r.returncode != 0:
        raise RuntimeError(f"higgsfield failed ({r.returncode}):\n{r.stderr or r.stdout}")
    if "--json" in args:
        try:
            return json.loads(r.stdout)
        except json.JSONDecodeError:
            raise RuntimeError(
                "higgsfield returned non-JSON (exit 0):\n"
                + (r.stderr or r.stdout or "")[:800]
            )
    return r.stdout


def _upload(path, env=None):
    """Upload a local media file; return a `media_input` ref {type,id} the
    generate API accepts inside input_images. (upload returns type='image'; the
    input_images array wants type='media_input' pointing at that media id.)"""
    out = _run(["upload", "create", path, "--json"], env)
    if isinstance(out, dict) and out.get("id"):
        return {"type": "media_input", "id": out["id"]}
    raise RuntimeError(f"upload returned no media id: {json.dumps(out)[:200]}")


def _resolve_params(engine, job_dir, upload=True):
    """Return a params copy with multi-ref `input_images` as local paths.

    Higgsfield CLI 0.2.3+ auto-uploads --image-references paths. We resolve
    relative paths against job_dir and leave them as strings. HTTP URLs are
    downloaded to a temp file first. Set upload=False for dry-run (cost is
    upload-independent) — input_images is dropped so no credits/network are used.
    """
    params = dict(engine.get("params") or {})
    imgs = params.get("input_images")
    if not isinstance(imgs, list):
        return params
    if not upload:
        params.pop("input_images", None)
        return params
    resolved = []
    for it in imgs:
        if isinstance(it, str) and it.startswith(("http://", "https://")):
            path_part = it.split("?", 1)[0].split("#", 1)[0]
            suffix = os.path.splitext(path_part)[1] or ".jpg"
            fd, tmp = tempfile.mkstemp(suffix=suffix, prefix="ip-input-")
            os.close(fd)
            urllib.request.urlretrieve(it, tmp)
            resolved.append(tmp)
        elif isinstance(it, str):
            path = it if os.path.isabs(it) else os.path.join(job_dir, it)
            if not os.path.exists(path):
                raise FileNotFoundError(f"input_images path not found: {path}")
            resolved.append(os.path.abspath(path))
        elif isinstance(it, dict) and (it.get("id") or it.get("image")):
            resolved.append(it.get("id") or it.get("image"))
        else:
            resolved.append(it)
    params["input_images"] = resolved
    return params


# ------------------------------------------------------------ charge capture

# Job-JSON keys that state credits actually charged for that job. The 1.1.26
# `--wait --json` job shape is not yet recorded; this list is the one place to
# correct once an operator snapshot exists. `cost` is deliberately NOT read here
# (it may be an estimate).
_JOB_CHARGE_KEYS = ("credits_charged", "charged_credits", "credits_spent", "credits_used")
# `account status --json` balance keys (same caveat).
_BALANCE_KEYS = ("credits", "available_credits", "credits_available", "balance")


def _decimal(val) -> Optional[Decimal]:
    if isinstance(val, bool) or not isinstance(val, (int, float, str)):
        return None
    try:
        d = Decimal(str(val))
    except InvalidOperation:
        return None
    return d if d.is_finite() and d >= 0 else None


def _job_reported_credits(job) -> Optional[Decimal]:
    if not isinstance(job, dict):
        return None
    for node in (job, job.get("billing") if isinstance(job.get("billing"), dict) else None):
        if not node:
            continue
        for k in _JOB_CHARGE_KEYS:
            if k in node:
                d = _decimal(node[k])
                if d is not None:
                    return d
    return None


def cli_version() -> Optional[str]:
    """`higgsfield --version` of the binary this runtime would execute (no account, no network); None if it cannot run."""
    binary = cli_binary()
    if binary is None:
        return None
    try:
        r = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=15,
                           env=dict(os.environ, HOME=tempfile.gettempdir(), HIGGSFIELD_NO_UPDATE_CHECK="1",
                                    HIGGSFIELD_DISABLE_TELEMETRY="1"))
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = (r.stdout or "").strip().splitlines()
    return out[0][:80] if r.returncode == 0 and out else None


def account_status(credential: WorkspaceCredential) -> dict:
    """Free check of a workspace login: `account status --json` creates no job and charges nothing."""
    out = _run(["account", "status", "--json"], _cli_env(credential))
    if not isinstance(out, dict):
        raise RuntimeError("higgsfield account status returned no object")
    return out


def _balance_from(out) -> Decimal:
    nodes = [out]
    if isinstance(out, dict):
        nodes += [out.get(k) for k in ("account", "credits", "balance") if isinstance(out.get(k), dict)]
    for node in nodes:
        if not isinstance(node, dict):
            continue
        for k in _BALANCE_KEYS + ("available",):
            if k in node:
                d = _decimal(node[k])
                if d is not None:
                    return d
    raise RuntimeError(f"account status returned no readable credit balance: {json.dumps(out)[:200]}")


def _read_balance(env):
    """(balance, None) or (None, reason). The reason is carried into the charge record."""
    try:
        return _balance_from(_run(["account", "status", "--json"], env)), None
    except Exception as exc:  # recorded in the charge, never dropped
        return None, f"balance_unreadable: {type(exc).__name__}: {str(exc)[:240]}"


def _credits_from_cost(res) -> Decimal:
    node = res
    if isinstance(node, dict) and isinstance(node.get("cost"), dict):
        node = node["cost"]
    if isinstance(node, dict):
        for k in ("credits", "credits_exact"):
            d = _decimal(node.get(k))
            if d is not None:
                return d
    raise RuntimeError(f"cost estimate returned no credits: {json.dumps(res)[:200]}")


def _metered_create(args, env, estimate: Callable[[], Any], batch_size: int = 1):
    """Run a create call and attach the actually charged credits to every job.

    Order (operator amendment 1, item 2): job JSON -> balance delta -> estimate.
    """
    call_id = str(uuid.uuid4())
    before, before_err = _read_balance(env)
    res = _run(args, env)
    jobs = res.get("jobs") if isinstance(res, dict) and isinstance(res.get("jobs"), list) else res
    jobs = jobs if isinstance(jobs, list) else [jobs]

    reported = [_job_reported_credits(j) for j in jobs]
    if jobs and all(c is not None for c in reported):
        for job, credits in zip(jobs, reported):
            if isinstance(job, dict):
                job["charge"] = {"certainty": "provider_reported", "scope": "job", "call_id": call_id,
                                 "credits": str(credits), "source": "job_json"}
        return jobs

    job_ids = [j.get("id") for j in jobs if isinstance(j, dict)]
    charge: dict = {"scope": "call", "call_id": call_id, "job_ids": job_ids, "batch_size": batch_size}
    reason = before_err
    if before is not None:
        after, after_err = _read_balance(env)
        if after is not None and before - after >= 0:
            charge.update(certainty="balance_delta", credits=str(before - after), source="account_status_delta",
                          balance_before=str(before), balance_after=str(after),
                          caveat="attributable only if nothing else spent on this Higgsfield account/workspace "
                                 "during the call; reconcile against `account transactions`")
        else:
            reason = after_err or f"balance_delta_not_attributable: before={before} after={after}"
    if "certainty" not in charge:
        charge["fallback_reason"] = reason or "job JSON carried no charged credits"
        try:
            charge.update(certainty="estimated", credits=str(_credits_from_cost(estimate())),
                          source="generate_cost_estimate")
        except Exception as exc:  # recorded, the paid results are still returned
            charge.update(certainty="unknown", credits=None,
                          estimate_error=f"{type(exc).__name__}: {str(exc)[:240]}")
    for job in jobs:
        if isinstance(job, dict):
            job["charge"] = dict(charge)
    return jobs


# ------------------------------------------------------------- classic models

def _classic_cost_args(prompt, output, engine, job_dir):
    eng = dict(engine)
    eng["params"] = _resolve_params(engine, job_dir, upload=False)
    return ["generate", "cost", engine["model"], "--prompt", prompt] + _param_args(output, eng) + ["--json"]


def cost(prompt, output, engine, job_dir=".", *, credential=None):
    """Dry-run: cost estimate only, no credits, no files, no uploads."""
    env = _cli_env(credential)
    return _run(_classic_cost_args(prompt, output, engine, job_dir), env)


def generate(prompt, reference, output, engine, job_dir=".", *, credential=None):
    """Create the job(s), block until finished, return [{result_url, id, charge, ...}]."""
    env = _cli_env(credential)
    model = engine["model"]
    eng = dict(engine)
    eng["params"] = _resolve_params(engine, job_dir, upload=True)
    args = ["generate", "create", model, "--prompt", prompt]
    if reference:
        for ref in (reference if isinstance(reference, list) else [reference]):
            args += ["--image", ref]
    args += _param_args(output, eng)
    args += ["--wait", "--json"]
    jobs = _metered_create(args, env, lambda: _run(_classic_cost_args(prompt, output, engine, job_dir), env),
                           batch_size=int(output.get("count") or 1))
    out = []
    for job in jobs:
        if not isinstance(job, dict) or job.get("status") != "completed" or not job.get("result_url"):
            raise RuntimeError(f"generation not completed: {json.dumps(job)[:300]}")
        out.append(job)
    return out

# ----------------------------------------------- Marketing Studio: catalogue

@dataclass(frozen=True)
class Catalogue:
    raw: dict
    sha256: str
    quality_tiers: dict = field(default_factory=dict)
    formats: dict = field(default_factory=dict)
    aspect_ratios: tuple = ()
    resolutions: tuple = ()
    references_max: int = 0
    batch_size_max: int = 0
    default_quality: dict = field(default_factory=dict)

    def tier_for(self, quality: str) -> str:
        if quality not in self.quality_tiers:
            raise ProviderRefusal("quality_unknown", f"Quality '{quality}' is not in the catalogue.")
        return self.quality_tiers[quality]

    def format(self, format_id: str) -> dict:
        if format_id not in self.formats:
            raise ProviderRefusal("ad_format_unknown", f"Ad format '{format_id}' is not in the catalogue.")
        return self.formats[format_id]


def _sha256(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_catalogue(path: Optional[str] = None) -> Catalogue:
    path = path or os.environ.get(CATALOGUE_ENV)
    if not path:
        raise ProviderRefusal("provider_catalogue_missing", f"Set {CATALOGUE_ENV} or pass a catalogue path.")
    if not os.path.isfile(path):
        raise ProviderRefusal("provider_catalogue_missing", f"Catalogue file not found: {path}")
    with open(path, "r", encoding="utf-8") as fh:
        try:
            raw = json.load(fh)
        except json.JSONDecodeError as exc:
            raise ProviderRefusal("provider_catalogue_invalid", f"Catalogue is not JSON: {exc}") from exc
    return parse_catalogue(raw)


def parse_catalogue(raw: dict) -> Catalogue:
    def bad(msg: str) -> ProviderRefusal:
        return ProviderRefusal("provider_catalogue_invalid", msg)

    if not isinstance(raw, dict) or raw.get("schema_version") != CATALOGUE_SCHEMA:
        raise bad(f"schema_version must be {CATALOGUE_SCHEMA}")
    if raw.get("provider") != PROVIDER_ID or raw.get("surface") != "marketing_studio":
        raise bad(f"provider must be {PROVIDER_ID} and surface marketing_studio")
    tiers = raw.get("quality_tiers")
    if not isinstance(tiers, dict) or not tiers or any(t not in ("draft", "final") for t in tiers.values()):
        raise bad("quality_tiers must map quality -> draft|final")
    if "final" not in tiers.values() or "draft" not in tiers.values():
        raise bad("quality_tiers must declare at least one draft and one final quality")
    formats = raw.get("ad_formats")
    if not isinstance(formats, list) or not formats:
        raise bad("ad_formats must be a non-empty list")
    by_id = {}
    for f in formats:
        if not isinstance(f, dict) or not _UUID.match(str(f.get("id", ""))) or not f.get("type"):
            raise bad("each ad format needs a uuid id and a type")
        by_id[f["id"]] = f
    limits = raw.get("limits") or {}
    for key in ("references_max", "batch_size_max"):
        if not isinstance(limits.get(key), int) or limits[key] < 1:
            raise bad(f"limits.{key} must be a positive integer")
        if not (limits.get("source") or {}).get(key):
            raise bad(f"limits.source.{key} must cite where the limit comes from")
    defaults = raw.get("tier_defaults") or {}
    for tier in ("draft", "final"):
        q = defaults.get(tier)
        if q not in tiers or tiers[q] != tier:
            raise bad(f"tier_defaults.{tier} must name a quality mapped to {tier}")
    return Catalogue(
        raw=raw, sha256=_sha256(raw), quality_tiers=dict(tiers), formats=by_id,
        aspect_ratios=tuple(raw.get("aspect_ratios") or ()), resolutions=tuple(raw.get("resolutions") or ()),
        references_max=limits["references_max"], batch_size_max=limits["batch_size_max"],
        default_quality=dict(defaults),
    )


def _resolve_catalogue(catalogue) -> Catalogue:
    return catalogue if isinstance(catalogue, Catalogue) else load_catalogue(catalogue)


def ad_formats(catalogue=None, *, type: Optional[str] = None) -> list:
    """Ad format presets from the catalogue file (an operator snapshot of
    `marketing-studio ad-formats list --json`). No CLI call."""
    cat = _resolve_catalogue(catalogue)
    out = [dict(f) for f in cat.formats.values()]
    return [f for f in out if f.get("type") == type] if type else out


# ---------------------------------------------- Marketing Studio: brand kit

_BRAND_KIT_FIELDS = ("name", "tagline", "colours", "fonts", "logo_url", "tone", "values")


def validate_brand_kit_payload(payload: dict) -> dict:
    """Validate the Brand CI (ENG-03b) export payload and return a normalised copy."""
    def bad(msg):
        return ProviderRefusal("brand_kit_payload_invalid", msg)

    if not isinstance(payload, dict):
        raise bad("Brand kit payload must be an object.")
    url_keys = {"url", "source_url", "website", "fetch_url"} & set(payload)
    if url_keys:
        raise ProviderRefusal(
            "brand_kit_url_source_forbidden",
            "Brand kits are pushed from the approved Brand CI export, not fetched from a website "
            f"(remove {sorted(url_keys)}).",
        )
    missing = [k for k in _BRAND_KIT_FIELDS if k not in payload]
    if missing:
        raise bad(f"Missing fields: {', '.join(missing)}")
    if not isinstance(payload["name"], str) or not payload["name"].strip():
        raise bad("name must be a non-empty string")
    if payload["tagline"] is not None and not isinstance(payload["tagline"], str):
        raise bad("tagline must be a string or null")
    colours = payload["colours"]
    if not isinstance(colours, list) or not colours:
        raise bad("colours must be a non-empty list")
    for c in colours:
        if not isinstance(c, dict) or not _HEX.match(str(c.get("hex", ""))) or not c.get("role"):
            raise bad("each colour needs hex (#RRGGBB) and role")
    fonts = payload["fonts"]
    if not isinstance(fonts, list) or not fonts:
        raise bad("fonts must be a non-empty list")
    for f in fonts:
        if not isinstance(f, dict) or not f.get("family") or not f.get("role"):
            raise bad("each font needs family and role")
    logo = payload["logo_url"]
    if not isinstance(logo, str) or not logo.startswith("https://"):
        raise bad("logo_url must be an https URL to the stored logo file")
    tone = payload["tone"]
    if not (isinstance(tone, str) and tone.strip()) and not (
            isinstance(tone, list) and tone and all(isinstance(t, str) for t in tone)):
        raise bad("tone must be a string or list of strings")
    values = payload["values"]
    if not isinstance(values, list) or not all(isinstance(v, str) and v.strip() for v in values):
        raise bad("values must be a list of strings")
    normalised = {k: payload[k] for k in _BRAND_KIT_FIELDS}
    if payload.get("source_version"):
        normalised["source_version"] = payload["source_version"]
    return normalised


def brand_kit_upsert(payload: dict, *, credential=None) -> dict:
    """Push our approved brand kit (ours feeds theirs). Validates, resolves the
    credential, then refuses honestly: Higgsfield CLI 1.1.26 `marketing-studio
    brand-kits` has only fetch --url / get / list — no create-from-payload — and
    `fetch --url` is never our source of truth."""
    body = validate_brand_kit_payload(payload)
    _cli_env(credential)
    raise ProviderRefusal(
        "provider_operation_unavailable",
        "Higgsfield CLI marketing-studio brand-kits offers no create/update from a payload (only fetch --url, "
        f"get, list). Payload {_sha256(body)[:12]} is valid; create the kit from it in Higgsfield and pass its id "
        "as brand_kit_id.",
    )


# -------------------------------------------- Marketing Studio: DTC ads

def _media_refs(references, cat: Catalogue) -> list:
    """Normalise references to [{"id"|"path", "role"}] and enforce the catalogue limit."""
    refs = list(references or [])
    if len(refs) > cat.references_max:
        raise ProviderRefusal("references_exceed_limit",
                              f"{len(refs)} references; the catalogue limit is {cat.references_max}.")
    out = []
    for r in refs:
        role = "image"
        if isinstance(r, dict):
            role = r.get("role") or "image"
            r = r.get("id") or r.get("path") or r.get("image")
        if not isinstance(r, str) or not r.strip() or ":" in role:
            raise ProviderRefusal("reference_invalid", "references must be media ids, paths, URLs or {id|path, role}")
        out.append({"id": r, "role": role} if _UUID.match(r) else {"path": r, "role": role})
    return out


def _media_args(refs: list, job_dir: str, env, upload: bool) -> tuple:
    """--media args. Paths/URLs go through _resolve_params + _upload (dtc-ads --media
    takes media_input ids only). For cost (upload=False) they are left out — cost is
    upload-independent, as for classic cost()."""
    args, ids = [], []
    for ref in refs:
        mid = ref.get("id")
        if mid is None:
            if not upload:
                continue
            local = _resolve_params({"params": {"input_images": [ref["path"]]}}, job_dir, upload=True)["input_images"][0]
            mid = _upload(local, env)["id"]
        ids.append(mid)
        args += ["--media", f"{mid}:{ref['role']}"]
    return args, ids


def _dtc_base(format_id, prompt, brand_kit_id, product, quality, aspect_ratio, resolution, batch_size, cat):
    fmt = cat.format(format_id)
    tier = cat.tier_for(quality)
    if not isinstance(prompt, str) or not prompt.strip():
        raise ProviderRefusal("prompt_missing", "The compiled ProviderPrompt is required.")
    if brand_kit_id is not None and not _UUID.match(str(brand_kit_id)):
        raise ProviderRefusal("brand_kit_id_invalid", "brand_kit_id must be a provider uuid")
    if aspect_ratio is not None and cat.aspect_ratios and aspect_ratio not in cat.aspect_ratios:
        raise ProviderRefusal("aspect_ratio_unsupported", f"{aspect_ratio} is not in the catalogue.")
    if resolution is not None and cat.resolutions and resolution not in cat.resolutions:
        raise ProviderRefusal("resolution_unsupported", f"{resolution} is not in the catalogue.")
    if isinstance(batch_size, bool) or not isinstance(batch_size, int) or not 1 <= batch_size <= cat.batch_size_max:
        raise ProviderRefusal("batch_size_invalid", f"batch_size must be 1..{cat.batch_size_max}")
    args = ["marketing-studio", "dtc-ads", "generate", "--prompt", prompt,
            "--format-id", format_id, "--quality", quality, "--batch-size", str(batch_size)]
    if brand_kit_id:
        args += ["--brand-kit-id", brand_kit_id]
    if product:
        args += ["--product", product]
    if aspect_ratio:
        args += ["--aspect-ratio", aspect_ratio]
    if resolution:
        args += ["--resolution", resolution]
    return args, {"tier": tier, "format": fmt}


def dtc_ad_cost(format_id, prompt, *, brand_kit_id=None, product=None, references=None, quality=None,
                tier=None, aspect_ratio=None, resolution=None, batch_size=1, job_dir=".", credential=None,
                catalogue=None):
    """Credit estimate via `dtc-ads generate --cost-only --json` (Higgsfield's own
    metering). No job, no uploads. Returns the CLI JSON, like cost()."""
    cat = _resolve_catalogue(catalogue)
    quality = quality or (cat.default_quality.get(tier) if tier else None)
    if not quality:
        raise ProviderRefusal("quality_unknown", "Pass quality or a tier with a catalogue default.")
    args, _ = _dtc_base(format_id, prompt, brand_kit_id, product, quality, aspect_ratio, resolution, batch_size, cat)
    refs = _media_refs(references, cat)
    env = _cli_env(credential)
    media, _ = _media_args(refs, job_dir, env, upload=False)
    return _run(args + media + ["--cost-only", "--json"], env)


def dtc_ad_generate(format_id, brand_kit_id, product, references, quality, *, prompt, aspect_ratio=None,
                    resolution=None, batch_size=1, job_dir=".", credential=None, catalogue=None):
    """Generate branded ad image(s), block until complete, return normalised results with `charge`."""
    cat = _resolve_catalogue(catalogue)
    args, meta = _dtc_base(format_id, prompt, brand_kit_id, product, quality, aspect_ratio, resolution,
                           batch_size, cat)
    refs = _media_refs(references, cat)
    env = _cli_env(credential)
    media, media_ids = _media_args(refs, job_dir, env, upload=True)
    create = args + media + ["--wait", "--json"]
    cost_args = args + media + ["--cost-only", "--json"]
    request_sha = _sha256([a for a in create if a != prompt] + [_sha256(prompt)])
    jobs = _metered_create(create, env, lambda: _run(cost_args, env), batch_size=batch_size)
    out = []
    for job in jobs:
        if not isinstance(job, dict):
            raise ProviderRefusal("provider_response_invalid", "generation returned a non-object job")
        results = job.get("results") if isinstance(job.get("results"), list) else None
        urls = [r.get("url") for r in results if isinstance(r, dict)] if results else [job.get("result_url")]
        if job.get("status") != "completed" or not urls or not all(
                isinstance(u, str) and u.startswith("https://") for u in urls):
            raise ProviderRefusal("generation_not_completed",
                                  f"job {job.get('id')} status={job.get('status')!r} charge={json.dumps(job.get('charge'))}")
        for u in urls:
            out.append({
                "provider": PROVIDER_ID, "surface": "marketing_studio", "id": job.get("id"),
                "status": "completed", "result_url": u, "tier": meta["tier"], "quality": quality,
                "format_id": format_id, "brand_kit_id": brand_kit_id, "reference_media_ids": media_ids,
                "request_sha256": request_sha, "catalogue_sha256": cat.sha256,
                "text_model_drawn": True, "charge": job.get("charge"),
            })
    if len(out) != batch_size:
        raise ProviderRefusal("provider_response_invalid", f"expected {batch_size} results, got {len(out)}")
    return out


# ------------------------------------------------------------ provider seam

def _effective_prompt(plan: dict) -> str:
    """Positive prompt; append negative inline when provider lacks native support."""
    prompt_block = plan.get("prompt") or {}
    positive = prompt_block.get("positive") or ""
    negative = prompt_block.get("negative")
    params = plan.get("params") or {}
    if negative and params.get("_negative_appended"):
        return positive.rstrip() + "\n\nAvoid: " + negative
    return positive


def _execute_marketing_studio(plan, reference, job_dir, dry_run, credential, catalogue):
    params = dict(plan.get("params") or {})
    output = plan.get("output") or {}
    refs = list(plan.get("media_inputs") or [])
    if reference:
        refs += list(reference if isinstance(reference, list) else [reference])
    quality = params.get("quality")
    if not quality:
        raise ProviderRefusal("quality_unknown", "plan.params.quality is required for Marketing Studio")
    a = dict(format_id=params.get("format_id") or plan.get("model"), prompt=_effective_prompt(plan),
             brand_kit_id=params.get("brand_kit_id"), product=params.get("product"), references=refs,
             aspect_ratio=output.get("aspect_ratio") or params.get("aspect_ratio"),
             resolution=output.get("resolution") or params.get("resolution"),
             batch_size=int(output.get("count") or params.get("batch_size") or 1))
    if dry_run or plan.get("method") == MARKETING_STUDIO_COST_METHOD:
        return dtc_ad_cost(a.pop("format_id"), a.pop("prompt"), quality=quality, job_dir=job_dir,
                           credential=credential, catalogue=catalogue, **a)
    return dtc_ad_generate(a.pop("format_id"), a.pop("brand_kit_id"), a.pop("product"), a.pop("references"),
                           quality, job_dir=job_dir, credential=credential, catalogue=catalogue, **a)


def execute_plan(plan, reference, job_dir=".", dry_run=False, *, credential=None, catalogue=None):
    """Execute a ProviderExecutionPlan via the higgsfield CLI.

    plan.method `marketing_studio.dtc_ads` (or `…cost_estimate`) selects Marketing
    Studio; anything else is classic `generate`. `credential` is keyword-only and
    supplied by the hosted runtime; without it only the dev CLI login can run.
    """
    if plan.get("method") in (MARKETING_STUDIO_METHOD, MARKETING_STUDIO_COST_METHOD):
        return _execute_marketing_studio(plan, reference, job_dir, dry_run, credential, catalogue)
    engine = {
        "name": plan["provider"],
        "model": plan["model"],
        "model_type": (plan.get("params") or {}).get("model_type"),
        "params": {
            k: v
            for k, v in (plan.get("params") or {}).items()
            if not k.startswith("_")
        },
    }
    output = plan.get("output") or {}
    prompt = _effective_prompt(plan)
    if dry_run or plan.get("method") == "cost_estimate":
        return cost(prompt, output, engine, job_dir, credential=credential)
    return generate(prompt, reference, output, engine, job_dir, credential=credential)
