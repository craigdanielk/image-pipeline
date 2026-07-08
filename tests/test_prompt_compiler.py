#!/usr/bin/env python3
"""Tests for core/prompt_compiler.py — determinism and job-v1 parity."""
from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

import jobspec  # noqa: E402
import prompt_compiler  # noqa: E402
import spec_compiler  # noqa: E402
import spec_validator  # noqa: E402

JOBS_DIR = os.path.join(os.path.dirname(__file__), "..", "jobs")
EXAMPLES_DIR = os.path.join(os.path.dirname(__file__), "..", "schemas", "examples")


class TestPromptCompiler(unittest.TestCase):
    def _fixture_triple(self):
        product = {
            "schema_version": "product-spec-v1",
            "id": "usb-a-key",
            "name": "USB-A Key",
            "immutable": True,
            "geometry_text": "Satin white body, brass NFC disc, USB-A blade.",
        }
        creative = {
            "schema_version": "creative-spec-v1",
            "id": "usb-a-hero-creative",
            "product_spec_id": "usb-a-key",
            "prompt_template": (
                "Studio shot of security key. VIEW: {view}. Geometry:\n\n{cad}"
            ),
            "default_subject": {"view": "three-quarter hero"},
        }
        execution = {
            "schema_version": "execution-request-v1",
            "id": "usb-a-scene-a",
            "subject": {},
            "reference": None,
            "output": {"imgtype": "scene", "count": 1},
            "engine": {"name": "higgsfield", "model": "nano_banana_2"},
        }
        return product, creative, execution

    def test_deterministic_byte_identical_output(self):
        product, creative, execution = self._fixture_triple()
        a = prompt_compiler.compile(product, creative, execution)
        b = prompt_compiler.compile(product, creative, execution)
        self.assertEqual(
            json.dumps(a, sort_keys=True, separators=(",", ":")),
            json.dumps(b, sort_keys=True, separators=(",", ":")),
        )
        self.assertEqual(a["metadata"]["content_hash"], b["metadata"]["content_hash"])

    def test_compiles_primikey_job(self):
        path = os.path.join(JOBS_DIR, "primikey-usba", "scene-float-a.job.json")
        job = jobspec.load(path)
        provider = prompt_compiler.compile_from_job_v1(job)
        spec_validator.validate_provider_prompt(provider)
        self.assertIn("satin-white", provider["positive"].lower())
        self.assertIn("three-quarter hero", provider["positive"])
        imgs = provider["input_assets"]["input_images"]
        self.assertIn("reference/plates/plate-2-hero34.png", imgs)

    def test_execution_request_from_all_canonical_jobs(self):
        for root, _dirs, files in os.walk(JOBS_DIR):
            for name in files:
                if not name.endswith(".job.json") or name.startswith("_"):
                    continue
                path = os.path.join(root, name)
                job = jobspec.load(path)
                req = spec_compiler.compile_execution_request(job, source_path=path)
                self.assertEqual(req["schema_version"], "execution-request-v1")
                self.assertEqual(req["id"], job["id"])

    def _edit(self, op, edit_id):
        return {
            "schema_version": spec_validator.EDIT_SCHEMA,
            "id": edit_id,
            "creative_spec_id": "usb-a-hero-creative",
            "operation": op,
            "parent_asset_id": "asset:path:anchor-1",
            "constraints": [],
            "exclusions": [],
            "provenance": {"compiled_from": "test"},
        }

    def test_single_edit_spec_folds_operation_into_positive_prompt(self):
        """compile() with exactly one edit_spec must produce a distinct
        positive prompt per operation, so N chained per-op nodes actually
        send the provider different instructions instead of duplicating
        the same edit N times."""
        product, creative, execution = self._fixture_triple()
        a = prompt_compiler.compile(
            product, creative, execution,
            edit_specs=[self._edit("replace_logo", "e-0")],
            fold_operation_into_prompt=True,
        )
        b = prompt_compiler.compile(
            product, creative, execution,
            edit_specs=[self._edit("subject_swap", "e-1")],
            fold_operation_into_prompt=True,
        )
        self.assertNotEqual(a["positive"], b["positive"])
        self.assertIn("replace the brand logo", a["positive"])
        self.assertIn("swap the subject", b["positive"])

    def test_merged_multi_edit_spec_path_unaffected_by_op_folding(self):
        """The existing compile_job merged-call shape (0 or 2+ edit_specs)
        must remain byte-identical to its pre-existing behavior — the new
        per-op instruction folding only applies when len(edit_specs) == 1."""
        product, creative, execution = self._fixture_triple()
        edits = [self._edit("replace_logo", "e-0"), self._edit("subject_swap", "e-1")]

        none_result = prompt_compiler.compile(product, creative, execution, edit_specs=None)
        empty_result = prompt_compiler.compile(product, creative, execution, edit_specs=[])
        self.assertEqual(none_result["positive"], empty_result["positive"])
        for result in (none_result, empty_result):
            for instruction in prompt_compiler._OP_INSTRUCTIONS.values():
                self.assertNotIn(instruction, result["positive"])

        merged = prompt_compiler.compile(product, creative, execution, edit_specs=edits)
        for instruction in prompt_compiler._OP_INSTRUCTIONS.values():
            self.assertNotIn(instruction, merged["positive"])
        # Merged (2 edit_specs) positive text is identical to the 0-edit_specs
        # case since neither path folds operation into the prompt.
        self.assertEqual(merged["positive"], none_result["positive"])

    def test_example_fixtures_validate(self):
        for filename in (
            "product-spec-primikey-usba.json",
            "creative-spec-primikey-usba.json",
            "execution-request-primikey-scene-float-a.json",
            "provider-prompt-primikey-scene-float-a.json",
        ):
            path = os.path.join(EXAMPLES_DIR, filename)
            with open(path) as f:
                data = json.load(f)
            ver = data.get("schema_version", "")
            if ver == "product-spec-v1":
                spec_validator.validate_product_spec(data)
            elif ver == "creative-spec-v1":
                spec_validator.validate_creative_spec(data)
            elif ver == "execution-request-v1":
                spec_validator.validate_execution_request(data)
            elif ver == "provider-prompt-v1":
                spec_validator.validate_provider_prompt(data)
            else:
                self.fail(f"Unknown schema in {filename}")


if __name__ == "__main__":
    unittest.main()
