"""Approved MOS BrandKit (ENG-03b) -> immutable ``brand-package-v1`` document.

Input is the Marketing OS brand export for one exact BrandKit artifact version:
the envelope (status + content), its approval receipt, MOS's current head for
that envelope type, the Brand CI guide revision it wraps, the file records with
their rights receipts, and the ``mos-higgsfield-brand-kit-v1`` payload MOS builds
for the Higgsfield adapter. Output is a contract-valid package whose identity is
derived from the source, so the same approved version always yields the same
document and digest.

Nothing is synthesised: a missing required piece (logo usage numbers, tone,
usable font files, rights receipt) is a typed refusal or a recorded unresolved
decision, never a default. Approval is checked here *and* again in SQL.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from service.requests import contracts
from service.requests.canonical import artifact_sha256, jcs_sha256

from .errors import InputRefusal

# Stable namespace for source-derived identities (uuid5); not a secret.
NAMESPACE = uuid.UUID("6f1d3c52-1b0e-5a7c-9d43-2b9e8f0c4a11")
EXPORT_SCHEMA = "mos-brand-export-v1"
HIGGSFIELD_SCHEMA = "mos-higgsfield-brand-kit-v1"
_LOGO_ROLES = {"primary": "primary", "secondary": "secondary", "mark": "mark", "icon": "mark",
               "mono": "monochrome", "monochrome": "monochrome", "reversed": "reversed"}
_ORIGINS = {"website": "capture", "capture": "capture", "upload": "upload", "owner": "upload",
            "provider": "provider", "derived": "derived", "legacy_import": "legacy_import"}


@dataclass(frozen=True)
class PackageBuild:
    document: dict              # brand-package-v1, sha256 set
    source_ref: dict            # VersionRef of the MOS BrandKit artifact version
    source_status: str          # MOS status of that version (must be 'approved')
    approval_receipt_ref: dict  # receipt + higgsfield payload digest, stored with the import
    files: list[dict]           # export file records to mirror (ref, name, mime, size...)


def slug(value: str, prefix: str = "x") -> str:
    s = re.sub(r"[^a-z0-9_-]+", "-", value.lower()).strip("-")
    if not s or not s[0].isalpha():
        s = f"{prefix}-{s}".strip("-")
    return s[:64].rstrip("-") or prefix


def _uid(*parts: str) -> str:
    return str(uuid.uuid5(NAMESPACE, ":".join(parts)))


def _field(content: dict, name: str):
    item = content.get(name)
    if isinstance(item, dict) and "value" in item:
        return item.get("value"), item.get("status")
    return item, None


def source_ref_for(export: dict) -> dict:
    art = export["artifact"]
    return {"authority": "marketing-os", "workspace_id": export["workspace_id"], "kind": "BrandKit",
            "id": art["id"], "version_id": art["id"], "revision": art["version"],
            "sha256": jcs_sha256(art["content"])}


def _check_receipt(receipt: dict, path: str) -> None:
    try:
        contracts.validate_def(receipt, "approval")
    except contracts.ContractViolation as exc:
        raise InputRefusal("approval_receipt_invalid", str(exc), [path + p for p in exc.field_paths]) from None
    if jcs_sha256({k: v for k, v in receipt.items() if k != "sha256"}) != receipt["sha256"]:
        raise InputRefusal("approval_receipt_invalid", "receipt sha256 does not match its content", [path + "/sha256"])


def build_package(export: dict) -> PackageBuild:
    if export.get("schema_version") != EXPORT_SCHEMA:
        raise InputRefusal("unsupported_schema_version", f"expected {EXPORT_SCHEMA}", ["/schema_version"])
    art = export["artifact"]
    mos_ws = export["workspace_id"]
    if art.get("type") != "BrandKit":
        raise InputRefusal("invalid_contract", "export is not a BrandKit", ["/artifact/type"])
    source_ref = source_ref_for(export)
    status = art.get("status")
    if status != "approved":
        raise InputRefusal("brand_not_approved", f"BrandKit v{art['version']} is {status}", ["/artifact/status"])
    current = export.get("current") or {}
    if current.get("version_id") != art["id"] or current.get("status") != "approved":
        raise InputRefusal("approval_stale", f"BrandKit v{art['version']} is not the current approved version "
                           f"(current v{current.get('revision')} {current.get('status')})", ["/current"])
    approval = export.get("approval")
    if not approval:
        raise InputRefusal("brand_not_approved", "no approval receipt", ["/approval"])
    _check_receipt(approval, "/approval")
    if approval["subject"].get("version_id") != art["id"] or approval["subject"].get("sha256") != source_ref["sha256"]:
        raise InputRefusal("approval_receipt_invalid", "receipt does not approve this exact BrandKit version",
                           ["/approval/subject"])

    content = art["content"]
    files = export.get("files") or []
    by_version = {f["ref"]["asset_version_id"]: f for f in files}
    for f in files:
        if f["ref"]["workspace_id"] != mos_ws or f["ref"]["authority"] != "marketing-os":
            raise InputRefusal("scope_mismatch", "file outside the exporting workspace", ["/files"])
        if not f.get("rights"):
            raise InputRefusal("brandkit_incomplete", f"file {f['name']} has no rights receipt", ["/files"])

    missing: list[str] = []
    brand_name, _ = _field(content, "brand_name")
    hf = export.get("higgsfield_payload")
    if not hf or hf.get("schema_version") != HIGGSFIELD_SCHEMA:
        raise InputRefusal("higgsfield_payload_mismatch", "missing mos-higgsfield-brand-kit-v1 payload",
                           ["/higgsfield_payload"])
    src = hf.get("source") or {}
    if (src.get("brand_kit_artifact_id"), src.get("brand_kit_version"), src.get("workspace_id"), src.get("approved")) \
            != (art["id"], art["version"], mos_ws, True):
        raise InputRefusal("higgsfield_payload_mismatch", "payload was built from a different BrandKit version",
                           ["/higgsfield_payload/source"])
    if not isinstance(brand_name, str) or not brand_name.strip():
        missing.append("brand_name")

    # -- logos ----------------------------------------------------------------
    logo_values, _ = _field(content, "logo_assets")
    usage, _ = _field(content, "logo_usage")
    logos = []
    if not logo_values:
        missing.append("logo_assets")
    if not isinstance(usage, dict) or not all(k in usage for k in ("min_width_px", "clear_space_ratio",
                                                                     "allowed_backgrounds")):
        missing.append("logo_usage")
    else:
        for item in logo_values or []:
            role = _LOGO_ROLES.get(item.get("role", ""))
            f = next((f for f in files if f.get("kind") == "logo" and f["ref"]["asset_id"] == item["asset_id"]), None)
            if role is None:
                raise InputRefusal("brandkit_incomplete", f"logo role {item.get('role')!r} is not a contract role",
                                   ["/artifact/content/logo_assets/value"])
            if f is None or f["ref"]["sha256"] != item["sha256"]:
                raise InputRefusal("asset_hash_mismatch", f"logo {item.get('role')} file missing or digest differs",
                                   ["/artifact/content/logo_assets/value"])
            logos.append({"id": slug(f"logo-{item['role']}"), "asset": f["ref"], "role": role,
                          "allowed_backgrounds": list(usage["allowed_backgrounds"]),
                          "min_width_px": usage["min_width_px"], "clear_space_ratio": usage["clear_space_ratio"],
                          "allow_crop": False, "allow_distort": False})
    primary = next((l for l in logos if l["role"] == "primary"), None)
    if hf.get("logo_sha256") is not None and logos and (primary is None or primary["asset"]["sha256"] != hf["logo_sha256"]):
        raise InputRefusal("higgsfield_payload_mismatch", "payload logo is not the package primary logo",
                           ["/higgsfield_payload/logo_sha256"])

    # -- palette --------------------------------------------------------------
    palette_values, _ = _field(content, "palette")
    palette = []
    for item in palette_values or []:
        if re.fullmatch(r"#[0-9A-Fa-f]{6}", item.get("hex", "")):
            palette.append({"token": slug(item.get("token") or item.get("role") or "color", "color"),
                            "hex": item["hex"].upper(), "role": item.get("role") or "unspecified"})
    if not palette:
        missing.append("palette")
    hexes = {p["hex"] for p in palette}
    if any(c["hex"].upper() not in hexes for c in hf.get("colors") or []):
        raise InputRefusal("higgsfield_payload_mismatch", "payload colour not in the approved palette",
                           ["/higgsfield_payload/colors"])

    # -- typography: only families with actual font files ---------------------
    font_values, _ = _field(content, "fonts")
    typography, decisions, rules = [], [], []
    for item in font_values or []:
        family = item["family"]
        font_files = [f["ref"] for f in files if f.get("kind") == "font" and f.get("font_family") == family]
        fid = slug(f"{family}-{item.get('role', 'text')}", "font")
        if not font_files:
            decisions.append({"id": slug(f"font-missing-{fid}"),
                              "description": f"font_asset_missing: no usable {family} file for role "
                                             f"{item.get('role')}; exact text in this role cannot be rendered",
                              "blocks_compile": False, "affected_asset_ids": []})
            continue
        weights = sorted({int(w) for w in item.get("weights") or [] if str(w).isdigit() and 100 <= int(w) <= 900})
        if not weights:
            decisions.append({"id": slug(f"font-weights-{fid}"),
                              "description": f"font weights for {family} ({item.get('role')}) are not stated",
                              "blocks_compile": False, "affected_asset_ids": []})
            continue
        typography.append({"id": fid, "family": family, "role": item.get("role") or "text",
                           "font_assets": font_files, "weights": weights, "fallback_family": None})
        if item.get("status") == "substituted" and item.get("original_family"):
            rules.append({"id": slug(f"font-substitution-{fid}"), "severity": "required",
                          "instruction": f"{family} is an accepted substitute for {item['original_family']} "
                                         f"({item.get('substitution_basis')}); do not render the original family.",
                          "source_path": "/content/fonts/value"})
    if not typography:
        raise InputRefusal("font_asset_missing", "no typography role has a usable font file",
                           ["/artifact/content/fonts/value"])

    # -- imagery / voice / rules ----------------------------------------------
    imagery_value, _ = _field(content, "imagery")
    if isinstance(imagery_value, dict):
        principles = [str(p) for p in imagery_value.get("principles") or []]
        prohibited = [str(p) for p in imagery_value.get("prohibited") or []]
    elif isinstance(imagery_value, list):
        principles, prohibited = [str(p) for p in imagery_value], []
    else:
        principles, prohibited = [], []
    reference_assets = [f["ref"] for f in files if f.get("kind") == "reference"]
    tone, _ = _field(content, "tone_attributes")
    if not tone:
        missing.append("tone_attributes")
    vocabulary, _ = _field(content, "vocabulary")
    vocabulary = vocabulary if isinstance(vocabulary, dict) else {}
    for i, d in enumerate(_field(content, "required_disclosures")[0] or []):
        rules.append({"id": slug(d.get("disclosure_id") or f"disclosure-{i + 1}", "disclosure"), "severity": "required",
                      "instruction": f"Include this disclosure verbatim where it applies ({d.get('applies_to')}, "
                                     f"{d.get('market')}): {d['text']}",
                      "source_path": f"/content/required_disclosures/value/{i}"})
    for i, c in enumerate(_field(content, "prohibited_claims")[0] or []):
        rules.append({"id": slug(c.get("rule_id") or f"claim-{i + 1}", "claim"), "severity": "required",
                      "instruction": f"Never claim: {c.get('description')}",
                      "source_path": f"/content/prohibited_claims/value/{i}"})
    for i, gap in enumerate(content.get("gaps") or []):
        decisions.append({"id": f"gap-{i + 1}", "description": str(gap)[:2000], "blocks_compile": False,
                          "affected_asset_ids": []})
    for name in hf.get("omitted") or []:
        decisions.append({"id": slug(f"unverified-{name}"), "description": f"{name} was not owner-verified in the "
                          "approved BrandKit and is excluded from provider payloads", "blocks_compile": False,
                          "affected_asset_ids": []})
    if missing:
        raise InputRefusal("brandkit_incomplete", "missing: " + ", ".join(missing),
                           [f"/artifact/content/{m}" for m in missing])

    # -- guide + approved PDF ---------------------------------------------------
    guide = export.get("guide") or {}
    pdf = by_version.get(guide.get("pdf_asset_version_id") or "")
    if not pdf or pdf.get("kind") != "pdf":
        raise InputRefusal("brandkit_incomplete", "approved guide PDF file is not exported", ["/guide"])
    for key in ("revision_id", "revision_version", "digest", "digest_algorithm", "document_jcs_sha256", "created_by"):
        if guide.get(key) in (None, ""):
            raise InputRefusal("brandkit_incomplete", f"guide {key} missing", [f"/guide/{key}"])
    content_guide = content.get("guide") or {}
    if content_guide.get("revision_id") != guide["revision_id"] or \
            (content_guide.get("pdf") or {}).get("pdf_sha256") != pdf["ref"]["sha256"]:
        raise InputRefusal("asset_hash_mismatch", "guide revision/PDF differs from the one the BrandKit pins",
                           ["/guide"])

    used = {f["ref"]["asset_version_id"] for f in files if f.get("kind") in ("logo", "pdf", "reference")} | {
        r["asset_version_id"] for t in typography for r in t["font_assets"]}
    assets = []
    for f in files:
        if f["ref"]["asset_version_id"] not in used:
            continue
        assets.append({"ref": f["ref"], "name": f["name"], "size_bytes": f["size_bytes"],
                       "width_px": f.get("width_px"), "height_px": f.get("height_px"),
                       "duration_ms": f.get("duration_ms"), "rights": f["rights"],
                       "origin": _ORIGINS.get(f.get("origin") or "", "upload"),
                       "source_url": f.get("source_url"), "captured_at": f.get("captured_at"),
                       "parent_assets": []})

    document = {
        "schema_version": "brand-package-v1",
        "id": _uid("brand-package", mos_ws, "BrandKit"),
        "version_id": _uid("brand-package-version", art["id"]),
        "revision": art["version"],
        "workspace_id": mos_ws,
        "created_at": approval["approved_at"],
        "created_by": approval["actor_id"],
        "authority": "brand-ci-studio",
        "brand_id": _uid("brand", mos_ws),
        "brand_name": brand_name.strip(),
        "source": {
            "bci_version_id": guide["revision_id"],
            "bci_revision": guide["revision_version"],
            "document_digest": guide["digest"],
            "document_digest_algorithm": guide["digest_algorithm"],
            "document_jcs_sha256": guide["document_jcs_sha256"],
            "approval_receipt_id": approval["receipt_id"],
            "guide_document": {"authority": "marketing-os", "workspace_id": mos_ws, "kind": "brand-guide-revision",
                               "id": guide["revision_id"], "version_id": guide["revision_id"],
                               "revision": guide["revision_version"], "sha256": guide["document_jcs_sha256"]},
            "approved_pdf": pdf["ref"],
            "document_created_by": guide["created_by"],
        },
        "approval": approval,
        "assets": assets,
        "logos": logos,
        "palette": palette,
        "typography": typography,
        "imagery": {"principles": principles[:40], "reference_assets": reference_assets, "prohibited": prohibited[:40]},
        "voice": {"principles": [str(t) for t in tone][:40],
                  "preferred_terms": [str(t) for t in vocabulary.get("do") or []][:100],
                  "prohibited_terms": [str(t) for t in vocabulary.get("dont") or []][:100]},
        "usage_rules": rules[:100],
        "unresolved_decisions": decisions[:100],
    }
    document["sha256"] = artifact_sha256(document)
    try:
        contracts.validate(document, "brand-package-v1")
    except contracts.ContractViolation as exc:
        raise InputRefusal("invalid_contract", str(exc), exc.field_paths) from None
    receipt_ref = {"receipt_id": approval["receipt_id"], "authority": approval["authority"],
                   "policy_revision": approval["policy_revision"], "sha256": approval["sha256"],
                   "higgsfield_payload_sha256": jcs_sha256(hf)}
    return PackageBuild(document=document, source_ref=source_ref, source_status=status,
                        approval_receipt_ref=receipt_ref,
                        files=[f for f in files if f["ref"]["asset_version_id"] in used])


def package_version_ref(document: dict) -> dict:
    return {"authority": document["authority"], "workspace_id": document["workspace_id"], "kind": "brand-package",
            "id": document["id"], "version_id": document["version_id"], "revision": document["revision"],
            "sha256": document["sha256"]}
