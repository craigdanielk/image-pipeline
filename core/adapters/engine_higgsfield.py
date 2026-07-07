#!/usr/bin/env python3
"""ENGINE adapter: higgsfield — drive the higgsfield CLI.

  create: higgsfield generate create <model> --prompt <p> [--image <ref>]
          [--aspect_ratio ..] [--resolution ..] [--batch_size N]
          [--model_type ..] [--<engine.params>..] --wait --json
  cost:   higgsfield generate cost <model> --prompt <p> [same params] --json

Output requirements map to CLI params here — this is where 'output varies case
to case' becomes real flags. count -> batch_size. Unknown/extra flags ride in
engine.params (escape hatch). --image is passed ONLY when a reference exists;
text-to-image models (e.g. recraft_v4_1) reject it and the error surfaces clean.

SaaS swap: a fal/replicate adapter exposes the same generate() signature and
returns the same normalized list of {result_url}.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import urllib.request

# output-field -> CLI param name. Only universally-accepted scalars are sent
# unconditionally; model-specific params (batch_size, model_type, colors) are
# emitted ONLY when explicitly present, so text-only vs image models don't choke
# on params they don't declare (hardening: nano_banana_2 has no batch_size).
_OUTPUT_PARAM = {
    "aspect_ratio": "aspect_ratio",
    "resolution": "resolution",
}


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
    for k, v in (engine.get("params") or {}).items():
        args += [f"--{k}", v if isinstance(v, str) else json.dumps(v)]
    return args


def _run(args):
    cmd = ["higgsfield"] + args
    print("  $ " + " ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"higgsfield failed ({r.returncode}):\n{r.stderr or r.stdout}")
    if "--json" in args:
        try:
            return json.loads(r.stdout)
        except json.JSONDecodeError:
            return {"_raw": r.stdout}
    return r.stdout


def _upload(path):
    """Upload a local media file; return a `media_input` ref {type,id} the
    generate API accepts inside input_images. (upload returns type='image'; the
    input_images array wants type='media_input' pointing at that media id.)"""
    out = _run(["upload", "create", path, "--json"])
    if isinstance(out, dict) and out.get("id"):
        return {"type": "media_input", "id": out["id"]}
    raise RuntimeError(f"upload returned no media id: {json.dumps(out)[:200]}")


def _resolve_params(engine, job_dir, upload=True):
    """Return a params copy with multi-ref `input_images` normalized.

    Each entry may be a LOCAL PATH (uploaded here -> media object), an http(s)
    URL (wrapped as an image object), or an already-formed object (passthrough).
    The higgsfield API wants an array of media OBJECTS, not path strings — so the
    JobSpec can keep clean local paths and this seam does the upload. Paths
    resolve relative to the job dir. Set upload=False for dry-run (cost is
    upload-independent) — input_images is dropped so no credits/network are used."""
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
            # API no longer accepts {"type":"image","url":...}; must upload first.
            path_part = it.split("?", 1)[0].split("#", 1)[0]
            suffix = os.path.splitext(path_part)[1] or ".jpg"
            fd, tmp = tempfile.mkstemp(suffix=suffix, prefix="ip-input-")
            os.close(fd)
            urllib.request.urlretrieve(it, tmp)
            resolved.append(_upload(tmp))
            os.unlink(tmp)
        elif isinstance(it, str):
            path = it if os.path.isabs(it) else os.path.join(job_dir, it)
            if not os.path.exists(path):
                raise FileNotFoundError(f"input_images path not found: {path}")
            resolved.append(_upload(path))
        else:
            resolved.append(it)  # already a media object
    params["input_images"] = resolved
    return params


def cost(prompt, output, engine, job_dir="."):
    """Dry-run: cost estimate only, no credits, no files, no uploads."""
    model = engine["model"]
    eng = dict(engine)
    eng["params"] = _resolve_params(engine, job_dir, upload=False)
    args = ["generate", "cost", model, "--prompt", prompt] + _param_args(output, eng) + ["--json"]
    return _run(args)


def generate(prompt, reference, output, engine, job_dir="."):
    """Create the job(s), block until finished, return [{result_url, id, ...}]."""
    model = engine["model"]
    eng = dict(engine)
    eng["params"] = _resolve_params(engine, job_dir, upload=True)
    args = ["generate", "create", model, "--prompt", prompt]
    if reference:
        for ref in (reference if isinstance(reference, list) else [reference]):
            args += ["--image", ref]
    args += _param_args(output, eng)
    args += ["--wait", "--json"]
    res = _run(args)
    jobs = res if isinstance(res, list) else [res]
    out = []
    for job in jobs:
        if job.get("status") != "completed" or not job.get("result_url"):
            raise RuntimeError(f"generation not completed: {json.dumps(job)[:300]}")
        out.append(job)
    return out


def _effective_prompt(plan: dict) -> str:
    """Positive prompt; append negative inline when provider lacks native support."""
    prompt_block = plan.get("prompt") or {}
    positive = prompt_block.get("positive") or ""
    negative = prompt_block.get("negative")
    params = plan.get("params") or {}
    if negative and params.get("_negative_appended"):
        return positive.rstrip() + "\n\nAvoid: " + negative
    return positive


def execute_plan(plan, reference, job_dir=".", dry_run=False):
    """Execute a ProviderExecutionPlan via higgsfield CLI."""
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
        return cost(prompt, output, engine, job_dir)
    return generate(prompt, reference, output, engine, job_dir)
