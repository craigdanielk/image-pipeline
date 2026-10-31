// Builder sections and workflow graph derived from ONE saved recipe. Both views
// project the same source; neither owns separate state.
import type { Recipe } from "./contracts/types.gen";
import type { RecipeNode } from "./recipeCommands";

export type BuilderSection = {
  id: "sources" | "generation" | "layout" | "outputs" | "custom";
  title: string;
  nodeIds: string[];
  issues: string[];
};

export type GraphNode = { id: string; kind: RecipeNode["kind"]; x: number; y: number; collapsed: boolean };
export type GraphEdge = { id: string; source: string; sourcePort: string; target: string; targetPort: string; ordinal: number };

const SECTION_OF: Record<RecipeNode["kind"], BuilderSection["id"]> = {
  "asset.load": "sources",
  "image.generate": "generation",
  "video.generate": "generation",
  "image.edit": "generation",
  "image.transform": "layout",
  "image.compose": "layout",
  "video.assemble": "layout",
  "export.render": "outputs",
};

const TITLES: Record<BuilderSection["id"], string> = {
  sources: "Brand & sources",
  generation: "Outputs & direction",
  layout: "Layout & assembly",
  outputs: "Review & create",
  custom: "Custom steps",
};

export function builderSections(recipe: Recipe): BuilderSection[] {
  const sections = new Map<BuilderSection["id"], BuilderSection>();
  for (const id of Object.keys(TITLES) as BuilderSection["id"][]) {
    sections.set(id, { id, title: TITLES[id], nodeIds: [], issues: [] });
  }
  const ids = new Set(recipe.nodes.map((n) => n.id));
  for (const node of [...recipe.nodes].sort((a, b) => a.id.localeCompare(b.id))) {
    const section = sections.get(SECTION_OF[node.kind] ?? "custom")!;
    section.nodeIds.push(node.id);
    for (const input of node.inputs) {
      if (input.source.kind === "node" && !ids.has(input.source.node_id)) {
        section.issues.push(`${node.id}.${input.port} is connected to missing step ${input.source.node_id}`);
      }
    }
  }
  const outputs = sections.get("outputs")!;
  for (const out of recipe.outputs) {
    if (!ids.has(out.node_id)) outputs.issues.push(`Output ${out.id} points at missing step ${out.node_id}`);
  }
  if (!recipe.outputs.length) outputs.issues.push("No output selected");
  return [...sections.values()].filter((s) => s.nodeIds.length || s.issues.length || s.id === "outputs");
}

export function workflowGraph(recipe: Recipe): { nodes: GraphNode[]; edges: GraphEdge[] } {
  const pos = new Map(recipe.authoring_view.positions.map((p) => [p.node_id, p]));
  const collapsed = new Set(recipe.authoring_view.collapsed_node_ids);
  const nodes = [...recipe.nodes]
    .sort((a, b) => a.id.localeCompare(b.id))
    .map((n, i) => ({
      id: n.id,
      kind: n.kind,
      x: pos.get(n.id)?.x ?? i * 240,
      y: pos.get(n.id)?.y ?? 0,
      collapsed: collapsed.has(n.id),
    }));
  const edges: GraphEdge[] = [];
  for (const n of recipe.nodes) {
    for (const input of n.inputs) {
      if (input.source.kind === "node") {
        edges.push({
          id: `${input.source.node_id}:${input.source.output_port}->${n.id}:${input.port}:${input.ordinal}`,
          source: input.source.node_id,
          sourcePort: input.source.output_port,
          target: n.id,
          targetPort: input.port,
          ordinal: input.ordinal,
        });
      }
    }
  }
  edges.sort((a, b) => a.id.localeCompare(b.id));
  return { nodes, edges };
}
