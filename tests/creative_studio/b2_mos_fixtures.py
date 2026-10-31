"""CS-B2 provisional Marketing OS fixtures + recorded-shape gateway mock (no network).

Shapes are taken from the MOS producers' canonical fixtures/contracts, not invented:
  * BrandKit content  - ENG-03b tests/fixtures/envelopes/BrandKit.v1*.json (universal field model)
  * Higgsfield payload - ENG-03b brandKitHiggsfieldExport.ts (mos-higgsfield-brand-kit-v1)
  * ContentPack        - ENG-04b tests/fixtures/envelopes/ContentPack.v1.json (cases ecom_kitchen_p1,
                         services_physio_selected_with_visuals)
  * visual request     - ENG-04c mos_request_media_jobs studio_request (mos-visual-request-v1)
The two *gateway* response envelopes (mos-brand-export-v1, mos-campaign-sources-v1) are
CS-B2 provisional: MOS's /api/creative-studio/sources and brand export routes are not built
yet. Every business is invented; ids are deterministic uuid5 values.
"""
from __future__ import annotations

import copy
import hashlib
import uuid
from datetime import datetime, timedelta, timezone

from service.brand.gateway import GatewayUnavailable
from service.requests.canonical import jcs_sha256

NS = uuid.UUID("0b2f8a4e-3c71-5d09-a6e2-7f4c1d9e8b30")
APPROVED_AT = "2026-10-30T16:00:00.000Z"
CAPTURED_AT = "2026-10-30T12:00:00.000Z"


def uid(*parts) -> str:
    return str(uuid.uuid5(NS, ":".join(str(p) for p in parts)))


def _sealed(receipt: dict) -> dict:
    receipt = dict(receipt)
    receipt["sha256"] = jcs_sha256({k: v for k, v in receipt.items() if k != "sha256"})
    return receipt


def rights(key, *, status="permitted", basis="owned", uses=("reference", "generate", "edit", "embed", "redistribute"),
           channels=("*",), territories=("*",), expires_at=None, holder="Brand owner") -> dict:
    return _sealed({"receipt_id": uid("rights", key), "revision": 1, "sha256": "0" * 64, "status": status,
                    "holder": holder, "basis": basis, "evidence": [], "allowed_uses": list(uses),
                    "channels": list(channels), "territories": list(territories), "valid_from": CAPTURED_AT,
                    "expires_at": expires_at, "attribution": None, "restrictions": [],
                    "recorded_by": uid("user", "brand-owner")})


def _field(value, status="confirmed", origin="owner"):
    return {"value": value, "status": status, "origin": origin, "sources": [], "confidence": 1,
            "confirmed_by": uid("user", "brand-owner"), "confirmed_at": APPROVED_AT, "label": "verified"}


class Business:
    """One invented business: MOS BrandKit export + campaign sources + file bytes."""

    def __init__(self, key, *, label, domain, model, regulated, offering_kind, palette, tone, fonts, logo_roles,
                 disclosures=(), prohibited=(), imagery=None, logo_mime="image/png"):
        self.key = key
        self.label = label
        self.mos_ws = uid("mos-ws", key)
        self.model = model
        self.regulated = regulated
        self.offering_kind = offering_kind
        self.blobs: dict[str, bytes] = {}
        self.files = []
        self.brandkit_version = 1
        self.artifact_id = uid("brandkit", key, 1)
        guide_rev = uid("guide", key)
        logos_value = []
        for role in logo_roles:
            f = self._file(f"logo-{role}", "logo", logo_mime, f"{domain}/brand/logo-{role}", width=512, height=512)
            logos_value.append({"role": role, "asset_id": f["ref"]["asset_id"], "sha256": f["ref"]["sha256"],
                                "storage_path": f"{self.mos_ws}/logo_{role}/{f['ref']['sha256']}.png",
                                "mime_type": logo_mime, "origin": "upload", "source_url": None,
                                "rights_status": "permitted"})
        for fam, weights in fonts:
            f = self._file(f"font-{fam}", "font", "font/ttf", None, family=fam,
                           rights_kw={"basis": "licence", "holder": "Open font licence (OFL-1.1)"})
        pdf = self._file("guide-pdf", "pdf", "application/pdf", None)
        content = {
            "schema_version": "mos-brandkit-v1", "brand_label": label, "regulated_category": regulated,
            "markets": ["US", "GB"],
            "guide": {"revision_id": guide_rev, "revision_version": 2, "revision_digest": hashlib.sha256(
                f"guide-{key}".encode()).hexdigest(), "artifact_id": uid("guide-doc", key),
                "html_digest": hashlib.sha256(f"html-{key}".encode()).hexdigest(),
                "pdf": {"receipt_id": uid("pdf-receipt", key), "pdf_sha256": pdf["ref"]["sha256"], "page_count": 11,
                        "expected_page_count": 11}, "open_decisions": 0},
            "brand_name": _field(label),
            "logo_assets": _field(logos_value),
            "palette": _field([{"hex": h, "role": r, "token": f"--color-{r}", "role_basis": "token_name"}
                               for h, r in palette]),
            "fonts": _field([{"family": fam, "role": role, "weights": w, "licence": "OFL-1.1", "licence_url": None,
                              "status": "owner_supplied", "original_family": None, "substitution_basis": None}
                             for (fam, w), role in zip(fonts * 2, ("heading", "body"))]),
            "logo_usage": _field({"min_width_px": 96, "clear_space_ratio": 0.5,
                                  "allowed_backgrounds": ["light", "brand-primary"]}),
            "tone_attributes": _field(list(tone)),
            "vocabulary": _field({"do": ["plain words"], "dont": ["cheapest", "guaranteed"]}),
            "imagery": _field(imagery or {"principles": ["Real people in real settings, natural light"],
                                          "prohibited": ["stock-photo handshakes"]}),
            "required_disclosures": _field(list(disclosures)),
            "prohibited_claims": _field(list(prohibited)),
            "gaps": [],
        }
        self.content = content
        self.guide = {"revision_id": guide_rev, "revision_version": 2,
                      "digest": content["guide"]["revision_digest"], "digest_algorithm": "sha256-js-json-stringify-v1",
                      "document_jcs_sha256": hashlib.sha256(f"jcs-{key}".encode()).hexdigest(),
                      "created_by": uid("user", "brand-owner"), "pdf_asset_version_id": pdf["ref"]["asset_version_id"]}
        self.status = "approved"
        self.current = None    # None -> this version is current

    def _file(self, name, kind, mime, url, *, width=None, height=None, family=None, rights_kw=None):
        head = {"image/png": b"\x89PNG\r\n\x1a\n", "font/ttf": b"\x00\x01\x00\x00",
                "application/pdf": b"%PDF-1.7\n", "image/svg+xml": b"<svg xmlns='http://www.w3.org/2000/svg'>"}[mime]
        data = head + f"{self.key}:{name}:generic-test-bytes".encode() * 4
        sha = hashlib.sha256(data).hexdigest()
        aid = uid("asset", self.key, name)
        f = {"ref": {"authority": "marketing-os", "workspace_id": self.mos_ws, "asset_id": aid,
                     "asset_version_id": aid, "revision": 1, "sha256": sha, "mime_type": mime},
             "name": f"{name}.{mime.split('/')[-1].replace('svg+xml', 'svg')}", "size_bytes": len(data),
             "width_px": width, "height_px": height, "duration_ms": None, "origin": "upload",
             "source_url": url and f"https://{url}", "captured_at": CAPTURED_AT,
             "rights": rights(f"{self.key}:{name}", **(rights_kw or {})), "kind": kind, "font_family": family}
        self.blobs[aid] = data
        self.files.append(f)
        return f

    # -- MOS BrandKit export ---------------------------------------------------
    def brandkit_ref(self) -> dict:
        return {"authority": "marketing-os", "workspace_id": self.mos_ws, "kind": "BrandKit", "id": self.artifact_id,
                "version_id": self.artifact_id, "revision": self.brandkit_version, "sha256": jcs_sha256(self.content)}

    def approval(self) -> dict:
        return _sealed({"receipt_id": uid("approval", self.key, self.brandkit_version), "authority": "marketing-os",
                        "workspace_id": self.mos_ws, "subject": self.brandkit_ref(), "decision": "approved",
                        "actor_id": uid("user", "brand-owner"), "actor_role": "owner", "approved_at": APPROVED_AT,
                        "policy_revision": "mos-envelope-approval-v1", "sha256": "0" * 64})

    def higgsfield_payload(self) -> dict:
        c = self.content
        primary = next(l for l in c["logo_assets"]["value"] if l["role"] == "primary")
        return {"schema_version": "mos-higgsfield-brand-kit-v1",
                "source": {"workspace_id": self.mos_ws, "brand_kit_artifact_id": self.artifact_id,
                           "brand_kit_version": self.brandkit_version, "approved": True},
                "name": self.label, "tagline": None,
                "colors": [{"hex": p["hex"].upper(), "role": p["role"]} for p in c["palette"]["value"]],
                "fonts": [{"family": f["family"], "role": f["role"], "weights": f["weights"], "licence": f["licence"],
                           "substituted_for": None} for f in c["fonts"]["value"]],
                "logo_url": "https://storage.example-mos.test/signed/logo", "logo_sha256": primary["sha256"],
                "tone": c["tone_attributes"]["value"], "values": [], "omitted": ["tagline", "brand_values"]}

    def export(self) -> dict:
        current = self.current or {"version_id": self.artifact_id, "revision": self.brandkit_version,
                                   "status": self.status}
        return {"schema_version": "mos-brand-export-v1", "workspace_id": self.mos_ws,
                "artifact": {"id": self.artifact_id, "type": "BrandKit", "version": self.brandkit_version,
                             "status": self.status, "content": copy.deepcopy(self.content),
                             "created_by": uid("user", "brand-owner"), "created_at": CAPTURED_AT},
                "approval": self.approval() if self.status == "approved" else None,
                "current": current, "guide": dict(self.guide), "higgsfield_payload": self.higgsfield_payload(),
                "files": copy.deepcopy(self.files)}

    def publish_new_version(self):
        """MOS approves BrandKit v+1 (e.g. new palette). Earlier imports become stale."""
        self.brandkit_version += 1
        self.artifact_id = uid("brandkit", self.key, self.brandkit_version)
        self.content = copy.deepcopy(self.content)
        self.content["palette"]["value"].append({"hex": "#222222", "role": "text", "token": "--color-text",
                                                 "role_basis": "token_name"})


def _vref(mos_ws, kind, key, revision=1, content=None):
    return {"authority": "marketing-os", "workspace_id": mos_ws, "kind": kind, "id": uid(kind, key),
            "version_id": uid(kind, key, "v", revision), "revision": revision,
            "sha256": jcs_sha256(content if content is not None else {"kind": kind, "key": key, "rev": revision})}


class Campaign:
    """One ContentPack placement (ENG-04b shape) + MOS's exported sources for it."""

    def __init__(self, biz: Business, key, *, hook, angle, channels, offering, objective, direction,
                 copy_variants, selected_index=None, product_spec=None):
        self.biz = biz
        ws = biz.mos_ws
        self.key = key
        self.placement_id = uid("placement", key)
        self.concept = _vref(ws, "Concept", key)
        copy_versions = {}
        variants = []
        for i, (fields, state) in enumerate(copy_variants, start=1):
            cv = _vref(ws, "CopyVersion", f"{key}-{i}", content=fields)
            copy_versions[cv["version_id"]] = cv
            variants.append({"checks": [], "fields": fields, "rewrite": {"attempted": False, "prior_findings": []},
                             "lineage_id": cv["version_id"], "check_state": state, "copy_version": 1,
                             "variant_index": i, "channel_outputs": [], "copy_version_id": cv["version_id"]})
        self.copy_versions = copy_versions
        pack = {"envelope": "ContentPack", "schema_version": 1, "pack_key": self.placement_id,
                "summary": f"{len(variants)} copy variants",
                "pins": _field({"model": "mock-copy-model-v1", "brand_kit": {"id": biz.artifact_id, "version": 1},
                                "placement_id": self.placement_id, "campaign_plan": {"id": uid("plan", key), "version": 1}},
                               "proposed", "inferred"),
                "placement": _field({"cta": copy_variants[0][0].get("cta"), "hook": hook, "angle": angle,
                                     "formats": ["single_image"], "channels": list(channels),
                                     "avatar_id": f"av-{key}", "concept_id": self.concept["id"],
                                     "funnel_stage": "consideration", "placement_id": self.placement_id,
                                     "offer_group_key": key}, "proposed", "inferred"),
                "copy_variants": _field(variants, "proposed", "inferred"),
                "visuals": _field([], "missing", "inferred")}
        if selected_index is not None:
            sel = variants[selected_index - 1]
            pack["selected_copy"] = _field({"copy_version_id": sel["copy_version_id"], "copy_version": 1}, "edited")
        self.pack = pack
        self.pack_ref = _vref(ws, "ContentPack", key, content=pack)
        self.offering = offering
        self.objective = objective
        self.direction = direction
        self.product_spec = product_spec
        self.claims = {"src-" + key: _vref(ws, "Claim", key)}
        self.draft_asset = None

    def visual_request(self, *, canvas="1:1", slot=1, tier="draft", media_kind="image", reference=None):
        return {"schema_version": "mos-visual-request-v1", "station": "produce", "step": "visuals",
                "workspace_id": self.biz.mos_ws, "placement_id": self.placement_id, "concept_id": self.concept["id"],
                "tier": tier, "media_kind": media_kind, "canvas": canvas, "slot": slot, "reference": reference,
                "copy_refs": [{"workspace_id": self.biz.mos_ws, "version_id": v["version_id"], "revision": 1}
                              for v in self.copy_versions.values()],
                "content_pack_ref": {"id": self.pack_ref["id"], "version": 1}, "production_script": None,
                "composition": {"exact_layers": "deferred_to_m3", "text_model_drawn": True}}

    def sources(self) -> dict:
        ws = self.biz.mos_ws
        budget_policy = _vref(ws, "BudgetPolicy", self.key)
        out = {"schema_version": "mos-campaign-sources-v1", "workspace_id": ws,
               "content_pack": {"ref": self.pack_ref, "content": copy.deepcopy(self.pack)},
               "campaign_brief": _vref(ws, "CampaignPlan", self.key), "concept": self.concept,
               "concept_approval": _sealed({"receipt_id": uid("concept-approval", self.key), "authority": "marketing-os",
                                            "workspace_id": ws, "subject": self.concept, "decision": "approved",
                                            "actor_id": uid("user", "reviewer"), "actor_role": "reviewer",
                                            "approved_at": APPROVED_AT, "policy_revision": "mos-concept-approval-v1",
                                            "sha256": "0" * 64}),
               "concept_direction": self.direction,
               "destination": _vref(ws, "Destination", self.key), "claim_set": _vref(ws, "ClaimSet", self.key),
               "claims": self.claims, "policy": _vref(ws, "GenerationPolicy", self.key),
               "offering": self.offering, "audiences": [_vref(ws, "Avatar", self.key)],
               "objective": self.objective, "locale": "en-GB", "copy_versions": self.copy_versions,
               # from the MOS reserved quote (ENG-04c studio_quote): never a Studio literal
               "budget": {"currency": "USD", "max_cost_micros": 62500, "funding_source": "workspace",
                          "credential_binding_id": uid("credential", self.key), "policy": budget_policy,
                          "expires_at": "2026-12-31T00:00:00.000Z"},
               "provider_selection": None, "production_script": None, "references": {}}
        if self.draft_asset:
            out["references"] = {self.draft_asset["asset_version_id"]: self.draft_asset}
        return out


def home_services(key: str = "home-services") -> tuple[Business, Campaign]:
    biz = Business(key, label="Example Home Services Co", domain="example-home-services.test",
                   model="local_services", regulated="none", offering_kind="service",
                   palette=[("#0B5FFF", "primary"), ("#FFB000", "accent"), ("#F7F7F5", "background")],
                   tone=["calm", "direct", "practical"], fonts=[("Example Sans", ["400", "700"])],
                   logo_roles=["primary", "mono"],
                   prohibited=[{"rule_id": "cl-guaranteed-cheapest", "category": "general",
                                "pattern": "cheapest in town", "description": "Owner-prohibited claim: cheapest in town",
                                "citation_url": None}])
    offering = {"kind": "service", "source": _vref(biz.mos_ws, "Offer", "maintenance-plan"),
                "name": "Annual maintenance plan", "summary": "Two scheduled inspections a year with a fixed quote "
                "before any repair work starts.", "value_propositions": ["Fixed quote before work starts",
                                                                          "Arrival windows you can plan around"],
                "approved_claims": [_vref(biz.mos_ws, "Claim", "same-day")], "physical_product_spec": None,
                "service_facts": [{"id": "visits", "label": "Visits", "value": "Two inspections a year",
                                   "source": _vref(biz.mos_ws, "Offer", "maintenance-plan")},
                                  {"id": "callout", "label": "Callout", "value": "Same-day callout in the service area",
                                   "source": _vref(biz.mos_ws, "Claim", "same-day")}]}
    camp = Campaign(biz, f"{key}-plan", hook="Never wait all day for a technician", angle="Arrival windows you can plan",
                    channels=["instagram", "facebook"], offering=offering, objective="Plan sign-ups in the service area",
                    direction={"summary": "A technician arriving on time at a tidy family home",
                               "scene": "Front porch of a suburban home in the morning, technician with a tool bag "
                                        "greeting the homeowner at the door",
                               "style": "Documentary photography, warm and honest",
                               "lighting": "Soft morning daylight", "camera": "35mm, eye level, shallow depth of field",
                               "composition": "Subject on the left third, open sky on the right for overlays",
                               "negative": ["uniform logos", "readable text on clothing"]},
                    copy_variants=[({"headline": "Your arrival window, kept", "primary_text": {
                        "short": "Two inspections a year, fixed quotes, no waiting around.",
                        "medium": "Two inspections a year and a fixed quote before any work starts.",
                        "long": "Two inspections a year, a fixed quote before any work starts, and arrival windows "
                                "you can plan your day around."}, "cta": "See the plan",
                        "claim_refs": [{"claim": "same-day callout", "source_id": f"src-{key}-plan"}]}, "passed"),
                        ({"headline": "Cheapest in town", "primary_text": {"short": "x"}, "cta": "Go",
                          "claim_refs": []}, "flagged")])
    return biz, camp


def physio_clinic(key: str = "physio") -> tuple[Business, Campaign]:
    biz = Business(key, label="Example Physio Clinic", domain="example-physio-clinic.test",
                   model="local_services", regulated="healthcare", offering_kind="service",
                   palette=[("#0E7C66", "primary"), ("#FFFFFF", "background")], tone=["reassuring", "clear"],
                   fonts=[("Example Humanist", ["400", "600"])], logo_roles=["primary"],
                   disclosures=[{"disclosure_id": "health.results_vary", "market": "GB",
                                 "text": "Individual results vary.", "applies_to": "all", "citation_url": None}])
    offering = {"kind": "service_group", "source": _vref(biz.mos_ws, "Offer", "rehab"), "name": "Back pain rehab",
                "summary": "Assessment with registered physiotherapists and a personalised exercise plan.",
                "value_propositions": ["A plan, not a quick fix"], "approved_claims": [],
                "physical_product_spec": None,
                "service_facts": [{"id": "assessment", "label": "Assessment",
                                   "value": "Registered physiotherapists assess how you sit, sleep and move",
                                   "source": _vref(biz.mos_ws, "Offer", "rehab")}]}
    camp = Campaign(biz, f"{key}-rehab", hook="Stiff back every morning?", angle="A plan, not a quick fix",
                    channels=["x", "linkedin"], offering=offering, objective="Assessment bookings",
                    direction={"summary": "A calm assessment in a bright treatment room",
                               "scene": "Physiotherapist guiding an adult patient through a gentle stretch",
                               "style": "Clean editorial healthcare photography", "lighting": "Bright diffuse daylight",
                               "camera": "50mm, slightly above eye level", "composition": "Centered pair, top band clear",
                               "negative": ["medical claims on screen", "x-ray imagery"]},
                    copy_variants=[({"headline": "A plan, not a quick fix", "primary_text": {
                        "short": "Stiff back every morning? Start with a plan. Individual results vary."},
                        "cta": "Book an assessment", "structure": {"kind": "thread", "items": [
                            {"index": 1, "text": "Stiff back every morning? A quick fix rarely lasts. Individual results vary."}]},
                        "claim_refs": [{"claim": "registered physiotherapists", "source_id": f"src-{key}-rehab"}]},
                        "passed"), ({"headline": "Feel better", "cta": "Book", "claim_refs": []}, "passed")],
                    selected_index=1)
    return biz, camp


def kitchenware(key: str = "kitchenware") -> tuple[Business, Campaign]:
    biz = Business(key, label="Example Kitchenware Co", domain="example-kitchenware.test",
                   model="e-commerce", regulated="none", offering_kind="physical_product",
                   palette=[("#1F2933", "primary"), ("#E4572E", "accent")], tone=["warm", "confident"],
                   fonts=[("Example Serif", ["400", "700"])], logo_roles=["primary"])
    spec = {"schema_version": "product-spec-v2", "id": "everyday-skillet", "name": "Everyday Skillet",
            "product": {"geometry": {"description": "Round 26 cm cast iron skillet with a long handle and pour spouts"},
                        "materials": [{"id": "body", "type": "cast iron", "finish": "matte black"}]},
            "constraints": {"preserve_geometry": True, "preserve_material": True, "preserve_proportions": True},
            "editing": {"protected_regions": ["handle"], "editable_regions": ["background"]}}
    offering = {"kind": "physical_product", "source": _vref(biz.mos_ws, "Offer", "skillet"), "name": "Everyday Skillet",
                "summary": "Pre-seasoned cast iron skillet for weeknight dinners.",
                "value_propositions": ["Pre-seasoned and ready to cook"], "approved_claims": [],
                "physical_product_spec": {"authority": "marketing-os", "workspace_id": biz.mos_ws,
                                          "kind": "ProductSpec", "id": uid("ProductSpec", "skillet"),
                                          "version_id": uid("ProductSpec", "skillet", 1), "revision": 1,
                                          "sha256": jcs_sha256(spec)},
                "service_facts": []}
    camp = Campaign(biz, f"{key}-skillet", hook="Your old pan won't make it", angle="Ready before the dinner party",
                    channels=["instagram"], offering=offering, objective="Product page visits",
                    direction={"summary": "The skillet searing vegetables on a home stove",
                               "scene": "Home kitchen counter, skillet on a gas hob with colourful vegetables",
                               "style": "Bright food photography", "lighting": "Window light from the left",
                               "camera": "Top-down 45 degrees", "composition": "Skillet centred, lower third clear",
                               "negative": ["brand names on the pan"]},
                    copy_variants=[({"headline": "Cook more, scrub less",
                                     "primary_text": {"short": "The Everyday Skillet: pre-seasoned and ready for tonight."},
                                     "cta": "Shop the skillet",
                                     "claim_refs": [{"claim": "pre-seasoned", "source_id": f"src-{key}-skillet"}]}, "passed")],
                    product_spec=spec)
    return biz, camp


class FakeMarketingOs:
    """Recorded-shape MOS gateway. Records every call; never touches the network."""

    def __init__(self, *pairs):
        self.biz, self.campaigns = {}, {}
        for pair in pairs:
            self.add(*pair)
        self.calls: list[tuple] = []
        self.unavailable = False
        self.tamper: dict[str, bytes] = {}
        self.deny_binding: set[str] = set()

    def add(self, biz, camp=None):
        self.biz[biz.mos_ws] = biz
        if camp is not None:
            self.campaigns[camp.placement_id] = camp
        return biz, camp

    def _check(self):
        if self.unavailable:
            raise GatewayUnavailable("mock upstream down")

    def authorize_binding(self, *, user_id, mos_workspace_id):
        self._check()
        self.calls.append(("authorize_binding", mos_workspace_id))
        allowed = mos_workspace_id in self.biz and mos_workspace_id not in self.deny_binding
        return {"authority": "marketing-os", "external_workspace_id": mos_workspace_id, "subject_user_id": user_id,
                "decision": "allowed" if allowed else "refused", "role": "owner",
                "checked_at": datetime.now(timezone.utc).isoformat()}

    def export_brand_kit(self, *, mos_workspace_id, artifact_id):
        self._check()
        self.calls.append(("export_brand_kit", mos_workspace_id, artifact_id))
        return self.biz[mos_workspace_id].export()

    def fetch_asset(self, *, mos_workspace_id, ref, purpose, max_bytes):
        self._check()
        self.calls.append(("fetch_asset", ref["asset_version_id"]))
        data = self.tamper.get(ref["asset_version_id"]) or self.biz[mos_workspace_id].blobs[ref["asset_version_id"]]
        return data[:max_bytes + 1]

    def campaign_sources(self, *, mos_workspace_id, visual_request):
        self._check()
        self.calls.append(("campaign_sources", visual_request["placement_id"]))
        return self.campaigns[visual_request["placement_id"]].sources()

    def eligibility(self, *, mos_workspace_id, user_id, action, source_versions):
        self._check()
        self.calls.append(("eligibility", action))
        now = datetime.now(timezone.utc).replace(microsecond=0)
        return _sealed({"id": str(uuid.uuid4()), "authority": "marketing-os", "workspace_id": mos_workspace_id,
                        "action": action, "decision": "allowed", "actor_id": user_id,
                        "source_versions": source_versions[:100],
                        "policy": _vref(mos_workspace_id, "GenerationPolicy", "default"),
                        "approval_receipt_ids": [], "checked_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "valid_until": (now + timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "reason_codes": [], "sha256": "0" * 64})
