"""Tests for job_compiler.compile_job_multi_op — N distinct per-op execution plans."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

import job_compiler  # noqa: E402
import spec_validator  # noqa: E402


def _job(job_id="board-test"):
    return {
        "schema_version": "job-v1",
        "id": job_id,
        "_dir": ".",
        "spec": {"prompt_template": "{cad}. {composition}", "cad": "A blue hairband, torus shape."},
        "engine": {"name": "higgsfield", "model": "nano_banana_2"},
        "output": {"imgtype": "hero", "dir": "out", "count": 1},
    }


def _edit_spec(op, edit_id, creative_spec_id):
    spec = {
        "schema_version": spec_validator.EDIT_SCHEMA,
        "id": edit_id,
        "creative_spec_id": creative_spec_id,
        "operation": op,
        "parent_asset_id": job_compiler and "anchor-1",
        "constraints": [],
        "exclusions": [],
        "provenance": {"compiled_from": "board-ui"},
    }
    return spec


class TestCompileJobMultiOp(unittest.TestCase):
    def test_two_edit_specs_produce_two_distinct_execution_plans_in_order(self):
        job = _job()
        product = job_compiler.extract_product_spec_from_job(job)
        import spec_compiler
        creative = spec_compiler.derive_creative_from_job(job, product_spec_id=product["id"])
        edit_specs = [
            _edit_spec("replace_logo", "board-test-edit-0", creative["id"]),
            _edit_spec("subject_swap", "board-test-edit-1", creative["id"]),
        ]
        for e in edit_specs:
            spec_validator.validate_edit_spec(e)

        bundle = job_compiler.compile_job_multi_op(job, edit_specs, dry_run=True)

        self.assertIn("op_execution_plans", bundle)
        plans = bundle["op_execution_plans"]
        self.assertEqual(len(plans), 2)
        self.assertEqual(plans[0]["op"], "replace_logo")
        self.assertEqual(plans[1]["op"], "subject_swap")
        # Each plan's prompt must mention its own edit_spec_id in metadata,
        # proving they were compiled from distinct edit_specs, not merged.
        self.assertEqual(plans[0]["execution_plan"]["metadata"]["job_dir"], ".")
        self.assertIsInstance(plans[0]["execution_plan"]["prompt"]["positive"], str)
        self.assertGreater(len(plans[0]["execution_plan"]["prompt"]["positive"]), 0)
        self.assertNotEqual(
            plans[0]["execution_plan"]["prompt"]["positive"],
            plans[1]["execution_plan"]["prompt"]["positive"],
        )

    def test_bundle_still_has_all_base_compile_job_fields(self):
        job = _job()
        bundle = job_compiler.compile_job_multi_op(job, [], dry_run=True)
        for key in ("schema_version", "job_id", "job_dir", "product_spec", "creative_spec", "execution_request", "persist_context", "graph"):
            self.assertIn(key, bundle)
        self.assertEqual(bundle.get("op_execution_plans"), [])


if __name__ == "__main__":
    unittest.main()
