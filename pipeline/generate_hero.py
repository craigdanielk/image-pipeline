#!/usr/bin/env python3
"""NODE: generate-hero  (the generative core)

Per product: load the CAD spec for its template+image-type, swap metafields from
the matrix row into prompt_template, attach the canonical target render as the
reference image, and drive Higgsfield (CLI) to produce the hero.

  cad/<T>_<imgtype>.json  +  product row  ->  resolved prompt
  canonical target (Drive, via locations.json)  ->  reference image
  higgsfield generate create <model> --prompt <resolved> --image <ref> --wait
  -> out/<slug>__<imgtype>__v<N>.png

--dry-run resolves the prompt and asks Higgsfield ONLY for a cost estimate; it
spends no credits and writes nothing. Always inspect a dry-run before a real run.

Usage:
  python generate_hero.py --slug minoxidil-2-5mg-biotin-capsules --dry-run
  python generate_hero.py --slug minoxidil-2-5mg-biotin-capsules
  python generate_hero.py --slug <slug> --imgtype hero --model nano_banana_2 --version 1
"""
import os, sys, json, glob, argparse, subprocess
import lib

HERE = lib.HERE
OUT = os.path.join(HERE, "out")
REF = os.path.join(HERE, "reference", "canonical")
DEFAULT_MODEL = "nano_banana_2"


def _hf(args, capture=True):
    """Run the higgsfield CLI; return parsed JSON when --json is in args."""
    cmd = ["higgsfield"] + args
    print("  $", " ".join(cmd))
    r = subprocess.run(cmd, capture_output=capture, text=True)
    if r.returncode != 0:
        raise SystemExit(f"higgsfield failed ({r.returncode}):\n{r.stderr or r.stdout}")
    if capture and "--json" in args:
        try:
            return json.loads(r.stdout)
        except json.JSONDecodeError:
            return {"_raw": r.stdout}
    return r.stdout


def ensure_reference(template, cad):
    """Download the canonical target (the look anchor) for this template once."""
    loc = lib.locations()["templates"][template]
    target_id = loc.get("targets", {}).get(cad["image_type"]) or loc.get("blank")
    if not target_id:
        raise SystemExit(f"No canonical reference for {template}/{cad['image_type']} in locations.json")
    dest = os.path.join(REF, f"{template}__{cad['image_type']}__ref.png")
    if not os.path.exists(dest):
        print(f"  fetching canonical reference -> {dest}")
        lib.download(target_id, dest)
    return dest


def next_version(slug, imgtype):
    existing = glob.glob(os.path.join(OUT, f"{slug}__{imgtype}__v*.png"))
    vs = [int(p.rsplit("__v", 1)[1].split(".")[0]) for p in existing if "__v" in p]
    return max(vs, default=0) + 1


def generate(slug=None, imgtype="hero", model=DEFAULT_MODEL, version=None, dry_run=True):
    products = {p["slug"]: p for p in lib.build_products()}
    if slug not in products:
        raise SystemExit(f"Unknown slug '{slug}'. Known: {len(products)} products.")
    prod = products[slug]
    template = prod["template"]
    cad = lib.cad_spec(template, imgtype)
    prompt = lib.resolve_prompt(cad, prod["metafields"])
    model = cad.get("render", {}).get("model") or model

    print(f"\n== generate-hero  slug={slug}  template={template}  imgtype={imgtype}  model={model}")
    print(f"   metafields: {json.dumps(prod['metafields'])}")
    ref = ensure_reference(template, cad)
    print(f"   reference: {ref}")
    print("   --- resolved prompt ---")
    print("   " + prompt)
    print("   -----------------------")

    if dry_run:
        print("\n   DRY RUN — cost estimate only, no credits spent:")
        cost = _hf(["generate", "cost", model, "--prompt", prompt, "--json"])
        print(f"   cost: {cost}")
        return None

    os.makedirs(OUT, exist_ok=True)
    v = version or next_version(slug, imgtype)
    res = _hf(["generate", "create", model, "--prompt", prompt, "--image", ref,
               "--wait", "--json"])
    job = res[0] if isinstance(res, list) else res
    if job.get("status") != "completed" or not job.get("result_url"):
        raise SystemExit(f"   generation not completed: {json.dumps(job)[:300]}")
    dest = os.path.join(OUT, f"{slug}__{imgtype}__v{v}.png")
    _download_url(job["result_url"], dest)
    print(f"   saved -> {dest}  (job {job['id']})")
    return dest


def _download_url(url, dest):
    import urllib.request
    with urllib.request.urlopen(url) as r, open(dest, "wb") as f:
        f.write(r.read())
    return dest


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True)
    ap.add_argument("--imgtype", default="hero")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--version", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    generate(slug=a.slug, imgtype=a.imgtype, model=a.model, version=a.version, dry_run=a.dry_run)
