#!/usr/bin/env python3
"""Tests for core/node_registry.py"""
from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

import graph_compiler  # noqa: E402
import graph_ir  # noqa: E402
import node_registry  # noqa: E402
import prompt_compiler  # noqa: E402


JOBS_DIR = os.path.join(os.path.dirname(__file__), "..", "jobs")


class TestNodeRegistry(unittest.TestCase):
    def test_phase1_node_types_registered(self):
        types = node_registry.list_node_types()
        self.assertEqual(len(types), 7)
        for ntype in node_registry.PHASE1_NODE_TYPES:
            self.assertIn(ntype, types)

    def test_each_node_has_input_and_output_schemas(self):
        for ntype in node_registry.PHASE1_NODE_TYPES:
            spec = node_registry.get(ntype)
            if ntype in ("intake.load", "artifacts.load"):
                self.assertTrue(spec.output_schema)
            else:
                self.assertTrue(spec.input_schema, ntype)
                self.assertTrue(spec.output_schema, ntype)

    def test_unknown_node_raises(self):
        with self.assertRaises(node_registry.NodeRegistryError):
            node_registry.get("cad.load")

    def test_handler_stubs_instantiate(self):
        for ntype in node_registry.PHASE1_NODE_TYPES:
            handler = node_registry.get_handler(ntype)
            self.assertEqual(handler.node_type, ntype)
            with self.assertRaises(NotImplementedError):
                handler.execute({}, {}, {})

    def test_prompt_build_references_prompt_compiler(self):
        handler = node_registry.get_handler("prompt.build")
        self.assertIs(node_registry.PromptBuildHandler.compiler, prompt_compiler)
        self.assertIs(handler.compiler, prompt_compiler)
        job = {
            "schema_version": "job-v1",
            "id": "registry-prompt-test",
            "spec": {
                "prompt_template": "Product {product} on white.",
                "cad": "Compact hairband geometry for prompt compile test.",
            },
            "subject": {"product": "hairband"},
            "output": {"imgtype": "hero", "count": 1},
            "engine": {"name": "higgsfield", "model": "nano_banana_2"},
            "_dir": "/tmp",
        }
        prompt = node_registry.PromptBuildHandler.compile_prompt(job)
        self.assertIn("hairband", prompt)

    def test_validate_payload_job_spec(self):
        job = {
            "schema_version": "job-v1",
            "id": "payload-test",
            "spec": {},
            "output": {"count": 1},
            "engine": {"name": "higgsfield", "model": "nano_banana_2"},
        }
        self.assertTrue(
            node_registry.validate_payload("jobspec.validate", "in", job)
        )

    def test_validate_payload_prompt_string(self):
        self.assertTrue(
            node_registry.validate_payload("prompt.build", "out", "A valid prompt.")
        )
        with self.assertRaises(node_registry.NodeRegistryError):
            node_registry.validate_payload("prompt.build", "out", "")

    def test_types_compatible_image_path_widening(self):
        self.assertTrue(
            node_registry.types_compatible("image_path", "image_path_list")
        )
        self.assertFalse(
            node_registry.types_compatible("job_spec", "prompt")
        )

    def test_published_schemas_on_disk(self):
        for ntype in node_registry.PHASE1_NODE_TYPES:
            slug = ntype.replace(".", "-")
            for direction in ("in", "out"):
                path = os.path.join(
                    os.path.dirname(__file__),
                    "..",
                    "schemas",
                    "nodes",
                    f"{slug}-{direction}.schema.json",
                )
                if ntype == "intake.load" and direction == "in":
                    continue
                if ntype == "artifacts.load" and direction == "in":
                    continue
                self.assertTrue(os.path.isfile(path), path)
                schema = node_registry.load_published_schema(ntype, direction)
                self.assertIn("$schema", schema)

    def test_graph_compiler_nodes_pass_registry_validation(self):
        path = os.path.join(JOBS_DIR, "primikey-usba", "scene-float-a.job.json")
        graph = graph_compiler.compile_path(path)
        graph_ir.validate(graph)
        for node in graph["nodes"]:
            spec = node_registry.get(node["type"])
            node_registry.validate_node_ports(node["type"], node.get("ports"))
            self.assertTrue(spec.input_schema or spec.output_schema)

    def test_unknown_graph_node_type_rejected(self):
        graph = {
            "schema_version": "graph-v1",
            "id": "bad-graph",
            "nodes": [
                {
                    "id": "x",
                    "type": "cad.load",
                    "ports": {"out": {"job": {"type": "job_spec"}}},
                }
            ],
            "edges": [],
        }
        with self.assertRaises(graph_ir.GraphIRError) as ctx:
            graph_ir.validate(graph)
        self.assertIn("Unknown node type", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
