"""Builds the CS-B4 provisional fixtures from contract text only (no upstream B2 fixture exists yet).

Run from repo root:  python3 -m tests.creative_studio.b4_fixtures.make_fixtures
Writes deterministic JSON next to this file. Business is invented and generic.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

from service.composition.canonical import artifact_sha256

HERE = Path(__file__).resolve().parent
ASSETS = HERE / "assets"
TS = "2026-10-31T09:00:00Z"
FAR = "2030-01-01T00:00:00Z"


def uid(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"cs-b4-fixture:{name}"))


STUDIO_WS = uid("studio-workspace")
BCI_ACCOUNT = uid("bci-account")
MOS_WS = uid("mos-workspace")
ACTOR = uid("producer-user")
BCI_OWNER = uid("bci-owner")

FILES = {
    "logo": ("example-logo-primary.png", "image/png"),
    "font": ("aileron-regular.ttf", "font/ttf"),
    "scene": ("example-generated-scene.png", "image/png"),
    "guide_pdf": ("example-guide.pdf", "application/pdf"),
}


def sha(name: str) -> str:
    return hashlib.sha256((ASSETS / FILES[name][0]).read_bytes()).hexdigest()


def asset_ref(name: str, authority: str, workspace: str) -> dict:
    asset_id = uid(f"{authority}:{name}")
    return {"authority": authority, "workspace_id": workspace, "asset_id": asset_id, "asset_version_id": asset_id,
            "revision": 1, "sha256": sha(name), "mime_type": FILES[name][1]}


def rights(name: str, status: str, basis: str, uses: list[str], holder: str | None, attribution: str | None,
           recorder: str) -> dict:
    r = {"receipt_id": uid(f"rights:{recorder}:{name}"), "revision": 1, "sha256": "", "status": status,
         "holder": holder, "basis": basis, "evidence": [], "allowed_uses": uses, "channels": ["*"],
         "territories": ["*"], "valid_from": "2026-01-01T00:00:00Z", "expires_at": FAR, "attribution": attribution,
         "restrictions": [], "recorded_by": recorder}
    r["sha256"] = artifact_sha256(r)
    return r


RIGHTS = {
    "logo": ("permitted", "owned", ["reference", "embed", "redistribute"], "Example Home Services Co", None),
    "font": ("permitted", "licence", ["embed", "redistribute"], "Aileron (dotcolon.net) CC0 1.0",
             None),
    "guide_pdf": ("permitted", "owned", ["reference"], "Example Home Services Co", None),
    "scene": ("permitted", "provider_terms", ["reference", "edit", "embed", "redistribute"], None, None),
}


def record(name: str, authority: str, workspace: str, recorder: str, origin: str, parents=()) -> dict:
    status, basis, uses, holder, attribution = RIGHTS[name]
    path = ASSETS / FILES[name][0]
    dims = {"logo": (480, 192), "scene": (1600, 1200)}.get(name, (None, None))
    return {"ref": asset_ref(name, authority, workspace), "name": FILES[name][0], "size_bytes": path.stat().st_size,
            "width_px": dims[0], "height_px": dims[1], "duration_ms": None,
            "rights": rights(name, status, basis, uses, holder, attribution, recorder), "origin": origin,
            "source_url": None, "captured_at": None, "parent_assets": list(parents)}


def vref(kind: str, authority: str, workspace: str, name: str) -> dict:
    return {"authority": authority, "workspace_id": workspace, "kind": kind, "id": uid(f"{name}:id"),
            "version_id": uid(f"{name}:v1"), "revision": 1,
            "sha256": hashlib.sha256(f"fixture:{name}".encode()).hexdigest()}


def brand_package() -> dict:
    bci = {n: record(n, "brand-ci-studio", BCI_ACCOUNT, BCI_OWNER, "upload") for n in ("logo", "font", "guide_pdf")}
    guide = vref("brand_guide", "brand-ci-studio", BCI_ACCOUNT, "guide-document")
    receipt_id = uid("bci-approval")
    approval = {"receipt_id": receipt_id, "authority": "brand-ci-studio", "workspace_id": BCI_ACCOUNT,
                "subject": guide, "decision": "approved", "actor_id": BCI_OWNER, "actor_role": "owner",
                "approved_at": TS, "policy_revision": "bci-owner-approval-v1", "sha256": ""}
    approval["sha256"] = artifact_sha256(approval)
    pkg = {
        "schema_version": "brand-package-v1", "id": uid("brand-package"), "version_id": uid("brand-package:v1"),
        "revision": 1, "workspace_id": BCI_ACCOUNT, "created_at": TS, "created_by": BCI_OWNER, "sha256": "",
        "authority": "brand-ci-studio", "brand_id": uid("brand"), "brand_name": "Example Home Services Co",
        "source": {"bci_version_id": guide["version_id"], "bci_revision": 1, "document_digest": guide["sha256"],
                   "document_digest_algorithm": "sha256", "document_jcs_sha256": guide["sha256"],
                   "approval_receipt_id": receipt_id, "guide_document": guide,
                   "approved_pdf": bci["guide_pdf"]["ref"], "document_created_by": BCI_OWNER},
        "approval": approval,
        "assets": [bci["logo"], bci["font"], bci["guide_pdf"]],
        "logos": [{"id": "primary", "asset": bci["logo"]["ref"], "role": "primary",
                   "allowed_backgrounds": ["dark", "photo"], "min_width_px": 160, "clear_space_ratio": 0.25,
                   "allow_crop": False, "allow_distort": False}],
        "palette": [{"token": "navy", "hex": "#0B1F33", "role": "primary text and scrims"},
                    {"token": "amber", "hex": "#E88828", "role": "call to action"},
                    {"token": "white", "hex": "#FFFFFF", "role": "text on dark"}],
        "typography": [{"id": "body", "family": "Aileron", "role": "headline and CTA",
                        "font_assets": [bci["font"]["ref"]], "weights": [400], "fallback_family": None}],
        "imagery": {"principles": ["Real homes, natural light"], "reference_assets": [], "prohibited": []},
        "voice": {"principles": ["Plain and friendly"], "preferred_terms": [], "prohibited_terms": []},
        "usage_rules": [{"id": "logo-clear-space", "instruction": "Keep clear space of 0.25x logo height",
                         "severity": "required", "source_path": "guide/logo"}],
        "unresolved_decisions": [],
    }
    pkg["sha256"] = artifact_sha256(pkg)
    return pkg


def studio_records(pkg: dict) -> dict:
    by_sha = {r["ref"]["sha256"]: r["ref"] for r in pkg["assets"]}
    return {
        "logo": record("logo", "creative-studio", STUDIO_WS, ACTOR, "legacy_import", [by_sha[sha("logo")]]),
        "font": record("font", "creative-studio", STUDIO_WS, ACTOR, "legacy_import", [by_sha[sha("font")]]),
        "scene": record("scene", "creative-studio", STUDIO_WS, ACTOR, "provider"),
    }


def T(x, y, w, h, rot=0, opacity=1):
    return {"x": x, "y": y, "width": w, "height": h, "rotation_deg": rot, "opacity": opacity}


def text_style(size, align="left", valign="top", fill="#FFFFFF", line_height=1.1):
    return {"font_size_px": size, "weight": 400, "line_height": line_height, "letter_spacing_px": 0,
            "fill": fill, "align": align, "vertical_align": valign}


HEADLINE = "Fast, friendly home repairs"
CTA = "Book a visit today"

# (artboard id, W, H, crop of scene, scrim box, logo box, headline box, cta group origin)
LAYOUTS = [
    ("square", 1080, 1080, None, (0, 600, 1080, 480), (60, 60, 300, 120), (60, 640, 960, 200), (60, 880)),
    ("portrait", 1080, 1350, {"x": 0.1, "y": 0, "width": 0.8, "height": 1}, (0, 780, 1080, 570),
     (60, 60, 300, 120), (60, 830, 960, 220), (60, 1150)),
    ("story", 1080, 1920, {"x": 0.25, "y": 0, "width": 0.5, "height": 1}, (0, 1120, 1080, 800),
     (60, 300, 300, 120), (60, 1180, 960, 220), (60, 1440)),
    ("landscape", 1920, 1080, None, (0, 0, 1240, 1080), (80, 80, 300, 120), (80, 560, 1100, 220), (80, 860)),
]


def composition(records: dict) -> dict:
    boards = []
    for board_id, w, h, crop, scrim, logo, head, cta in LAYOUTS:
        layers = [
            {"id": "scene", "name": "Generated scene", "type": "image", "transform": T(0, 0, w, h), "order": 0,
             "locked": True, "content": {"asset": records["scene"]["ref"], "fit": "cover", "crop": crop}},
            {"id": "scrim", "name": "Scrim", "type": "shape", "transform": T(*scrim), "order": 1, "locked": False,
             "content": {"shape": "rectangle", "fill": "#0B1F33B3", "stroke": None, "stroke_width_px": 0,
                         "corner_radius_px": 0}},
            {"id": "logo", "name": "Logo", "type": "logo", "transform": T(*logo), "order": 2, "locked": False,
             "content": {"asset": records["logo"]["ref"], "brand_logo_id": "primary", "fit": "contain"}},
            {"id": "headline", "name": "Headline", "type": "text", "transform": T(*head), "order": 3,
             "locked": False, "content": {"text": HEADLINE, "copy_id": "headline", "font": records["font"]["ref"],
                                          "style": text_style(72)}},
            {"id": "cta-group", "name": "CTA", "type": "group", "transform": T(cta[0], cta[1], 460, 120),
             "order": 4, "locked": False, "content": {"child_ids": ["cta-bg", "cta-text"]}},
            {"id": "cta-bg", "name": "CTA button", "type": "shape", "transform": T(0, 0, 460, 120), "order": 0,
             "locked": False, "content": {"shape": "rectangle", "fill": "#E88828", "stroke": None,
                                          "stroke_width_px": 0, "corner_radius_px": 60}},
            {"id": "cta-text", "name": "CTA text", "type": "text", "transform": T(0, 0, 460, 120), "order": 1,
             "locked": False, "content": {"text": CTA, "copy_id": "cta", "font": records["font"]["ref"],
                                          "style": text_style(40, "center", "center", "#0B1F33")}},
        ]
        boards.append({"id": board_id, "width": w, "height": h, "color_space": "srgb", "background": "#0B1F33",
                       "layers": layers})
    doc = {"schema_version": "cs-composition-v1", "id": uid("composition"), "version_id": uid("composition:v1"),
           "revision": 1, "workspace_id": STUDIO_WS, "created_at": TS, "created_by": ACTOR, "sha256": "",
           "kind": "image", "basis_recipe": vref("recipe", "creative-studio", STUDIO_WS, "recipe-basis"),
           "basis_run": None, "artboards": boards, "video": None}
    doc["sha256"] = artifact_sha256(doc)
    return doc


def lineage(records: dict) -> dict:
    plan = vref("execution_plan", "creative-studio", STUDIO_WS, "plan")
    compile_hash = hashlib.sha256(b"fixture:compile").hexdigest()
    return {
        "request": vref("creative_request", "creative-studio", STUDIO_WS, "request"),
        "treatment": vref("treatment", "creative-studio", STUDIO_WS, "treatment"),
        "recipe": vref("recipe", "creative-studio", STUDIO_WS, "recipe-export"),
        "plan": plan, "compile_hash": compile_hash,
        "catalogue": vref("provider_catalogue", "creative-studio", STUDIO_WS, "catalogue"),
        "generated_sources": [{"run_id": uid("run"), "plan_id": plan["id"], "compile_hash": compile_hash,
                               "node_id": "hero", "attempt_id": uid("attempt-1"), "output_port": "image",
                               "asset": records["scene"]["ref"]}],
        # Provider charge not yet confirmed: kept as unknown, never zero.
        "cost": {"currency": "USD", "state": "unknown", "estimated_micros": 120000, "reserved_micros": 150000,
                 "settled_micros": 0, "unresolved_micros": 150000, "receipt_ids": [uid("provider-receipt-1")]},
    }


def mos_target() -> dict:
    return {"authority": "marketing-os", "workspace_id": MOS_WS,
            "concept": vref("concept", "marketing-os", MOS_WS, "concept"),
            "placement": vref("placement", "marketing-os", MOS_WS, "placement"),
            "copy_versions": [vref("copy", "marketing-os", MOS_WS, "copy-headline"),
                              vref("copy", "marketing-os", MOS_WS, "copy-cta")],
            "destination": vref("destination", "marketing-os", MOS_WS, "destination"),
            "claim_set": vref("claim_set", "marketing-os", MOS_WS, "claims"),
            "policy": vref("policy", "marketing-os", MOS_WS, "policy")}


SAFE_ZONES = {
    "square": None, "portrait": None, "landscape": None,
    # Cited channel data (MOS ENG-03d registry/channel-specs.yaml @9a9caa07, meta_ads image 9:16 safe_zone).
    "story": {"top_pct": 14, "right_pct": 0, "bottom_pct": 14, "left_pct": 0,
              "source": "marketing-os registry/channel-specs.yaml@9a9caa07 meta_ads image 9:16 safe_zone"},
}


def main() -> None:
    pkg = brand_package()
    records = studio_records(pkg)
    target = mos_target()
    out = {"brand_package.json": pkg, "studio_assets.json": records, "composition.json": composition(records),
           "lineage.json": lineage(records), "mos_target.json": target,
           "copy_sources.json": {"headline": target["copy_versions"][0], "cta": target["copy_versions"][1]},
           "safe_zones.json": SAFE_ZONES,
           "ids.json": {"studio_workspace": STUDIO_WS, "bci_account": BCI_ACCOUNT, "mos_workspace": MOS_WS,
                        "actor": ACTOR}}
    for name, doc in out.items():
        (HERE / name).write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
