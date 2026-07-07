#!/usr/bin/env python3
"""WORKFLOW: regenic hero pipeline (the orchestrator).

Flow (n8n-style nodes):
  [validate-locations]  enforce config/locations.json (fail loud on drift)
       |
  [fetch-data]      matrix -> data/products.json        (lib.build_products)
  [fetch-reference] _CANONICAL_TEMPLATES -> reference/  (look anchors)
       |
  run.py  -- per selected product -->
       | resolve CAD spec cad/<T>_<imgtype>.json
       | swap metafields from product row -> prompt_template
       | [generate-hero]  Higgsfield(prompt + canonical reference) -> out/<slug>__<imgtype>__vN.png
       v
  [export]  out/ -> 02_REVIEW/<Tn>/  (--export)

Engine = Higgsfield generative (CAD prompt + canonical target reference).
Retarget by editing config/cad/<T>_<imgtype>.json. No code changes needed.

Usage:
  python run.py --slug minoxidil-2-5mg-biotin-capsules --dry-run      # inspect prompt+cost, no credits
  python run.py --type T2 --dry-run                                   # all T2 products, dry
  python run.py --slug <slug>                                         # real generate
  python run.py --slug <slug> --export                                # generate + upload to REVIEW
"""
import os, argparse
import lib
import validate_locations
import generate_hero


def select(products, type_=None, slug=None, slugs=None, needs_only=False):
    sel = products
    if type_:
        sel = [p for p in sel if p["template"] == type_]
    if slug:
        sel = [p for p in sel if p["slug"] == slug]
    if slugs:
        want = set(s.strip() for s in slugs.split(",") if s.strip())
        sel = [p for p in sel if p["slug"] in want]
    if needs_only:
        sel = [p for p in sel if "NEEDS" in p["image_state"].upper() or p["image_state"] in ("", "NONE")]
    return sel


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--type")
    ap.add_argument("--slug")
    ap.add_argument("--slugs", help="comma-separated slug list")
    ap.add_argument("--imgtype", default="hero")
    ap.add_argument("--model", default=generate_hero.DEFAULT_MODEL)
    ap.add_argument("--needs-only", action="store_true", help="only products whose Image State needs generation")
    ap.add_argument("--dry-run", action="store_true", help="resolve prompt + cost only; no credits, no files")
    ap.add_argument("--export", action="store_true", help="after generate, upload to 02_REVIEW")
    a = ap.parse_args()

    if not (a.slug or a.slugs or a.type or a.needs_only):
        raise SystemExit("specify --slug, --slugs, --type, or --needs-only")

    # NODE 0 — enforce the single location map before anything reads/writes
    validate_locations.assert_ok()
    print("locations OK")

    products = lib.build_products()
    sel = select(products, a.type, a.slug, a.slugs, a.needs_only)
    if not sel:
        raise SystemExit("no products matched selection")
    print(f"selected {len(sel)} product(s)")

    built = []
    for p in sel:
        try:
            generate_hero.generate(slug=p["slug"], imgtype=a.imgtype, model=a.model, dry_run=a.dry_run)
            if not a.dry_run:
                built.append(p["slug"])
        except SystemExit as e:
            print(f"  ! {p['slug']}: {e}")

    if a.export and built:
        import export_heroes, sys
        sys.argv = ["export_heroes.py", "--stage", "review", "--imgtype", a.imgtype,
                    "--slugs", ",".join(built)]
        export_heroes.main()


if __name__ == "__main__":
    main()
