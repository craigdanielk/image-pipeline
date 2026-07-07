#!/usr/bin/env python3
"""Convert graph-v1 IR to React Flow nodes/edges for operator UI."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# Allow import from core when run as script
_CORE = Path(__file__).resolve().parent.parent / "core"
if str(_CORE) not in sys.path:
    sys.path.insert(0, str(_CORE))

import graph_ir  # noqa: E402

NODE_W = 220
NODE_H = 72
X_COL = 0
Y_GAP = 110

NODE_TYPE_LABELS: dict[str, str] = {
    "artifacts.load": "Load specs",
    "reference.resolve": "Match references",
    "engine.generate": "Generate image",
    "output.persist": "Save output",
}


def _display_label(node_type: str, node_id: str) -> str:
    return NODE_TYPE_LABELS.get(node_type, node_id.replace("_", " ").title())


def graph_v1_to_reactflow(
    graph: dict[str, Any],
    *,
    node_status: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Return {nodes, edges} for @xyflow/react."""
    graph_ir.validate(graph)
    order = graph_ir.topological_sort(graph)
    status = node_status or {}
    positions = {nid: idx for idx, nid in enumerate(order)}

    rf_nodes: list[dict[str, Any]] = []
    for node in graph["nodes"]:
        nid = node["id"]
        row = positions.get(nid, 0)
        node_type = node.get("type", "")
        rf_nodes.append(
            {
                "id": nid,
                "type": "compilerNode",
                "position": {"x": X_COL, "y": row * Y_GAP},
                "data": {
                    "label": nid,
                    "displayLabel": _display_label(node_type, nid),
                    "nodeType": node_type,
                    "status": status.get(nid, "idle"),
                    "params": node.get("params") or {},
                },
            }
        )

    rf_edges: list[dict[str, Any]] = []
    for i, edge in enumerate(graph.get("edges") or []):
        src = edge.get("from") or {}
        dst = edge.get("to") or {}
        src_id = src.get("node")
        dst_id = dst.get("node")
        if not src_id or not dst_id:
            continue
        rf_edges.append(
            {
                "id": f"e{i}-{src_id}-{dst_id}",
                "source": src_id,
                "target": dst_id,
                "animated": True,
                "label": src.get("port", ""),
            }
        )

    return {
        "nodes": rf_nodes,
        "edges": rf_edges,
        "meta": {
            "graph_id": graph.get("id"),
            "schema_version": graph.get("schema_version"),
            "compile_hash": (graph.get("metadata") or {}).get("compile_hash"),
        },
    }


def load_graph_from_compile_dir(compile_dir: Path) -> dict[str, Any]:
    path = compile_dir / "graph.json"
    if not path.is_file():
        raise FileNotFoundError(f"graph.json not found in {compile_dir}")
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> None:
    import argparse

    ap = argparse.ArgumentParser(description="graph-v1 → React Flow JSON")
    ap.add_argument("graph", help="path to graph.json or compile/ directory")
    ap.add_argument("-o", "--output", help="write JSON to file")
    a = ap.parse_args(argv)

    p = Path(a.graph)
    if p.is_dir():
        graph = load_graph_from_compile_dir(p)
    else:
        graph = json.loads(p.read_text(encoding="utf-8"))

    payload = graph_v1_to_reactflow(graph)
    text = json.dumps(payload, indent=2)
    if a.output:
        Path(a.output).write_text(text + "\n", encoding="utf-8")
        print(a.output)
    else:
        print(text)


if __name__ == "__main__":
    main()
