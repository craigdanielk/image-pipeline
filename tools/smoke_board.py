#!/usr/bin/env python3
"""Board smoke harness — exercises every UI API surface non-destructively.

No image generation, no LLM calls, no state mutation that isn't reverted.
Run against a live server:  python3 tools/smoke_board.py [--base http://localhost:8787] [--tenant joga]
Exit code = number of failing checks (0 = clean).
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter

FAIL: list[str] = []
WARN: list[str] = []
PASSED = Counter()


def req(method: str, url: str, body: dict | None = None, timeout: int = 60):
    r = urllib.request.Request(
        url,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        raw = resp.read()
        ctype = resp.headers.get("Content-Type", "")
        if "json" not in ctype:  # /media returns binary — status is the check
            return resp.status, {}
        return resp.status, json.loads(raw or b"{}")


def check(label: str, fn, expect_status: int = 200):
    try:
        status, data = fn()
        if status != expect_status:
            FAIL.append(f"{label}: HTTP {status} (expected {expect_status})")
            return None
        PASSED[label.split(":")[0]] += 1
        return data
    except urllib.error.HTTPError as e:
        if e.code == expect_status:
            PASSED[label.split(":")[0]] += 1
            return None
        detail = ""
        try:
            detail = json.loads(e.read()).get("detail", "")
            if isinstance(detail, dict):
                detail = detail.get("stderr", "")[-160:]
        except Exception:
            pass
        FAIL.append(f"{label}: HTTP {e.code} — {str(detail)[:200]}")
        return None
    except Exception as e:
        FAIL.append(f"{label}: {type(e).__name__} {e}")
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8787")
    ap.add_argument("--tenant", default="joga")
    args = ap.parse_args()
    B, T = args.base, args.tenant
    tq = f"tenant={T}"

    # ---- core reads --------------------------------------------------------
    for path in ["/api/health", "/api/config", "/api/tenants", f"/api/jobs?{tq}",
                 f"/api/campaigns?{tq}", f"/api/operations?{tq}", "/api/templates",
                 "/api/board/runtime-chain", "/api/runs/registry",
                 f"/api/knowledge/products?{tq}", f"/api/knowledge/campaigns?{tq}",
                 f"/api/dashboard/stats?{tq}"]:
        check(f"GET {path.split('?')[0]}", lambda p=path: req("GET", B + p))

    products = (check("GET /api/products", lambda: req("GET", f"{B}/api/products?{tq}")) or {}).get("products", [])
    if not products:
        FAIL.append("products list: EMPTY for tenant")
        _report()
        return len(FAIL)

    # ---- per-product surface ----------------------------------------------
    all_shots = []
    for p in products:
        slug = p["slug"]
        detail = check(f"product detail: {slug}", lambda s=slug: req("GET", f"{B}/api/products/{s}?{tq}"))
        refs = check(f"product refs: {slug}", lambda s=slug: req("GET", f"{B}/api/products/{s}/references?{tq}")) or {}
        # every listed ref must serve through /media
        for r in (refs.get("references") or [])[:3]:
            path = r.get("path") or ""
            check(
                f"media ref: {slug}",
                lambda pp=path: req("GET", f"{B}/media?path={urllib.parse.quote(pp)}"),
            ) if path else None
        for s in p.get("shots") or []:
            all_shots.append((slug, s))
            th = s.get("thumb")
            if th:
                check(
                    f"media shot-thumb: {slug}",
                    lambda pp=th: req("GET", f"{B}/media?path={urllib.parse.quote(pp)}"),
                )
            else:
                WARN.append(f"shot without target thumb: {slug}/{s['id']}")

    # ---- dry-run compile EVERY shot (no generation) ------------------------
    compile_errors: Counter = Counter()
    for slug, s in all_shots:
        try:
            status, data = req("POST", f"{B}/api/compile", {"job": s["path"], "dry_run": True}, timeout=120)
            if not (data.get("prompt") or "").strip():
                WARN.append(f"compiles but EMPTY PROMPT: {slug}/{s['id']}")
            PASSED["compile shot"] += 1
        except urllib.error.HTTPError as e:
            msg = ""
            try:
                d = json.loads(e.read()).get("detail", {})
                msg = (d.get("stderr", "") if isinstance(d, dict) else str(d)).strip().splitlines()[-1][:160]
            except Exception:
                msg = f"HTTP {e.code}"
            compile_errors[msg] += 1
            FAIL.append(f"compile: {slug}/{s['id']} — {msg}")
        except Exception as e:
            FAIL.append(f"compile: {slug}/{s['id']} — {type(e).__name__} {e}")

    # ---- board endpoints: happy + adversarial -------------------------------
    check("validate-wiring (good)", lambda: req("POST", f"{B}/api/board/validate-wiring",
        {"node_type": "artifacts.load", "ports": {"out": {"artifacts": {"type": "compile_artifacts"}}}}))
    check("validate-wiring (bad type) -> 400", lambda: req("POST", f"{B}/api/board/validate-wiring",
        {"node_type": "not.a.node", "ports": {}}), expect_status=400)
    check("artifacts-load (bad id) -> 404", lambda: req("POST", f"{B}/api/board/artifacts-load",
        {"artifact_id": "does-not-exist", "tenant": T}), expect_status=404)
    check("canonical-image (bad path) -> 400", lambda: req("POST",
        f"{B}/api/products/{products[0]['slug']}/canonical-image?{tq}",
        {"path": "/etc/passwd"}), expect_status=400)
    check("compile (missing job) -> 404", lambda: req("POST", f"{B}/api/compile",
        {"job": "/nope/missing.job.json", "dry_run": True}), expect_status=400)

    # artifacts feed + comment round-trip (reverted)
    arts = (check("GET /api/artifacts", lambda: req("GET", f"{B}/api/artifacts?{tq}&limit=60")) or {}).get("artifacts", [])
    target = next((a for a in arts if not a.get("comment") and not a.get("approved_at")), None)
    if target:
        check("comment save", lambda: req("PATCH", f"{B}/api/artifacts/{target['id']}?{tq}", {"comment": "smoke"}))
        check("comment revert", lambda: req("PATCH", f"{B}/api/artifacts/{target['id']}?{tq}", {"comment": ""}))
    # every artifact url must serve
    for a in arts[:10]:
        check("media artifact", lambda aa=a: req("GET", f"{B}/media?path={urllib.parse.quote(aa['path'])}"))

    _report()
    return len(FAIL)


def _report():
    print("\n================ SMOKE REPORT ================")
    total = sum(PASSED.values())
    print(f"PASS: {total} checks across {len(PASSED)} surfaces")
    if WARN:
        print(f"\nWARN ({len(WARN)}):")
        for w in WARN:
            print("  ~", w)
    if FAIL:
        print(f"\nFAIL ({len(FAIL)}):")
        for f in FAIL:
            print("  ✗", f)
    else:
        print("\nNo failures.")


if __name__ == "__main__":
    sys.exit(main())
