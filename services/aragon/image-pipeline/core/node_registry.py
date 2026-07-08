#!/usr/bin/env python3
"""Node registry — Phase 1 typed, independently addressable node handlers.

Registers the six Phase 1 pipeline stages as typed node handlers with
declared JSON Schema input/output ports:

  cad.load → prompt.build → reference.resolve
           → engine.cost → engine.generate → output.persist

Each node is an independently addressable unit: a caller can look one up by
type, inspect its ``input_schema`` / ``output_schema``, and (in Phase 2)
instantiate its handler for scheduler wiring. Phase 1 handlers are *stubs* —
they declare the execution seam but perform no runtime execution (no adapter
calls, no provider I/O, no LLM invocation).

Dep-free by design (bare Python 3.9+), matching the image-pipeline core.

Out of scope (Phase 2): execution scheduler, run_graph runtime, React Flow UI,
Supabase persistence.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Logical port types — shared with graph_ir edge compatibility checks.
# graph_ir imports ``types_compatible`` / ``ports_compatible`` from here so that
# the registry is the single source of truth for port typing.
# ---------------------------------------------------------------------------
_TYPE_COMPAT: dict[str, set[str]] = {
    "any": {
        "any",
        "string",
        "prompt",
        "job_spec",
        "cad_doc",
        "image_path",
        "image_path_list",
        "cost_estimate",
        "engine_results",
        "file_list",
    },
    "cad_doc": {"cad_doc", "any"},
    "job_spec": {"job_spec", "any"},
    "prompt": {"prompt", "string", "any"},
    "string": {"string", "prompt", "any"},
    "image_path": {"image_path", "image_path_list", "any"},
    "image_path_list": {"image_path_list", "any"},
    "cost_estimate": {"cost_estimate", "any"},
    "engine_results": {"engine_results", "any"},
    "file_list": {"file_list", "any"},
}

# The six Phase 1 node types, in linear pipeline order.
PHASE1_NODE_TYPES: tuple[str, ...] = (
    "cad.load",
    "prompt.build",
    "reference.resolve",
    "engine.cost",
    "engine.generate",
    "output.persist",
)


class NodeRegistryError(ValueError):
    """Raised when a node type or port contract is invalid."""


@runtime_checkable
class NodeHandler(Protocol):
    """Execution interface stub — Phase 2 scheduler invokes ``execute()``."""

    node_type: str

    def execute(
        self,
        inputs: dict[str, Any],
        params: dict[str, Any],
        ctx: dict[str, Any],
    ) -> dict[str, Any]:
        """Run node logic. Not implemented in Phase 1."""
        ...


@dataclass(frozen=True)
class PortSpec:
    """Declared graph port (name + logical type)."""

    name: str
    type: str
    required: bool = True


@dataclass(frozen=True)
class NodeSpec:
    """Registered node type: ports + JSON Schema payload contracts + handler."""

    node_type: str
    description: str
    ports_in: dict[str, PortSpec] = field(default_factory=dict)
    ports_out: dict[str, PortSpec] = field(default_factory=dict)
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    handler_class: type | None = None


# ---------------------------------------------------------------------------
# Handler stubs — document adapter seams; no runtime execution in Phase 1.
# Handlers are independently addressable: get_handler(type)() returns a unit
# whose execute() is the sole entry point the Phase 2 scheduler will call.
# ---------------------------------------------------------------------------


class _Phase1Handler:
    """Base stub: declares node_type, refuses runtime execution in Phase 1."""

    node_type: str = ""
    _seam: str = ""

    def execute(
        self,
        inputs: dict[str, Any],
        params: dict[str, Any],
        ctx: dict[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError(
            f"{self.node_type} executes via {self._seam} in Phase 2 "
            "(scheduler wiring). Phase 1 handlers are declaration-only stubs."
        )


class CadLoadHandler(_Phase1Handler):
    """Load a product's cad.md / ProductSpec into a structured CAD document."""

    node_type = "cad.load"
    _seam = "adapters.get_cad_loader"


class PromptBuildHandler(_Phase1Handler):
    """Compile CAD doc + job-v1 → resolved prompt via PromptCompiler."""

    node_type = "prompt.build"
    _seam = "PromptCompiler.compile_positive"


class ReferenceResolveHandler(_Phase1Handler):
    """Resolve reference field to local image path(s) via reference adapter."""

    node_type = "reference.resolve"
    _seam = "adapters.get_reference"


class EngineCostHandler(_Phase1Handler):
    """Estimate provider cost/quota for a generation before dispatch."""

    node_type = "engine.cost"
    _seam = "adapters.get_engine().estimate_cost"


class EngineGenerateHandler(_Phase1Handler):
    """Generate images via engine adapter (Phase 2)."""

    node_type = "engine.generate"
    _seam = "adapters.get_engine().generate"


class OutputPersistHandler(_Phase1Handler):
    """Persist engine results to configured output sink (Phase 2)."""

    node_type = "output.persist"
    _seam = "adapters.get_output"


# ---------------------------------------------------------------------------
# Payload schemas (draft-07 subset; dep-free structural validation).
# ---------------------------------------------------------------------------

_DRAFT = "http://json-schema.org/draft-07/schema#"


def _cad_doc_schema() -> dict[str, Any]:
    return {
        "$schema": _DRAFT,
        "type": "object",
        "required": ["schema_version", "product"],
        "properties": {
            "schema_version": {"type": "string", "const": "cad-doc-v1"},
            "product": {"type": "string"},
            "cad_markdown": {"type": "string"},
            "spec": {"type": "object"},
            "house_style": {"type": "object"},
        },
        "additionalProperties": True,
    }


def _job_spec_schema() -> dict[str, Any]:
    return {
        "$schema": _DRAFT,
        "type": "object",
        "required": ["schema_version", "id"],
        "properties": {
            "schema_version": {"type": "string", "const": "job-v1"},
            "id": {"type": "string"},
            "spec": {"type": "object"},
            "subject": {"type": "object"},
            "reference": {},
            "output": {"type": "object"},
            "engine": {"type": "object"},
        },
        "additionalProperties": True,
    }


def _prompt_schema() -> dict[str, Any]:
    return {
        "$schema": _DRAFT,
        "type": "string",
        "minLength": 1,
        "description": "Fully resolved positive prompt from PromptCompiler.",
    }


def _refs_schema() -> dict[str, Any]:
    return {
        "$schema": _DRAFT,
        "oneOf": [
            {"type": "string", "description": "Single resolved image path."},
            {
                "type": "array",
                "items": {"type": "string"},
                "description": "Multi-reference path list.",
            },
            {"type": "null", "description": "No reference (text-to-image)."},
        ],
    }


def _cost_estimate_schema() -> dict[str, Any]:
    return {
        "$schema": _DRAFT,
        "type": "object",
        "required": ["provider", "estimated_cost_usd"],
        "properties": {
            "provider": {"type": "string"},
            "estimated_cost_usd": {"type": "number"},
            "unit_count": {"type": "integer"},
            "currency": {"type": "string"},
        },
        "additionalProperties": True,
    }


def _engine_results_schema() -> dict[str, Any]:
    return {
        "$schema": _DRAFT,
        "type": "array",
        "items": {
            "type": "object",
            "required": ["result_url"],
            "properties": {
                "result_url": {"type": "string"},
                "seed": {},
                "meta": {"type": "object"},
            },
            "additionalProperties": True,
        },
    }


def _file_list_schema() -> dict[str, Any]:
    return {
        "$schema": _DRAFT,
        "type": "array",
        "items": {"type": "string"},
        "description": "Local filesystem paths of persisted outputs.",
    }


def _build_registry() -> dict[str, NodeSpec]:
    cad = _cad_doc_schema()
    job = _job_spec_schema()
    prompt = _prompt_schema()
    refs = _refs_schema()
    cost = _cost_estimate_schema()
    results = _engine_results_schema()

    registry: dict[str, NodeSpec] = {
        "cad.load": NodeSpec(
            node_type="cad.load",
            description="Load product cad.md / ProductSpec into a CAD document.",
            ports_in={"job": PortSpec("job", "job_spec")},
            ports_out={"cad": PortSpec("cad", "cad_doc")},
            input_schema=job,
            output_schema=cad,
            handler_class=CadLoadHandler,
        ),
        "prompt.build": NodeSpec(
            node_type="prompt.build",
            description=(
                "Compile CAD doc + job-v1 to a resolved prompt via "
                "PromptCompiler (ProductSpec + CreativeSpec + ExecutionRequest)."
            ),
            ports_in={
                "cad": PortSpec("cad", "cad_doc"),
                "job": PortSpec("job", "job_spec"),
            },
            ports_out={"prompt": PortSpec("prompt", "prompt")},
            input_schema={
                "$schema": _DRAFT,
                "type": "object",
                "required": ["cad", "job"],
                "properties": {"cad": cad, "job": job},
                "additionalProperties": True,
            },
            output_schema=prompt,
            handler_class=PromptBuildHandler,
        ),
        "reference.resolve": NodeSpec(
            node_type="reference.resolve",
            description="Resolve look-anchor reference to local image path(s).",
            ports_in={"job": PortSpec("job", "job_spec")},
            ports_out={"refs": PortSpec("refs", "image_path_list", required=False)},
            input_schema=job,
            output_schema=refs,
            handler_class=ReferenceResolveHandler,
        ),
        "engine.cost": NodeSpec(
            node_type="engine.cost",
            description="Estimate provider cost/quota before generation dispatch.",
            ports_in={
                "prompt": PortSpec("prompt", "prompt"),
                "refs": PortSpec("refs", "image_path_list", required=False),
            },
            ports_out={"cost": PortSpec("cost", "cost_estimate")},
            input_schema={
                "$schema": _DRAFT,
                "type": "object",
                "required": ["prompt"],
                "properties": {"prompt": prompt, "refs": refs},
                "additionalProperties": True,
            },
            output_schema=cost,
            handler_class=EngineCostHandler,
        ),
        "engine.generate": NodeSpec(
            node_type="engine.generate",
            description="Execute the provider generation via the engine adapter.",
            ports_in={
                "prompt": PortSpec("prompt", "prompt"),
                "refs": PortSpec("refs", "image_path_list", required=False),
                "cost": PortSpec("cost", "cost_estimate", required=False),
            },
            ports_out={"results": PortSpec("results", "engine_results")},
            input_schema={
                "$schema": _DRAFT,
                "type": "object",
                "required": ["prompt"],
                "properties": {"prompt": prompt, "refs": refs, "cost": cost},
                "additionalProperties": True,
            },
            output_schema=results,
            handler_class=EngineGenerateHandler,
        ),
        "output.persist": NodeSpec(
            node_type="output.persist",
            description="Persist engine results to the configured output sink.",
            ports_in={"results": PortSpec("results", "engine_results")},
            ports_out={"files": PortSpec("files", "file_list")},
            input_schema={
                "$schema": _DRAFT,
                "type": "object",
                "required": ["results"],
                "properties": {"results": results},
                "additionalProperties": True,
            },
            output_schema=_file_list_schema(),
            handler_class=OutputPersistHandler,
        ),
    }
    return registry


_REGISTRY: dict[str, NodeSpec] = _build_registry()


# ---------------------------------------------------------------------------
# Public lookup / validation API.
# ---------------------------------------------------------------------------


def list_node_types() -> list[str]:
    """Return all registered node type ids (sorted)."""
    return sorted(_REGISTRY.keys())


def get(node_type: str) -> NodeSpec:
    """Lookup a registered node. Raises NodeRegistryError if unknown."""
    if node_type not in _REGISTRY:
        raise NodeRegistryError(
            f"Unknown node type {node_type!r}. Registered: {sorted(_REGISTRY)}."
        )
    return _REGISTRY[node_type]


def get_handler(node_type: str) -> NodeHandler:
    """Instantiate the handler stub for a registered node type."""
    spec = get(node_type)
    if spec.handler_class is None:
        raise NodeRegistryError(f"No handler registered for {node_type!r}.")
    return spec.handler_class()


def input_schema(node_type: str) -> dict[str, Any]:
    """Return the declared input JSON Schema for a node type."""
    return get(node_type).input_schema


def output_schema(node_type: str) -> dict[str, Any]:
    """Return the declared output JSON Schema for a node type."""
    return get(node_type).output_schema


def types_compatible(src_type: str, dst_type: str) -> bool:
    """Return True when an edge may connect src output type to dst input type."""
    if src_type == dst_type:
        return True
    if src_type == "any" or dst_type == "any":
        return True
    return dst_type in _TYPE_COMPAT.get(src_type, {src_type})


def ports_compatible(
    src_node_type: str,
    src_port: str,
    dst_node_type: str,
    dst_port: str,
) -> bool:
    """Port-compatibility check for graph_ir edge validation.

    Looks up the declared output port on the source node and the declared input
    port on the destination node, then checks logical type compatibility.
    Raises NodeRegistryError if either port is undeclared.
    """
    src_spec = get(src_node_type)
    dst_spec = get(dst_node_type)
    if src_port not in src_spec.ports_out:
        raise NodeRegistryError(
            f"{src_node_type!r} has no output port {src_port!r} "
            f"(declared: {sorted(src_spec.ports_out)})."
        )
    if dst_port not in dst_spec.ports_in:
        raise NodeRegistryError(
            f"{dst_node_type!r} has no input port {dst_port!r} "
            f"(declared: {sorted(dst_spec.ports_in)})."
        )
    return types_compatible(
        src_spec.ports_out[src_port].type,
        dst_spec.ports_in[dst_port].type,
    )


def validate_node_type(node_type: str) -> bool:
    """Ensure node_type is registered. Raises NodeRegistryError on miss."""
    get(node_type)
    return True


def validate_payload(node_type: str, direction: str, payload: Any) -> bool:
    """Structural validation of a payload against a node's in/out schema."""
    spec = get(node_type)
    if direction not in ("in", "out"):
        raise NodeRegistryError(f"direction must be 'in' or 'out', got {direction!r}.")
    schema = spec.input_schema if direction == "in" else spec.output_schema
    if not schema:
        return True
    return _validate_against_schema(payload, schema, node_type, direction)


def validate_schema_shape(node_type: str) -> bool:
    """Assert a node declares non-empty, well-formed in/out JSON Schemas."""
    spec = get(node_type)
    for label, schema in (("input", spec.input_schema), ("output", spec.output_schema)):
        if not isinstance(schema, dict) or not schema:
            raise NodeRegistryError(
                f"{node_type!r} {label}_schema must be a non-empty object."
            )
        if "type" not in schema and "oneOf" not in schema:
            raise NodeRegistryError(
                f"{node_type!r} {label}_schema needs a 'type' or 'oneOf' key."
            )
    return True


def _validate_against_schema(
    payload: Any,
    schema: dict[str, Any],
    node_type: str,
    direction: str,
) -> bool:
    """Dep-free subset validator (matches spec_validator philosophy)."""
    if schema.get("oneOf"):
        for alt in schema["oneOf"]:
            try:
                _validate_against_schema(payload, alt, node_type, direction)
                return True
            except NodeRegistryError:
                continue
        raise NodeRegistryError(
            f"{node_type} {direction} payload matches no oneOf variant."
        )

    stype = schema.get("type")

    if stype == "null":
        if payload is not None:
            raise NodeRegistryError(f"{node_type} {direction} payload must be null.")
        return True

    if stype == "string":
        if not isinstance(payload, str):
            raise NodeRegistryError(f"{node_type} {direction} payload must be a string.")
        if schema.get("minLength") and len(payload) < schema["minLength"]:
            raise NodeRegistryError(f"{node_type} {direction} payload string too short.")
        return True

    if stype == "array":
        if not isinstance(payload, list):
            raise NodeRegistryError(f"{node_type} {direction} payload must be an array.")
        return True

    if stype in ("number", "integer"):
        if isinstance(payload, bool) or not isinstance(payload, (int, float)):
            raise NodeRegistryError(f"{node_type} {direction} payload must be numeric.")
        return True

    if stype == "object":
        if not isinstance(payload, dict):
            raise NodeRegistryError(f"{node_type} {direction} payload must be an object.")
        for req in schema.get("required") or []:
            if req not in payload:
                raise NodeRegistryError(
                    f"{node_type} {direction} payload missing required {req!r}."
                )
        const = (schema.get("properties") or {}).get("schema_version", {}).get("const")
        if const and payload.get("schema_version") != const:
            raise NodeRegistryError(
                f"{node_type} {direction} schema_version must be {const!r}."
            )
        return True

    return True


if __name__ == "__main__":
    print(json.dumps({"phase1_nodes": list_node_types()}, indent=2))
