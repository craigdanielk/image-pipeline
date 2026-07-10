#!/usr/bin/env python3
"""One-shot importer: joga (or any) tenant disk workspace -> Supabase store.

Brief #30201 step 4. Migrates products, shots (job specs), artifacts and their
media off local disk into the cloud store defined by supabase/migrations/:

  knowledge/products/*.json + jobs/**/cad.md   -> products
  jobs/**/*.job.json (+ *.json JobSpecs)        -> shots
  jobs/**/out/*.png|webp|jpg (registry status)  -> artifacts (+ media upload)

Media bytes are uploaded to the `rendered-outputs` Storage bucket under
`<workspace>/<product>/<filename>`; the artifact row records that object key so
the UI serves it via a signed URL (never the disk path).

Idempotent: products/shots/artifacts upsert on their natural keys, so re-running
is safe. Requires SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY.

Usage:
  python tools/import_joga_workspace.py --tenant joga [--dry-run] [--limit N]
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UI_DIR = ROOT / "ui"
for p in (str(UI_DIR), str(ROOT / "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

import cloud_store  # noqa: E402
import artifact_registry  # noqa: E402
import tenants as tenants_mod  # noqa: E402


def _log(msg: str) -> None:
    print(f"[import] {msg}", flush=True)


def _jobs_root(tenant: str) -> Path:
    t = tenants_mod.get_tenant(ROOT, tenant)
    if t and t.get("jobs_root"):
        jr = Path(t["jobs_root"])
        if jr.is_dir():
            return jr
    # fallback to in-repo jobs dir
    return ROOT / "jobs"


def ensure_workspace(tenant: str, dry: bool) -> str | None:
    wid = cloud_store.workspace_id_for_slug(tenant)
    if wid:
        _log(f"workspace '{tenant}' exists ({wid})")
        return wid
    if dry:
        _log(f"[dry-run] would create workspace '{tenant}'")
        return None
    row = cloud_store.insert(
        "workspaces", {"slug": tenant, "name": tenant}, upsert=True, on_conflict="slug"
    )
    _log(f"created workspace '{tenant}' ({row.get('id')})")
    return row.get("id")


def import_products(tenant: str, wid: str | None, jobs_root: Path, dry: bool) -> dict[str, str]:
    """Returns slug -> product_id map."""
    slug_to_id: dict[str, str] = {}
    prod_dir = ROOT / "knowledge" / "products"
    specs: dict[str, dict] = {}
    if prod_dir.is_dir():
        for f in prod_dir.glob("*.json"):
            try:
                spec = json.loads(f.read_text(encoding="utf-8"))
                specs[spec.get("id") or f.stem] = spec
            except Exception as e:  # noqa: BLE001
                _log(f"skip product spec {f.name}: {e}")

    # jobs_only products: any product folder on disk with an out/ dir
    disk_slugs: set[str] = set()
    if jobs_root.is_dir():
        for out_dir in jobs_root.rglob("out"):
            if out_dir.is_dir():
                disk_slugs.add(out_dir.parent.name)

    all_slugs = set(specs) | disk_slugs
    _log(f"products: {len(specs)} spec'd, {len(disk_slugs)} jobs_only, {len(all_slugs)} total")
    for slug in sorted(all_slugs):
        spec = specs.get(slug)
        name = (spec or {}).get("name") or slug.replace("-", " ").title()
        cad_md = None
        cad_path = jobs_root / slug / "cad.md"
        if cad_path.is_file():
            cad_md = cad_path.read_text(encoding="utf-8")
        if dry or not wid:
            _log(f"[dry-run] product {slug} (spec={'y' if spec else 'n'}, cad={'y' if cad_md else 'n'})")
            continue
        row = cloud_store.upsert_product(tenant, slug, name, spec=spec, cad_md=cad_md)
        slug_to_id[slug] = row["id"]
    return slug_to_id


def import_shots(tenant: str, wid: str | None, jobs_root: Path,
                 slug_to_id: dict[str, str], dry: bool) -> int:
    if not jobs_root.is_dir():
        return 0
    count = 0
    for jf in sorted(jobs_root.rglob("*.json")):
        name = jf.name.lower()
        if "/out/" in jf.as_posix() or name in ("cad.json",):
            continue
        try:
            spec = json.loads(jf.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(spec, dict):
            continue
        # heuristic: a JobSpec has a prompt/desired_end_state/output shape
        if not any(k in spec for k in ("prompt_template", "desired_end_state",
                                       "output_requirements", "subject", "engine")):
            continue
        product_slug = jf.parent.name if jf.parent.name != "jobs" else None
        shot_slug = spec.get("id") or spec.get("external_slug") or jf.stem
        if dry or not wid:
            count += 1
            continue
        cloud_store.upsert_shot(
            wid, shot_slug, spec,
            product_id=slug_to_id.get(product_slug or ""),
            prompt_template=spec.get("prompt_template"),
        )
        count += 1
    _log(f"shots: {count} imported")
    return count


def import_artifacts(tenant: str, wid: str | None, jobs_root: Path,
                     slug_to_id: dict[str, str], dry: bool, limit: int | None) -> int:
    rows = artifact_registry.discover_images(jobs_root)
    # overlay review state (status/comment) from the disk registry
    reg = artifact_registry.load_registry(ROOT, tenant).get("artifacts", {})
    _log(f"artifacts: {len(rows)} images discovered on disk")
    count = 0
    for row in rows:
        if limit and count >= limit:
            break
        disk = Path(row["path"])
        slug = row["product_slug"]
        object_key = f"{tenant}/{slug}/{disk.name}"
        overlay = reg.get(row["id"], {})
        status = "pending"
        if overlay.get("approved_at"):
            status = "approved"
        elif overlay.get("deleted_at"):
            status = "rejected"
        comment = overlay.get("comment") or None
        if dry or not wid:
            _log(f"[dry-run] artifact {object_key} status={status}")
            count += 1
            continue
        # upload media -> Storage
        if disk.is_file():
            ctype = mimetypes.guess_type(disk.name)[0] or "image/png"
            try:
                cloud_store.upload_object(
                    cloud_store.OUTPUT_BUCKET, object_key, disk.read_bytes(), ctype
                )
            except cloud_store.CloudStoreError as e:
                _log(f"upload failed {object_key}: {e}")
        art = cloud_store.upsert_artifact(
            wid, object_key, disk.name,
            product_id=slug_to_id.get(slug),
            status=status, comment=comment, source=row.get("source"),
            legacy_disk_path=row["path"],
        )
        if comment:
            cloud_store.add_comment(art["id"], comment)
        count += 1
    _log(f"artifacts: {count} imported")
    return count


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", default="joga")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=None, help="cap artifacts (testing)")
    args = ap.parse_args()

    if not cloud_store.enabled():
        _log("ERROR: SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set")
        return 2

    jobs_root = _jobs_root(args.tenant)
    _log(f"tenant={args.tenant} jobs_root={jobs_root} dry_run={args.dry_run}")

    wid = ensure_workspace(args.tenant, args.dry_run)
    slug_to_id = import_products(args.tenant, wid, jobs_root, args.dry_run)
    import_shots(args.tenant, wid, jobs_root, slug_to_id, args.dry_run)
    import_artifacts(args.tenant, wid, jobs_root, slug_to_id, args.dry_run, args.limit)
    _log("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
