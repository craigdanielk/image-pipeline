#!/usr/bin/env python3
"""Tests for the Phase 1 node registry.

Verifies:
  * all six Phase 1 node types resolve,
  * each declares non-empty input_schema and output_schema,
  * payload validation accepts good payloads and rejects bad ones,
  * graph_ir uses the registry for port-compatibility checks.

Dep-free: plain asserts, runnable via `python3 test_node_registry.py` or
`python3 -m pytest`.
"""
from __future__ import annotations

import os
import sys

_CORE = os.path.join(os.path.dirname(__file__), "..", "core")
sys.path.insert(0, os.path.abspath(_CORE))

import node_registry  # noqa: E402
import graph_ir  # noqa: E402

EXPECTED_SIX = {
    "cad.load",
    "prompt.build",
    "reference.resolve",
    "engine.cost",
    "engine.generate",
    "output.persist",
}


def test_registry_registers_exactly_the_six_phase1_nodes():
    assert set(node_registry.list_node_types()) == EXPECTED_SIX
    assert set(node_registry.PHASE1_NODE_TYPES) == EXPECTED_SIX
    assert len(node_registry.PHASE1_NODE_TYPES) == 6


def test_all_six_node_types_resolve():
    for ntype in EXPECTED_SIX:
        spec = node_registry.get(ntype)
        assert spec.node_type == ntype
        handler = node_registry.get_handler(ntype)
        assert handler.node_type == ntype
        # Handler satisfies the NodeHandler protocol.
        assert isinstance(handler, node_registry.NodeHandler)


def test_each_node_declares_input_and_output_schema():
    for ntype in EXPECTED_SIX:
        spec = node_registry.get(ntype)
        assert spec.input_schema, f"{ntype} missing input_schema"
        assert spec.output_schema, f"{ntype} missing output_schema"
        # Well-formed schema shape (type or oneOf present).
        assert node_registry.validate_schema_shape(ntype) is True
        assert node_registry.input_schema(ntype) == spec.input_schema
        assert node_registry.output_schema(ntype) == spec.output_schema


def test_unknown_node_type_raises():
    try:
        node_registry.get("does.not.exist")
    except node_registry.NodeRegistryError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected NodeRegistryError")


def test_payload_validation_accepts_valid_payloads():
    assert node_registry.validate_payload(
        "cad.load", "in", {"schema_version": "job-v1", "id": "j1"}
    )
    assert node_registry.validate_payload(
        "cad.load", "out", {"schema_version": "cad-doc-v1", "product": "grip"}
    )
    assert node_registry.validate_payload("prompt.build", "out", "a resolved prompt")
    assert node_registry.validate_payload(
        "reference.resolve", "out", ["/tmp/a.png", "/tmp/b.png"]
    )
    assert node_registry.validate_payload("reference.resolve", "out", None)
    assert node_registry.validate_payload(
        "engine.cost", "out", {"provider": "fal", "estimated_cost_usd": 0.04}
    )
    assert node_registry.validate_payload(
        "engine.generate", "out", [{"result_url": "https://x/y.png"}]
    )
    assert node_registry.validate_payload("output.persist", "out", ["/out/1.png"])


def test_payload_validation_rejects_bad_payloads():
    # prompt.build output must be a non-empty string.
    for bad in (123, "", None):
        try:
            node_registry.validate_payload("prompt.build", "out", bad)
        except node_registry.NodeRegistryError:
            pass
        else:  # pragma: no cover
            raise AssertionError(f"expected rejection for {bad!r}")

    # cad.load output missing required field.
    try:
        node_registry.validate_payload("cad.load", "out", {"product": "grip"})
    except node_registry.NodeRegistryError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected rejection for missing schema_version")

    # engine.cost output missing estimated_cost_usd.
    try:
        node_registry.validate_payload("engine.cost", "out", {"provider": "fal"})
    except node_registry.NodeRegistryError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected rejection for missing cost")


def test_phase1_handlers_are_declaration_only_stubs():
    # Handlers are addressable but refuse runtime execution in Phase 1.
    for ntype in EXPECTED_SIX:
        handler = node_registry.get_handler(ntype)
        try:
            handler.execute({}, {}, {})
        except NotImplementedError:
            pass
        else:  # pragma: no cover
            raise AssertionError(f"{ntype} handler should be a Phase 1 stub")


def test_types_compatible_matrix():
    assert node_registry.types_compatible("prompt", "prompt")
    assert node_registry.types_compatible("prompt", "string")
    assert node_registry.types_compatible("image_path", "image_path_list")
    assert node_registry.types_compatible("cost_estimate", "any")
    assert not node_registry.types_compatible("job_spec", "engine_results")


def test_graph_ir_uses_registry_for_port_compat():
    # Compatible edge (prompt.build.prompt -> engine.cost.prompt) is accepted.
    g = graph_ir.GraphIR()
    g.add_node("a", "prompt.build")
    g.add_node("b", "engine.cost")
    edge = g.add_edge("a", "prompt", "b", "prompt")
    assert edge.dst_node == "b"
    assert g.validate() is True

    # Incompatible edge (cad.load.cad -> output.persist.results) is rejected.
    g2 = graph_ir.GraphIR()
    g2.add_node("c", "cad.load")
    g2.add_node("d", "output.persist")
    try:
        g2.add_edge("c", "cad", "d", "results")
    except node_registry.NodeRegistryError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected incompatible-edge rejection")

    # Undeclared port is rejected via registry lookup.
    try:
        node_registry.ports_compatible("cad.load", "nope", "prompt.build", "cad")
    except node_registry.NodeRegistryError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected undeclared-port rejection")


def test_canonical_linear_phase1_graph_validates():
    g = graph_ir.linear_phase1_graph()
    assert len(g.nodes) == 6
    assert g.validate() is True


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"PASS {fn.__name__}")
    print(f"\n{len(fns)} tests passed.")


if __name__ == "__main__":
    _run_all()
