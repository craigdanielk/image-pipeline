#!/usr/bin/env python3
"""Shared helpers for the Regenic image pipeline nodes.

Single source of auth, the enforced location map, matrix reads, metafield
derivation, and Drive download. Every node imports from here so wiring stays DRY
and the location map is never bypassed.
"""
import os, io, re, json

TOKEN = os.path.expanduser("~/.config/regenic-sheets/token.json")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCATIONS = os.path.join(HERE, "config", "locations.json")


# ---- config ---------------------------------------------------------------
def locations():
    with open(LOCATIONS) as f:
        return json.load(f)


def matrix_id():
    return locations()["matrix"]["spreadsheet_id"]


# ---- google services ------------------------------------------------------
def _creds():
    from google.oauth2.credentials import Credentials
    return Credentials.from_authorized_user_file(TOKEN)


def sheets():
    from googleapiclient.discovery import build
    return build("sheets", "v4", credentials=_creds()).spreadsheets().values()


def drive():
    from googleapiclient.discovery import build
    return build("drive", "v3", credentials=_creds())


def read_tab(tab, rng="A1:Z300"):
    """Return list-of-dict rows for a matrix tab (header row = keys)."""
    sid = matrix_id()
    vals = sheets().get(spreadsheetId=sid, range=f"{tab}!{rng}").execute().get("values", [])
    if not vals:
        return []
    hdr = vals[0]
    out = []
    for r in vals[1:]:
        r = r + [""] * (len(hdr) - len(r))
        out.append(dict(zip(hdr, r)))
    return out


# ---- metafield derivation -------------------------------------------------
# dose token = number (commas/decimals ok) + unit, optionally a /unit (e.g. 500mcg/ml, 5mg/2ml)
_DOSE = r"[\d.,]+\s*(?:mg|mcg|g|%|iu|ml)(?:\s*/\s*[\d.]*\s*(?:mg|mcg|g|ml))?"

# Default FORM line per template — fallback ONLY when the label content row has none.
# Never fall back to raw Packaging (it carries container-spec junk).
FORM_BY_TEMPLATE = {
    "T2": "CAPSULES", "T3": "SPRAY", "T4": "FOAM", "T5": "INJECTION",
    "T6": "INJECTION", "T7": "INJECTION", "T8": "INJECTION",
    "T9": "CREAM", "T10": "SERUM",
}


def split_ingredient(piece):
    """'Minoxidil 2.5mg (slow-release formulation)' -> ('Minoxidil', '2.5MG').
    Captures ONLY the numeric dose token; trailing notes/parentheticals are dropped."""
    p = re.sub(r"\(.*?\)", "", piece).strip()                       # drop parentheticals
    m = re.search(rf"^(.*?)\s+({_DOSE})(?!\w)", p, re.I)
    if not m:
        return p, ""
    name = m.group(1).strip()
    dose = re.sub(r"\s+", "", m.group(2)).upper()
    return name, dose


def parse_front(front):
    """FRONT LABEL string -> (name, form, count).
    'Regenic|Lab  ·  [S4]  |  Minoxidil 2.5mg + Biotin 10mg  |  CAPSULES  |  30 capsules'
    Splits on the spaced ' | ' field separator; the bare '|' in 'Regenic|Lab' stays intact."""
    segs = [s.strip() for s in re.split(r"\s+\|\s+", front)]
    body = [s for s in segs if s and "Regenic" not in s and not re.fullmatch(r"\[?S\d\]?\s*·?\s*", s)]
    name = body[0] if len(body) > 0 else ""
    form = body[1] if len(body) > 1 else ""
    count = body[2] if len(body) > 2 else ""
    return name, form, count


def derive_metafields(master_row, content_row):
    """Build the CAD metafields ({product_name},{dose},{form},{schedule_badge},{brand})
    in the canonical-target style: ingredient names joined by '+', doses separate + upper."""
    front = content_row.get("FRONT LABEL (brand: Regenic|Lab)", "") if content_row else ""
    raw_name, form, _count = parse_front(front)
    ingredients = content_row.get("Active Ingredients", "") if content_row else ""
    # fallback when no content row: parse the master Product Name, minus trailing form word
    master_src = re.sub(r"\s+(capsules?|tablets?|spray|foam|injection)\b.*$", "",
                        master_row.get("Product Name", ""), flags=re.I)
    src = ingredients or raw_name or master_src
    names, doses = [], []
    # split on whitespace-bounded ' + ' so compound names keep their literal '+'
    # (e.g. 'NAD+ 333 mg/ml' stays intact; 'CJC-1295 + Ipamorelin' still splits).
    for piece in re.split(r"\s+\+\s+", src):
        n, d = split_ingredient(piece)
        # strip parenthetical like 'Biotin (Vitamin B7)'
        n = re.sub(r"\s*\(.*?\)", "", n).strip()
        if n:
            names.append(n)
        if d:
            doses.append(d)
    # dose fallback: some SKUs carry the strength in Pack Size, not Active Ingredients
    # (e.g. BPC-157 'Pen 2 mg/ml · 3 ml'). Only accept a real STRENGTH token (mg/mcg/%/iu),
    # never a bare volume like '30 ml' (that is pack size, not a dose).
    if not doses and content_row:
        m = re.search(r"[\d.,]+\s*(?:mg|mcg|g|%|iu)(?:\s*/\s*[\d.]*\s*(?:mg|mcg|g|ml))?",
                      content_row.get("Pack Size", ""), re.I)
        if m:
            doses.append(re.sub(r"\s+", "", m.group(0)).upper())
    # product_master.Schedule is the reconciled source of truth; the label-content
    # 'Schedule Badge' column is stale (e.g. Minoxidil 2% = 'OTC' there but 'S2' in master).
    badge = (master_row.get("Schedule", "") or (content_row.get("Schedule Badge") if content_row else "")).strip()
    # Scheduled meds (S2/S3/S4...) carry a badge; general-sale/OTC/S0/Section-21/unscheduled do NOT.
    no_badge = re.sub(r"[^a-z0-9]", "", badge.lower()) in (
        "", "s0", "otc", "none", "s21", "section21", "generalsale", "unscheduled", "unschedule")
    badge_clause = ("top-left " if no_badge
                    else f"top-left a small navy outlined square schedule badge reading '{badge}', and to its right ")
    # FORM fallback: never use raw Packaging (carries container-spec junk like
    # 'Capsule Bottle (Size 00 · 30 caps)'). Default per template instead.
    template = master_row.get("Template", "").strip().upper()
    form_clean = (form or FORM_BY_TEMPLATE.get(template, "")).upper()
    dose = " + ".join(doses)
    # dose_clause: omit the dose line entirely when there is no dose, so the model
    # has nothing to hallucinate (e.g. multivitamins with no single dose).
    dose_clause = (f"below it the dose '{dose}' in Montserrat SemiBold navy, uppercase units; "
                   if dose else "")
    name_final = " + ".join(names) or master_row.get("Product Name", "")
    # strip trailing container/form nouns from the display name (e.g. 'Tirzepatide Pen'
    # -> 'Tirzepatide'); the container is conveyed by the image + form tag, not the name.
    name_final = re.sub(r"\s+(injection\s+)?(cartridge\s+)?pen$", "", name_final, flags=re.I)
    name_final = re.sub(r"\s+(vial|syringe)$", "", name_final, flags=re.I).strip()
    # composition: data-driven dense-label line (peptide pens/vials/syringes).
    # Built from active ingredients + strength; truthful, never invented dosing.
    active = (content_row.get("Active Ingredients", "") if content_row else "") or name_final
    _an = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())
    composition = f"Each unit contains {active}" + (f", {dose}" if dose and _an(dose) not in _an(active) else "") + "."
    # title: ingredient(s) WITH inline strength, vehicle/form words stripped — the
    # combined heading used on topical labels (e.g. 'Finasteride 0.1% + Minoxidil 5%').
    title = re.sub(r"\(.*?\)", "", active)
    title = re.sub(r"\b(topical|solution|spray|foam|vehicle|in)\b", "", title, flags=re.I)
    title = re.sub(r"\s{2,}", " ", title).strip(" +").strip()
    # size: pack volume for the size line (e.g. '50 ml')
    size = ""
    _m = re.search(r"(\d+)\s*ml", (content_row or {}).get("Pack Size", ""), re.I)
    if _m:
        size = f"{_m.group(1)} ml"
    return {
        "product_name": name_final,
        "dose": dose,
        "dose_clause": dose_clause,
        "form": form_clean,
        "title": title,
        "size": size,
        "composition": composition,
        "schedule_badge": badge,
        "badge_code": "" if no_badge else badge,
        "badge_clause": badge_clause,
        "brand": master_row.get("Brand", "Regenic|Lab"),
    }


def _normname(s):
    """Normalize a product name for joining across tabs (the '#' columns are NOT
    row-aligned between product_master and product_label_content)."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _match_content(master_row, content_rows):
    """Pick the richest product_label_content row for a master product.
    product_label_content holds two blocks: canonical detail rows (have a '#',
    full Active Ingredients + dose, e.g. 'Tirzepatide Pen 5mg/2ml') and a tail of
    sparse duplicates (no '#', '1 pen'). master names ('Tirzepatide Pen') don't
    normalize-equal the detail names, so match on Template + longest shared name
    prefix among the '#'-rows. Falls back to exact name match, else None."""
    mn = _normname(master_row.get("Product Name"))
    tmpl = (master_row.get("Template") or "").strip()
    cands = [c for c in content_rows
             if str(c.get("#", "")).strip() and (c.get("Template") or "").strip() == tmpl]
    best, best_score = None, 0
    for c in cands:
        cn = _normname(c.get("Product Name"))
        score = len(os.path.commonprefix([cn, mn]))
        if score > best_score:
            best, best_score = c, score
    if best and best_score >= 3:
        return best
    for c in content_rows:                       # fallback: exact name match
        if _normname(c.get("Product Name")) == mn:
            return c
    return None


def build_products():
    """Join product_master + product_label_content -> list of product dicts with
    slug, template, image_state, and derived metafields. The single data input.
    Join: Template + longest-name-prefix on the canonical '#'-rows (see _match_content)."""
    master = read_tab("product_master")
    content_rows = read_tab("product_label_content")
    out = []
    for m in master:
        if not m.get("Slug"):
            continue
        c = _match_content(m, content_rows)
        out.append({
            "num": m.get("#"),
            "slug": m["Slug"],
            "template": m.get("Template", "").strip(),
            "image_state": m.get("Image State", ""),
            "is_active": m.get("is_active", ""),
            "metafields": derive_metafields(m, c),
        })
    return out


# ---- drive io -------------------------------------------------------------
def download(file_id, dest_path):
    from googleapiclient.http import MediaIoBaseDownload
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    dv = drive()
    fh = io.FileIO(dest_path, "wb")
    dl = MediaIoBaseDownload(fh, dv.files().get_media(fileId=file_id, supportsAllDrives=True))
    done = False
    while not done:
        _, done = dl.next_chunk()
    fh.close()
    return dest_path


def cad_spec(template, imgtype="hero"):
    p = os.path.join(HERE, "config", "cad", f"{template}_{imgtype}.json")
    if not os.path.exists(p):
        raise FileNotFoundError(f"No CAD spec: {p} (run the measurement node for {template} {imgtype})")
    with open(p) as f:
        return json.load(f)


def resolve_prompt(cad, metafields):
    """Swap {metafield} placeholders in the CAD prompt_template."""
    tmpl = cad["prompt_template"]
    for k, v in metafields.items():
        tmpl = tmpl.replace("{" + k + "}", str(v))
    return tmpl
