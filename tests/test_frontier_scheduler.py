#!/usr/bin/env python3
"""Tests for the frontier-based async scheduler (graph_ir.GraphFrontier +
run_graph._schedule) — spec: 2026-07-08-execution-engine-rewrite-design.md."""
from __future__ import annotations

import asyncio
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "core"))

import graph_ir  # noqa: E402
import run_graph  # noqa: E402


def _edge(src: str, dst: str) -> dict:
    return {"from": {"node": src, "port": "out"}, "to": {"node": dst, "port": "in"}}


def _graph(node_ids: list[str], edges: list[tuple[str, str]]) -> dict:
    return {
        "schema_version": "graph-v1",
        "id": "test-graph",
        "nodes": [{"id": nid, "type": "test.node"} for nid in node_ids],
        "edges": [_edge(s, d) for s, d in edges],
    }


def _diamond() -> dict:
    # A→B, A→C, B→D, C→D
    return _graph(["a", "b", "c", "d"], [("a", "b"), ("a", "c"), ("b", "d"), ("c", "d")])


class TestGraphFrontier(unittest.TestCase):
    def test_diamond_ready_progression(self):
        f = graph_ir.GraphFrontier(_diamond())
        self.assertEqual(f.ready(), ["a"])
        f.dispatch("a")
        self.assertEqual(f.ready(), [])
        f.complete("a")
        self.assertEqual(set(f.ready()), {"b", "c"})
        f.dispatch("b")
        f.complete("b")
        # D not ready until BOTH B and C complete
        self.assertEqual(f.ready(), ["c"])
        f.dispatch("c")
        f.complete("c")
        self.assertEqual(f.ready(), ["d"])
        f.dispatch("d")
        f.complete("d")
        self.assertTrue(f.done())
        self.assertEqual(set(f.states().values()), {"completed"})

    def test_fail_skips_downstream_only(self):
        f = graph_ir.GraphFrontier(_diamond())
        f.dispatch("a")
        f.complete("a")
        f.dispatch("b")
        skipped = f.fail("b")
        self.assertEqual(skipped, ["d"])
        # C untouched — still eligible
        self.assertEqual(f.ready(), ["c"])
        f.dispatch("c")
        f.complete("c")
        self.assertTrue(f.done())
        states = f.states()
        self.assertEqual(states["b"], "failed")
        self.assertEqual(states["d"], "skipped")
        self.assertEqual(states["c"], "completed")
        self.assertEqual(run_graph._run_status(states), "partial")

    def test_dispatch_guards(self):
        f = graph_ir.GraphFrontier(_diamond())
        with self.assertRaises(graph_ir.GraphIRError):
            f.dispatch("d")  # upstream incomplete
        f.dispatch("a")
        with self.assertRaises(graph_ir.GraphIRError):
            f.dispatch("a")  # already dispatched
        with self.assertRaises(graph_ir.GraphIRError):
            f.complete("b")  # never dispatched

    def test_run_status_values(self):
        self.assertEqual(run_graph._run_status({"a": "completed"}), "completed")
        self.assertEqual(
            run_graph._run_status({"a": "failed", "b": "skipped"}), "failed"
        )
        self.assertEqual(
            run_graph._run_status({"a": "completed", "b": "failed"}), "partial"
        )


class TestSliceToTarget(unittest.TestCase):
    def test_slices_to_ancestor_set(self):
        # a→b→d plus disconnected c
        g = _graph(["a", "b", "c", "d"], [("a", "b"), ("b", "d")])
        sliced = graph_ir.slice_to_target(g, "b")
        self.assertEqual({n["id"] for n in sliced["nodes"]}, {"a", "b"})
        self.assertEqual(len(sliced["edges"]), 1)

    def test_unknown_target_raises(self):
        g = _graph(["a"], [])
        with self.assertRaises(graph_ir.GraphIRError):
            graph_ir.slice_to_target(g, "nope")


class TestScheduler(unittest.TestCase):
    def _run(self, graph, execute_fn, on_status=None):
        with mock.patch("graph_runtime.execute_node", side_effect=execute_fn):
            return asyncio.run(
                run_graph._schedule(graph, {}, on_status=on_status)
            )

    def test_failure_isolation(self):
        events = []

        def fake(node, inputs, ctx):
            if node["id"] == "b":
                raise RuntimeError("boom")
            return {"out": node["id"]}

        frontier, outputs, errors = self._run(
            _diamond(), fake, on_status=events.append
        )
        states = frontier.states()
        self.assertEqual(states["b"], "failed")
        self.assertEqual(states["d"], "skipped")
        self.assertEqual(states["c"], "completed")
        self.assertIn("b", errors)
        self.assertNotIn("d", outputs)
        self.assertEqual(run_graph._run_status(states), "partial")
        # skipped node emitted a status event, never a "running" one
        d_events = [e["status"] for e in events if e["node_id"] == "d"]
        self.assertEqual(d_events, ["skipped"])

    def test_target_slicing_leaves_disconnected_branch_untouched(self):
        g = _graph(["a", "b", "c"], [("a", "b")])  # c disconnected
        sliced = graph_ir.slice_to_target(g, "b")
        executed = []

        def fake(node, inputs, ctx):
            executed.append(node["id"])
            return {"out": node["id"]}

        frontier, _, errors = self._run(sliced, fake)
        self.assertEqual(set(executed), {"a", "b"})
        self.assertNotIn("c", frontier.states())
        self.assertEqual(errors, {})

    def test_parallel_leaves_run_concurrently(self):
        # 1 root, 3 independent leaves — wall clock ~max, not sum
        g = _graph(
            ["root", "l1", "l2", "l3"],
            [("root", "l1"), ("root", "l2"), ("root", "l3")],
        )
        sleep_s = 0.3

        def fake(node, inputs, ctx):
            if node["id"] != "root":
                time.sleep(sleep_s)
            return {"out": node["id"]}

        t0 = time.monotonic()
        frontier, _, errors = self._run(g, fake)
        wall = time.monotonic() - t0
        self.assertEqual(errors, {})
        self.assertTrue(frontier.done())
        # serial would be >= 0.9s; concurrent should be well under 2x one leaf
        self.assertLess(wall, sleep_s * 2, f"leaves ran serially: {wall:.2f}s")

    def test_inputs_wired_from_upstream_outputs(self):
        g = _graph(["a", "b"], [("a", "b")])
        seen = {}

        def fake(node, inputs, ctx):
            seen[node["id"]] = inputs
            return {"out": f"val-{node['id']}"}

        self._run(g, fake)
        self.assertEqual(seen["b"], {"in": "val-a"})


if __name__ == "__main__":
    unittest.main()
