#!/usr/bin/env python3
"""NODE: export
Upload generated heroes to the enforced output location (locations.json),
routed by stage + template:
  --stage review   -> 02_REVIEW/<Tn>/   (default; awaiting sign-off)
  --stage approved -> 03_APPROVED/<Tn>/ (+ writes Final Drive URL + Image State to matrix)

Reads the SINGLE location map (no more config/drive.json). Naming:
<slug>__<imgtype>__v<N>.png. On approve, prior versions in the folder are moved
to 04_ARCHIVE and the matrix row (product_master + product_container_image_data)
gets the Final Drive URL + Image State.

Usage:
  python export_heroes.py --stage review
  python export_heroes.py --stage approved --slug minoxidil-2-5mg-biotin-capsules
"""
import os, glob, argparse, re
from googleapiclient.http import MediaFileUpload
import lib

OUT = os.path.join(lib.HERE, "out")
ARCHIVE_QUERY_NAME = "04_ARCHIVE"


def _archive_folder(d):
    # find 04_ARCHIVE under the drive root's Product Imagery hub
    root = lib.locations()["drive_root"]
    hub = d.files().list(q=f"'{root}' in parents and name='Product Imagery' and trashed=false",
                         fields="files(id)", supportsAllDrives=True).execute()["files"][0]["id"]
    arc = d.files().list(q=f"'{hub}' in parents and name='04_ARCHIVE' and trashed=false",
                         fields="files(id)", supportsAllDrives=True).execute()["files"]
    return arc[0]["id"] if arc else None


def stage_folder(loc, stage, tn):
    key = {"review": "review_folder", "approved": "approved_folder"}[stage]
    return loc["templates"][tn][key]


def next_version(d, folder, slug):
    ex = d.files().list(q=f"'{folder}' in parents and name contains '{slug}__' and trashed=false",
                        fields="files(id,name)", supportsAllDrives=True).execute().get("files", [])
    vers = [int(m.group(1)) for f in ex if (m := re.search(rf"{re.escape(slug)}__\w+__v(\d+)", f["name"]))]
    return (max(vers) + 1 if vers else 1), ex


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=["review", "approved"], default="review")
    ap.add_argument("--slug")
    ap.add_argument("--slugs", help="comma-separated slug list")
    ap.add_argument("--imgtype", default="hero")
    a = ap.parse_args()

    loc = lib.locations()
    d = lib.drive()
    products = {p["slug"]: p for p in lib.build_products()}

    # collect LATEST local version per slug (never upload stale versions)
    want_slugs = set(s.strip() for s in a.slugs.split(",")) if a.slugs else None
    if a.slug:
        want_slugs = {a.slug}
    latest = {}
    for p in glob.glob(os.path.join(OUT, f"*__{a.imgtype}__v*.png")):
        m = re.match(rf"(.+)__{a.imgtype}__v(\d+)\.png$", os.path.basename(p))
        if not m:
            continue
        slug, ver = m.group(1), int(m.group(2))
        if want_slugs and slug not in want_slugs:
            continue
        if slug not in latest or ver > latest[slug][1]:
            latest[slug] = (p, ver)
    files = [v[0] for v in latest.values()]
    links = {}
    archive = _archive_folder(d) if a.stage == "approved" else None
    for path in files:
        slug = re.sub(rf"__{a.imgtype}__v\d+\.png$", "", os.path.basename(path))
        prod = products.get(slug)
        if not prod:
            print(f"  SKIP {slug}: not in matrix"); continue
        tn = prod["template"]
        folder = stage_folder(loc, a.stage, tn)
        ver, existing = next_version(d, folder, slug)
        if a.stage == "approved" and archive:
            for e in existing:
                d.files().update(fileId=e["id"], addParents=archive,
                                 removeParents=folder, supportsAllDrives=True).execute()
        name = f"{slug}__{a.imgtype}__v{ver}.png"
        f = d.files().create(body={"name": name, "parents": [folder]},
                             media_body=MediaFileUpload(path, mimetype="image/png"),
                             fields="id,webViewLink", supportsAllDrives=True).execute()
        links[slug] = f["webViewLink"]
        print(f"  [{a.stage}/{tn}] {name} -> {f['webViewLink']}")

    if a.stage == "approved" and links:
        _writeback(links)
    print(f"\nexported {len(links)} {a.imgtype}(s) [{a.stage}]")


def _writeback(links):
    """Write Final Drive URL + Image State=APPROVED into both routing tabs."""
    sid = lib.matrix_id()
    sh = lib.sheets()
    for tab, slug_col, url_col, state_col in [
        ("product_master", "Slug", "Final Drive URL", "Image State"),
    ]:
        vals = sh.get(spreadsheetId=sid, range=f"{tab}!A1:Z300").execute().get("values", [])
        hdr = vals[0]; ci = {h: i for i, h in enumerate(hdr)}
        if slug_col not in ci:
            continue
        ups = []
        for ri, row in enumerate(vals[1:], start=2):
            slug = row[ci[slug_col]] if len(row) > ci[slug_col] else ""
            if slug in links:
                ups.append({"range": f"{tab}!{_col(ci[url_col])}{ri}", "values": [[links[slug]]]})
                ups.append({"range": f"{tab}!{_col(ci[state_col])}{ri}", "values": [["APPROVED ✓"]]})
        if ups:
            sh.batchUpdate(spreadsheetId=sid, body={"valueInputOption": "RAW", "data": ups}).execute()
            print(f"  matrix[{tab}]: {len(ups)//2} rows -> APPROVED")


def _col(i):
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


if __name__ == "__main__":
    main()
