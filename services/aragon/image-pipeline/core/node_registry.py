#!/usr/bin/env python3
"""Node registry — Phase 1 typed handlers and port contracts.

Registers the six linear-pipeline node types emitted by graph_compiler.py:
  intake.load → jobspec.validate → prompt.build → reference.resolve
  → engine.generate → output.persist

Handler stubs define the execution interface for Phase 2 scheduler wiring.
They do not perform runtime execution (no adapter calls, no provider I/O).

schemas/nodes/*.schema.json publish port payload contracts for editors.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Protocol

import prompt_compiler

SCHEMA_DIR = os.path.join(os.path.dirname(__file__), "..", "schemas", "nodes")

# Logical port types — shared with graph_ir edge compatibility checks.
_TYPE_COMPAT: dict[str, set[str]] = {
    "any": {
        "any",
        "string",
        "prompt",
        "job_spec",
        "image_path",
        "image_path_list",
        "engine_results",
        "file_list",
    },
    "image_path": {"image_path", "image_path_list", "any"},
    "image_path_list": {"image_path_list", "any"},
    "string": {"string", "prompt", "any"},
    "prompt": {"prompt", "string", "any"},
    "job_spec": {"job_spec", "any"},
    "engine_results": {"engine_results", "any"},
    "file_list": {"file_list", "any"},
    "compile_artifacts": {"compile_artifacts", "any"},
}

EXECUTION_NODE_TYPES: tuple[str, ...] = (
    "artifacts.load",
    "reference.resolve",
    "engine.generate",
    "output.persist",
)

PHASE1_NODE_TYPES: tuple[str, ...] = (
    "intake.load",
    "jobspec.validate",
    "prompt.build",
    "reference.resolve",
    "engine.generate",
    "output.persist",
) + EXECUTION_NODE_TYPES


class NodeRegistryError(ValueError):
    """Raised when a node type or port contract is invalid."""


class NodeHandler(Protocol):
    """Execution interface stub — Phase 2 scheduler invokes execute()."""

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
    """Registered node type with port map and JSON Schema payload contracts."""

    node_type: str
    description: str
    ports_in: dict[str, PortSpec] = field(default_factory=dict)
    ports_out: dict[str, PortSpec] = field(default_factory=dict)
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    handler_class: type | None = None


# ---------------------------------------------------------------------------
# Handler stubs — document adapter seams; no runtime execution in Phase 1.
# ---------------------------------------------------------------------------


class IntakeLoadHandler:
    """Load job-v1 from source path via intake adapter (Phase 2)."""

    node_type = "intake.load"

    def execute(
        self,
        inputs: dict[str, Any],
        params: dict[str, Any],
        ctx: dict[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError(
            "intake.load executes via adapters.get_intake in Phase 2."
        )


class JobSpecValidateHandler:
    """Validate job-v1 via jobspec.validate (Phase 2)."""

    node_type = "jobspec.validate"

    def execute(
        self,
        inputs: dict[str, Any],
        params: dict[str, Any],
        ctx: dict[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError(
            "jobspec.validate executes via jobspec.validate in Phase 2."
        )


class PromptBuildHandler:
    """Compile job-v1 → prompt via PromptCompiler (never raw cad.md)."""

    node_type = "prompt.build"
    compiler = prompt_compiler  # seam reference for scheduler wiring

    def execute(
        self,
        inputs: dict[str, Any],
        params: dict[str, Any],
        ctx: dict[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError(
            "prompt.build executes via PromptCompiler.compile_positive in Phase 2."
        )

    @staticmethod
    def compile_prompt(job: dict[str, Any]) -> str:
        """Documented compile seam — triple-spec via PromptCompiler."""
        return prompt_compiler.compile_positive(job)


class ReferenceResolveHandler:
    """Resolve reference field via reference adapter (Phase 2)."""

    node_type = "reference.resolve"

    def execute(
        self,
        inputs: dict[str, Any],
        params: dict[str, Any],
        ctx: dict[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError(
            "reference.resolve executes via adapters.get_reference in Phase 2."
        )


class EngineGenerateHandler:
    """Generate images via engine adapter (Phase 2)."""

    node_type = "engine.generate"

    def execute(
        self,
        inputs: dict[str, Any],
        params: dict[str, Any],
        ctx: dict[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError(
            "engine.generate executes via adapters.get_engine in Phase 2."
        )


class ArtifactsLoadHandler:
    """Load pre-compiled artifacts from ctx (Phase 2)."""

    node_type = "artifacts.load"

    def execute(
        self,
        inputs: dict[str, Any],
        params: dict[str, Any],
        ctx: dict[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError(
            "artifacts.load reads ctx['artifacts'] in Phase 2."
        )


class OutputPersistHandler:
    """Persist engine results via output adapter (Phase 2)."""

    node_type = "output.persist"

    def execute(
        self,
        inputs: dict[str, Any],
        params: dict[str, Any],
        ctx: dict[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError(
            "output.persist executes via adapters.get_output in Phase 2."
        )


def _job_spec_schema() -> dict[str, Any]:
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
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
            "_dir": {"type": "string"},
        },
        "additionalProperties": True,
    }


def _prompt_schema() -> dict[str, Any]:
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "string",
        "minLength": 1,
        "description": "Fully resolved positive prompt from PromptCompiler.",
    }


def _refs_schema() -> dict[str, Any]:
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
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


def _engine_results_schema() -> dict[str, Any]:
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
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
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "array",
        "items": {"type": "string"},
        "description": "Local filesystem paths of persisted outputs.",
    }


def _artifacts_schema() -> dict[str, Any]:
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "required": ["schema_version", "provider_prompt", "execution_plan"],
        "properties": {
            "schema_version": {"type": "string", "const": "compile-artifacts-v1"},
            "provider_prompt": {"type": "object"},
            "execution_plan": {"type": "object"},
            "execution_request": {"type": "object"},
            "persist_context": {"type": "object"},
            "job_dir": {"type": "string"},
        },
        "additionalProperties": True,
    }


def _build_registry() -> dict[str, NodeSpec]:
    job = _job_spec_schema()
    artifacts = _artifacts_schema()
    registry = {
        "intake.load": NodeSpec(
            node_type="intake.load",
            description="Load job-v1 from source path or stdin.",
            ports_out={"job": PortSpec("job", "job_spec")},
            input_schema={
                "$schema": "http://json-schema.org/draft-07/schema#",
                "type": "object",
                "required": ["source"],
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "Path to .job.json or '-' for stdin.",
                    },
                    "schema_version": {"type": "string", "const": "job-v1"},
                },
            },
            output_schema=job,
            handler_class=IntakeLoadHandler,
        ),
        "jobspec.validate": NodeSpec(
            node_type="jobspec.validate",
            description="Validate job-v1 against jobspec schema.",
            ports_in={"job": PortSpec("job", "job_spec")},
            ports_out={"job": PortSpec("job", "job_spec")},
            input_schema=job,
            output_schema=job,
            handler_class=JobSpecValidateHandler,
        ),
        "prompt.build": NodeSpec(
            node_type="prompt.build",
            description=(
                "Compile job-v1 to resolved prompt via PromptCompiler "
                "(ProductSpec + CreativeSpec + ExecutionRequest)."
            ),
            ports_in={"job": PortSpec("job", "job_spec")},
            ports_out={"prompt": PortSpec("prompt", "prompt")},
            input_schema=job,
            output_schema=_prompt_schema(),
            handler_class=PromptBuildHandler,
        ),
        "artifacts.load": NodeSpec(
            node_type="artifacts.load",
            description="Load pre-compiled artifacts from ctx (runtime never compiles).",
            ports_out={"artifacts": PortSpec("artifacts", "compile_artifacts")},
            input_schema={"$schema": "http://json-schema.org/draft-07/schema#", "type": "object"},
            output_schema=artifacts,
            handler_class=ArtifactsLoadHandler,
        ),
        "reference.resolve": NodeSpec(
            node_type="reference.resolve",
            description="Resolve look-anchor reference to local image path(s).",
            ports_in={"artifacts": PortSpec("artifacts", "compile_artifacts")},
            ports_out={"refs": PortSpec("refs", "image_path_list", required=False)},
            input_schema=artifacts,
            output_schema=_refs_schema(),
            handler_class=ReferenceResolveHandler,
        ),
        "engine.generate": NodeSpec(
            node_type="engine.generate",
            description="Execute ProviderExecutionPlan via engine adapter.",
            ports_in={
                "artifacts": PortSpec("artifacts", "compile_artifacts"),
                "refs": PortSpec("refs", "image_path_list", required=False),
            },
            output_schema=_engine_results_schema(),
            input_schema={
                "$schema": "http://json-schema.org/draft-07/schema#",
                "type": "object",
                "required": ["artifacts"],
                "properties": {
                    "artifacts": artifacts,
                    "refs": _refs_schema(),
                },
            },
            ports_out={"results": PortSpec("results", "engine_results")},
            handler_class=EngineGenerateHandler,
        ),
        "result.as_ref": NodeSpec(
            node_type="result.as_ref",
            description="Turn a prior engine.generate node's results into this chain link's ref output.",
            ports_in={"results": PortSpec("results", "engine_results")},
            ports_out={"refs": PortSpec("refs", "image_path")},
            input_schema={
                "$schema": "http://json-schema.org/draft-07/schema#",
                "type": "object",
                "required": ["results"],
                "properties": {"results": _engine_results_schema()},
            },
            output_schema=_refs_schema(),
            handler_class=None,
        ),
        "output.persist": NodeSpec(
            node_type="output.persist",
            description="Persist engine results to local filesystem.",
            ports_in={
                "results": PortSpec("results", "engine_results"),
                "artifacts": PortSpec("artifacts", "compile_artifacts"),
            },
            ports_out={"files": PortSpec("files", "file_list")},
            input_schema={
                "$schema": "http://json-schema.org/draft-07/schema#",
                "type": "object",
                "required": ["results", "artifacts"],
                "properties": {
                    "results": _engine_results_schema(),
                    "artifacts": artifacts,
                },
            },
            output_schema=_file_list_schema(),
            handler_class=OutputPersistHandler,
        ),
    }
    return registry


_REGISTRY: dict[str, NodeSpec] = _build_registry()


def list_node_types() -> list[str]:
    """Return all registered node type ids."""
    return sorted(_REGISTRY.keys())


def get(node_type: str) -> NodeSpec:
    """Lookup a registered node. Raises NodeRegistryError if unknown."""
    if node_type not in _REGISTRY:
        raise NodeRegistryError(
            f"Unknown node type {node_type!r}. "
            f"Registered: {sorted(_REGISTRY)}."
        )
    return _REGISTRY[node_type]


def get_handler(node_type: str) -> NodeHandler:
    """Instantiate the handler stub for a registered node type."""
    spec = get(node_type)
    if spec.handler_class is None:
        raise NodeRegistryError(f"No handler registered for {node_type!r}.")
    return spec.handler_class()


def types_compatible(src_type: str, dst_type: str) -> bool:
    """Return True when an edge may connect src output type to dst input type."""
    if src_type == dst_type:
        return True
    allowed = _TYPE_COMPAT.get(src_type, {src_type})
    return dst_type in allowed or dst_type == "any" or src_type == "any"


def validate_node_type(node_type: str) -> bool:
    """Ensure node_type is registered. Raises NodeRegistryError on miss."""
    get(node_type)
    return True


def validate_node_ports(node_type: str, ports: dict[str, Any] | None) -> bool:
    """Check graph node ports against registry declaration."""
    spec = get(node_type)
    ports = ports or {}
    for direction, declared in (("in", spec.ports_in), ("out", spec.ports_out)):
        side = ports.get(direction)
        if side is None:
            if declared:
                raise NodeRegistryError(
                    f"nodes type {node_type!r} requires ports.{direction} "
                    f"({sorted(declared)})."
                )
            continue
        if not isinstance(side, dict):
            raise NodeRegistryError(
                f"nodes type {node_type!r} ports.{direction} must be an object."
            )
        for pname, pspec in side.items():
            if pname not in declared:
                raise NodeRegistryError(
                    f"nodes type {node_type!r} has undeclared port "
                    f"{direction}.{pname!r}."
                )
            if not isinstance(pspec, dict) or not pspec.get("type"):
                raise NodeRegistryError(
                    f"nodes type {node_type!r} port {direction}.{pname} "
                    "requires a type string."
                )
            expected = declared[pname].type
            actual = pspec["type"]
            # reference.resolve / engine.generate accept image_path or list variant.
            if pname == "refs" and actual in ("image_path", "image_path_list"):
                if expected not in ("image_path", "image_path_list"):
                    raise NodeRegistryError(
                        f"nodes type {node_type!r} port refs type mismatch."
                    )
                continue
            if actual != expected and not types_compatible(actual, expected):
                raise NodeRegistryError(
                    f"nodes type {node_type!r} port {direction}.{pname} "
                    f"type {actual!r} != registry {expected!r}."
                )
    return True


def validate_payload(node_type: str, direction: str, payload: Any) -> bool:
    """Minimal structural validation against node input/output schema."""
    spec = get(node_type)
    schema = spec.input_schema if direction == "in" else spec.output_schema
    if not schema:
        return True
    return _validate_against_schema(payload, schema, node_type, direction)


def _validate_against_schema(
    payload: Any,
    schema: dict[str, Any],
    node_type: str,
    direction: str,
) -> bool:
    """Dep-free subset validator (matches spec_validator philosophy)."""
    if schema.get("type") == "string":
        if not isinstance(payload, str):
            raise NodeRegistryError(
                f"{node_type} {direction} payload must be a string."
            )
        if schema.get("minLength") and len(payload) < schema["minLength"]:
            raise NodeRegistryError(
                f"{node_type} {direction} payload string too short."
            )
        return True

    if schema.get("type") == "array":
        if not isinstance(payload, list):
            raise NodeRegistryError(
                f"{node_type} {direction} payload must be an array."
            )
        return True

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

    if schema.get("type") == "object":
        if not isinstance(payload, dict):
            raise NodeRegistryError(
                f"{node_type} {direction} payload must be an object."
            )
        for req in schema.get("required") or []:
            if req not in payload:
                raise NodeRegistryError(
                    f"{node_type} {direction} payload missing required {req!r}."
                )
        const = (schema.get("properties") or {}).get("schema_version", {}).get(
            "const"
        )
        if const and payload.get("schema_version") != const:
            raise NodeRegistryError(
                f"{node_type} {direction} schema_version must be {const!r}."
            )
        return True

    if payload is None and {"type": "null"} in (schema.get("oneOf") or []):
        return True

    return True


def load_published_schema(node_type: str, direction: str) -> dict[str, Any]:
    """Load schemas/nodes/<type>-<direction>.schema.json if present."""
    slug = node_type.replace(".", "-")
    path = os.path.join(SCHEMA_DIR, f"{slug}-{direction}.schema.json")
    if not os.path.isfile(path):
        raise NodeRegistryError(f"Published schema not found: {path}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


if __name__ == "__main__":
    print(json.dumps({"phase1_nodes": list_node_types()}, indent=2))
