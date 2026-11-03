"""CS-16 unit: deterministic hosted compile goldens (no database, no provider, no network)."""
from __future__ import annotations

import copy
import json
import os
import unittest

from service.errors import ServiceError
from service.plans import compiler
from service.providers.canonical import artifact_digest
from service.providers.contracts import validate
from tests.creative_studio.cs16_support import GOLD, catalogue, fixture, three_canvas

SHAPES = {
    # square + portrait + story canvases, logo + exact text layers
    "home-services-three-canvases": ("home-services.service", "home-services.accepted", "home-services.complete", True),
    # a service offering, single square canvas
    "home-services-square": ("home-services.service", "home-services.accepted", "home-services.complete", False),
    # service group with no product geometry, regulated vertical
    "physio-service-group": ("physio.service_group", "physio.accepted", "physio.regulated-healthcare", False),
}


def load(shape):
    rq, tr, pk, multi = SHAPES[shape]
    request = fixture("cs-creative-request-v1." + rq)
    package = fixture("brand-package-v1." + pk) if not pk.startswith("physio") else fixture("brand-package-v1." + pk)
    treatment = fixture("cs-treatment-v1." + tr)
    return (three_canvas(request) if multi else request), treatment, package


def build(shape, revision=1, tier="draft", cat=None):
    request, treatment, package = load(shape)
    return compiler.compile_plan(request=request, treatment=treatment, package=package,
                                 catalogue=cat or catalogue(), tier=tier, revision=revision)


class Goldens(unittest.TestCase):
    def test_golden_plans(self):
        for shape in SHAPES:
            plan, summary, key, used = build(shape)
            doc = {"plan": plan, "media_pack": summary, "content_key": key}
            path = GOLD / f"{shape}.json"
            if os.environ.get("CS16_WRITE_GOLDENS") == "1":
                path.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
            self.assertEqual(json.loads(json.dumps(doc)), json.loads(path.read_text(encoding="utf-8")), shape)
            validate("execution-plan-v1.schema.json", plan)
            self.assertEqual(plan["sha256"], artifact_digest(plan))

    def test_determinism_same_bytes_same_hash(self):
        for shape in SHAPES:
            a, b = build(shape), build(shape)
            self.assertEqual(json.dumps(a[0], sort_keys=True), json.dumps(b[0], sort_keys=True))
            self.assertEqual(a[0]["sha256"], b[0]["sha256"])
            self.assertEqual(a[2], b[2])

    def test_three_canvases_have_their_own_prompt_node_and_size(self):
        plan, summary, _, _ = build("home-services-three-canvases")
        sizes = [(c["width_px"], c["height_px"]) for c in summary["canvases"]]
        self.assertEqual(sizes, [(1080, 1080), (1080, 1350), (1080, 1920)])
        self.assertEqual(len(plan["program"]["nodes"]), 3)
        for c in summary["canvases"]:
            self.assertTrue(c["prompt"]["positive"])
        self.assertEqual(summary["estimated_credits"], None)
        self.assertEqual(summary["quote_status"], "unquoted")

    def test_text_and_logo_are_composition_layers_never_model_drawn(self):
        plan, summary, _, _ = build("home-services-square")
        request, _, package = load("home-services-square")
        self.assertEqual([t["text"] for t in summary["text_layers"]], [c["text"] for c in request["exact_copy"]])
        self.assertTrue(all(t["composed_by"] == "composition" and t["model_drawn"] is False
                            for t in summary["text_layers"]))
        for t in summary["text_layers"]:
            for n in plan["program"]["nodes"]:
                self.assertNotIn(t["text"], n["provider_call"]["positive_prompt"])
        self.assertEqual([l["sha256"] for l in summary["brand_elements"]["logos"]],
                         [l["asset"]["sha256"] for l in package["logos"]])
        self.assertIn("do not render any text", plan["program"]["nodes"][0]["provider_call"]["positive_prompt"])

    def test_service_offering_has_no_geometry(self):
        for shape in ("home-services-square", "physio-service-group"):
            text = json.dumps(build(shape)[0]).lower()
            for banned in ("geometry", "product_spec_id", "preserve exact product"):
                self.assertNotIn(banned, text)

    def test_changed_inputs_change_the_plan_and_pins(self):
        base = build("home-services-square")
        request, treatment, package = load("home-services-square")
        changed = copy.deepcopy(request)
        changed["sha256"] = "a" * 64
        changed["exact_copy"][0]["text"] = "Another headline"
        other = compiler.compile_plan(request=changed, treatment={**treatment, "source_request": {
            **treatment["source_request"], "sha256": "a" * 64}}, package=package, catalogue=catalogue(), tier="draft",
            revision=2)
        self.assertNotEqual(base[0]["sha256"], other[0]["sha256"])
        self.assertNotEqual(base[2], other[2])
        self.assertEqual(other[0]["revision"], 2)
        # a different catalogue price is a different plan too
        cat = catalogue()
        cat[0]["sha256"], cat[0]["unit_cost_micros"] = "b" * 64, 51000
        self.assertNotEqual(build("home-services-square", cat=cat)[2], base[2])

    def test_typed_refusals(self):
        request, treatment, package = load("home-services-square")
        with self.assertRaises(ServiceError) as c:
            compiler.compile_plan(request=request, treatment={**treatment, "status": "draft"}, package=package,
                                  catalogue=catalogue(), tier="draft", revision=1)
        self.assertEqual((c.exception.status, c.exception.code), (409, "treatment_not_frozen"))
        with self.assertRaises(ServiceError) as c:
            compiler.compile_plan(request=request, treatment=treatment, package=package, catalogue=[], tier="draft",
                                  revision=1)
        self.assertEqual((c.exception.status, c.exception.code), (409, "catalogue_unavailable"))
        over = copy.deepcopy(request)
        over["provider_selection"]["max_unit_cost_micros"] = 10
        with self.assertRaises(ServiceError) as c:
            compiler.compile_plan(request=over, treatment=treatment, package=package, catalogue=catalogue(),
                                  tier="draft", revision=1)
        self.assertEqual(c.exception.code, "catalogue_unavailable")
        small = copy.deepcopy(request)
        small["budget"]["max_cost_micros"] = 1
        with self.assertRaises(ServiceError) as c:
            compiler.compile_plan(request=small, treatment=treatment, package=package, catalogue=catalogue(),
                                  tier="draft", revision=1)
        self.assertEqual(c.exception.code, "plan_over_budget")

    def test_cheapest_entry_within_the_request_cap_is_chosen(self):
        plan = build("home-services-square")[0]
        self.assertEqual(plan["program"]["nodes"][0]["provider_call"]["catalogue_entry_id"], "hf-ms-low")
        self.assertEqual(plan["program"]["budget_ceiling_micros"], 50000)

    def test_compiler_is_offline_and_clockless(self):
        import ast
        tree = ast.parse(open(compiler.__file__).read())
        mods = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                mods |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom) and not n.level:
                mods.add(n.module.split(".")[0])
        self.assertEqual(mods - {"__future__", "re", "uuid"}, set())
        calls = {getattr(n.func, "attr", getattr(n.func, "id", "")) for n in ast.walk(tree) if isinstance(n, ast.Call)}
        self.assertFalse(calls & {"uuid4", "now", "time", "urlopen", "run", "Popen"})


class ReferencesAndCanvas(unittest.TestCase):
    """CS-22: references reach the provider call; each canvas renders at its own shape and priced resolution."""
    def gen_catalogue(self):
        cat = []
        for e in catalogue():
            g = copy.deepcopy(e)
            g["surface"] = "generate"
            g["entry_id"] = e["entry_id"].replace("hf-ms", "hf-gen")
            g["entry"]["reference_roles"] = [{"role": "image", "field": "image_references", "max_count": 14}]
            g["price_snapshot"]["quote_parameters"] = {"aspect_ratio": "1:1", "resolution": "2k"}
            cat.append(g)
        return cat

    def test_reference_bound_and_canvas_shape_set(self):
        request, treatment, package = load("home-services-three-canvases")
        ws = treatment.get("workspace_id") or request.get("workspace_id") or "00000000-0000-4000-8000-000000000001"
        draft = {"authority": "creative-studio", "workspace_id": ws, "asset_id": "11111111-1111-4111-8111-111111111111",
                 "asset_version_id": "22222222-2222-4222-8222-222222222222", "revision": 1, "sha256": "b" * 64,
                 "mime_type": "image/png"}
        request = dict(request, references=[{"id": "approved-draft", "role": "composition", "asset": draft,
                                             "required": True, "strength": None}])
        plan, summary, _, _ = compiler.compile_plan(request=request, treatment=treatment, package=package,
                                                    catalogue=self.gen_catalogue(), tier="draft", revision=1)
        nodes = plan["program"]["nodes"]
        self.assertEqual([n["provider_call"]["parameters"]["aspect_ratio"] for n in nodes], ["1:1", "4:5", "9:16"])
        self.assertTrue(all(n["provider_call"]["parameters"]["resolution"] == "2k" for n in nodes))
        for n in nodes:
            self.assertEqual(n["inputs"], [{"port": "references", "source": {"kind": "asset", "asset": draft}}])
            self.assertEqual(n["provider_call"]["media_bindings"],
                             [{"field": "image_references", "role": "image", "input_port": "references", "ordinal": 0}])
        self.assertTrue(summary["references"]["bound_to_provider_call"])
        validate("execution-plan-v1.schema.json", plan)

    def test_required_reference_refused_when_model_takes_none(self):
        request, treatment, package = load("home-services-square")
        cat = self.gen_catalogue()
        for e in cat:
            e["entry"]["reference_roles"] = []
        ref = {"authority": "creative-studio", "workspace_id": "00000000-0000-4000-8000-000000000001",
               "asset_id": "11111111-1111-4111-8111-111111111111", "asset_version_id": "22222222-2222-4222-8222-222222222222",
               "revision": 1, "sha256": "b" * 64, "mime_type": "image/png"}
        request = dict(request, references=[{"id": "approved-draft", "role": "composition", "asset": ref, "required": True,
                                             "strength": None}])
        with self.assertRaises(ServiceError) as c:
            compiler.compile_plan(request=request, treatment=treatment, package=package, catalogue=cat, tier="draft",
                                  revision=1)
        self.assertEqual(c.exception.code, "catalogue_unavailable")


if __name__ == "__main__":
    unittest.main()
