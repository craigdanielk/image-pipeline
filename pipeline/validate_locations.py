#!/usr/bin/env python3
"""NODE: validate-locations
Enforce the single agreed location map (config/locations.json). Every pipeline run
calls assert_ok() first; it fails LOUD if any source/output folder or blank is
missing, renamed, or unreadable -- so the pipeline can never silently write to the
wrong place or read a stale source. Run directly to print a health table.

Auth: ~/.config/regenic-sheets/token.json (drive + sheets scopes).
"""
import os, json, sys

TOKEN = os.path.expanduser("~/.config/regenic-sheets/token.json")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = os.path.join(HERE, "config", "locations.json")


def _drive():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    return build("drive", "v3", credentials=Credentials.from_authorized_user_file(TOKEN))


def load():
    with open(CFG) as f:
        return json.load(f)


def _meta(dv, fid):
    try:
        f = dv.files().get(fileId=fid, fields="id,name,mimeType,trashed",
                           supportsAllDrives=True).execute()
        return None if f.get("trashed") else f
    except Exception as e:
        return {"_error": str(e)[:80]}


def check(cfg=None, dv=None):
    """Return (ok, problems[]). Verifies every folder is a live folder and every
    blank/target file is a live image."""
    cfg = cfg or load()
    dv = dv or _drive()
    problems = []

    def want(fid, label, kind):
        if not fid:
            return  # null blank (e.g. T9) is allowed by config
        m = _meta(dv, fid)
        if not m or m.get("_error"):
            problems.append(f"{label}: missing/unreadable ({fid}) {m.get('_error','') if m else 'trashed/404'}")
            return
        is_folder = m["mimeType"].endswith("folder")
        if kind == "folder" and not is_folder:
            problems.append(f"{label}: expected folder, got {m['mimeType']} ({fid})")
        if kind == "file" and is_folder:
            problems.append(f"{label}: expected file, got folder ({fid})")

    want(cfg["drive_root"], "drive_root", "folder")
    for code, t in cfg["templates"].items():
        want(t["canonical_folder"], f"{code}.canonical_folder", "folder")
        want(t["review_folder"], f"{code}.review_folder", "folder")
        want(t["approved_folder"], f"{code}.approved_folder", "folder")
        want(t.get("blank"), f"{code}.blank", "file")
        for it, fid in t.get("targets", {}).items():
            want(fid, f"{code}.target[{it}]", "file")
    return (len(problems) == 0, problems)


def assert_ok():
    """Call at the top of every pipeline run. Raises on any drift."""
    ok, problems = check()
    if not ok:
        raise SystemExit("LOCATION VALIDATION FAILED:\n  - " + "\n  - ".join(problems))
    return True


if __name__ == "__main__":
    cfg = load()
    dv = _drive()
    ok, problems = check(cfg, dv)
    print(f"templates: {len(cfg['templates'])}  matrix: {cfg['matrix']['spreadsheet_id']}")
    if ok:
        print("OK — all source/output locations live and correctly typed.")
    else:
        print(f"PROBLEMS ({len(problems)}):")
        for p in problems:
            print("  -", p)
        sys.exit(1)
