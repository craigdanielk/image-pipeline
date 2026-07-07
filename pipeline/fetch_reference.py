#!/usr/bin/env python3
"""NODE: fetch-reference  (replaces the old fetch-templates)
Pull the canonical reference images (BLANK + finished target renders) for each
template from _CANONICAL_TEMPLATES via locations.json into reference/canonical/.
These are the look/identity anchors fed to Higgsfield. Source of truth = the
enforced location map, NOT the stale 00_BLANKS folder. Idempotent.

Usage:
  python fetch_reference.py            # all templates, all targets
  python fetch_reference.py --template T2
"""
import os, argparse
import lib

OUT = os.path.join(lib.HERE, "reference", "canonical")


def fetch(template_filter=None):
    loc = lib.locations()["templates"]
    n = 0
    for code, t in loc.items():
        if template_filter and code != template_filter:
            continue
        if t.get("blank"):
            lib.download(t["blank"], os.path.join(OUT, f"{code}__BLANK.png"))
            n += 1
        for imgtype, fid in t.get("targets", {}).items():
            lib.download(fid, os.path.join(OUT, f"{code}__{imgtype}__ref.png"))
            n += 1
        print(f"  {code}: blank + {len(t.get('targets', {}))} target(s)")
    print(f"fetched {n} reference images -> {OUT}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", default=None)
    a = ap.parse_args()
    fetch(a.template)
