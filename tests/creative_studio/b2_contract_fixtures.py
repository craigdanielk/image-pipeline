"""CS-B2 canonical contract fixtures for downstream pre-build (CS-B3/B4 consume these, not B2 code).

Deterministic documents produced by the real CS-B2 adapters from the invented MOS
fixtures (b2_mos_fixtures.py), with fixed Studio ids/timestamps so the JSON is
stable. Each is schema-valid and carries a correct RFC 8785 sha256.

Regenerate:  python3 -m tests.creative_studio.b2_contract_fixtures
The unit suite fails if the committed files differ from a fresh generation.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from service.brand.mos_brandkit import build_package, package_version_ref
from service.requests import contracts, documents, mos_intake
from service.requests.canonical import artifact_sha256
from tests.creative_studio import b2_mos_fixtures as fx

OUT_DIR = Path(__file__).with_name("b2_fixtures")
AT = "2026-10-31T09:00:00.000Z"
ACCEPTED_AT = "2026-10-31T09:05:00.000Z"


def _restamp(doc: dict, **fields) -> dict:
    doc = copy.deepcopy(doc)
    doc.update(fields)
    doc["sha256"] = artifact_sha256(doc)
    contracts.validate(doc)
    return doc


def _eligibility(camp, sources, fields) -> dict:
    receipt = {"id": fx.uid("eligibility", camp.key), "authority": "marketing-os", "workspace_id": camp.biz.mos_ws,
               "action": "generate", "decision": "allowed", "actor_id": fx.uid("user", "producer"),
               "source_versions": [sources["concept"], sources["content_pack"]["ref"]]
               + fields["origin"]["copy_versions"],
               "policy": fx._vref(camp.biz.mos_ws, "GenerationPolicy", "default"), "approval_receipt_ids": [],
               "checked_at": "2026-10-31T08:59:30Z", "valid_until": "2026-10-31T09:00:30Z", "reason_codes": [],
               "sha256": "0" * 64}
    return fx._sealed(receipt)


def _chain(key: str, biz, camp, **visual):
    ws = fx.uid("studio-ws", key)
    actor = fx.uid("user", "producer")
    package = build_package(biz.export()).document
    pkg_ctx = {"document": package, "ref": package_version_ref(package), "source_artifact_id": biz.artifact_id}
    sources = camp.sources()
    fields = mos_intake.build_request_fields(camp.visual_request(**visual), sources, pkg_ctx)
    fields["origin"]["generation_eligibility"] = _eligibility(camp, sources, fields)
    req = documents.new_request(fields, workspace_id=ws, actor_id=actor)
    req = _restamp(req, id=fx.uid("request", key), version_id=fx.uid("request", key, 1), created_at=AT)
    req_ref = documents.version_ref(req, "creative-request")
    draft_t = documents.new_treatment(mos_intake.build_treatment_fields(sources, req_ref, pkg_ctx["ref"]),
                                      workspace_id=ws, actor_id=actor)
    draft_t = _restamp(draft_t, id=fx.uid("treatment", key), version_id=fx.uid("treatment", key, 1), created_at=AT)
    accepted = _restamp(documents.accepted_treatment(draft_t, actor_id=actor), version_id=fx.uid("treatment", key, 2),
                        created_at=ACCEPTED_AT, accepted_at=ACCEPTED_AT)
    return package, req, draft_t, accepted, ws, actor


def generate() -> dict[str, dict]:
    out: dict[str, dict] = {}
    home, home_camp = fx.home_services()
    package, req, draft_t, accepted, ws, actor = _chain("home-services", home, home_camp)
    out["brand-package-v1.home-services.complete.json"] = package
    out["cs-creative-request-v1.home-services.service.json"] = req
    out["cs-treatment-v1.home-services.draft.json"] = draft_t
    out["cs-treatment-v1.home-services.accepted.json"] = accepted
    draft = documents.new_authoring_draft(workspace_id=ws, actor_id=actor, name="Annual maintenance plan 1:1")
    draft = _restamp(draft, id=fx.uid("draft", "home-services"), created_at=AT, updated_at=AT, step="direction",
                     selections={"brand_package": package_version_ref(package),
                                 "offering": home_camp.offering["source"], "audiences": req["audiences"],
                                 "concept": req["origin"]["concept"],
                                 "request": documents.version_ref(req, "creative-request"),
                                 "treatment": documents.version_ref(accepted, "treatment")})
    out["cs-authoring-draft-v1.home-services.json"] = draft

    physio, physio_camp = fx.physio_clinic()
    package, req, _, accepted, _, _ = _chain("physio", physio, physio_camp, canvas="4:5")
    out["brand-package-v1.physio.regulated-healthcare.json"] = package
    out["cs-creative-request-v1.physio.service_group.json"] = req
    out["cs-treatment-v1.physio.accepted.json"] = accepted

    kitchen, kitchen_camp = fx.kitchenware()
    package, req, _, accepted, _, _ = _chain("kitchenware", kitchen, kitchen_camp, canvas="9:16")
    out["brand-package-v1.kitchenware.physical.json"] = package
    out["cs-creative-request-v1.kitchenware.physical_product.json"] = req
    out["cs-treatment-v1.kitchenware.accepted.json"] = accepted

    # explicit gaps: a font role without files and owner-listed gaps stay visible, never synthesised
    gaps, _ = fx.home_services("home-gaps")
    gaps.content["fonts"]["value"].append({"family": "Example Display", "role": "display", "weights": ["800"],
                                           "licence": "commercial", "licence_url": None, "status": "owner_supplied",
                                           "original_family": None, "substitution_basis": None})
    gaps.content["gaps"] = ["Lifestyle photography library not supplied yet."]
    out["brand-package-v1.home-gaps.with-unresolved.json"] = build_package(gaps.export()).document
    return out


def write() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    for name, doc in generate().items():
        (OUT_DIR / name).write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    write()
    print(f"wrote {len(generate())} fixtures to {OUT_DIR}")
