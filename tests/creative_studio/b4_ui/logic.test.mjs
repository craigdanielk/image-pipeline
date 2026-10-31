// Node assertions for the Studio UI's pure modules (compiled from TS by the Python test).
import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";
import { join } from "node:path";

const dir = process.argv[2];
const load = (rel) => import(pathToFileURL(join(dir, rel)).href);
const rc = await load("recipeCommands.js");
const rp = await load("recipeProjection.js");
const geo = await load("composition/geometry.js");

const ref = (kind, n) => ({ authority: "creative-studio", workspace_id: "00000000-0000-4000-8000-000000000001", kind,
  id: `00000000-0000-4000-8000-00000000000${n}`, version_id: `00000000-0000-4000-8000-00000000001${n}`, revision: 1,
  sha256: "a".repeat(64) });
const recipe = {
  schema_version: "cs-recipe-v1", id: ref("recipe", 1).id, version_id: ref("recipe", 1).version_id, revision: 3,
  workspace_id: ref("recipe", 1).workspace_id, created_at: "2026-10-31T00:00:00Z", created_by: ref("x", 2).id,
  sha256: "b".repeat(64), request: ref("creative_request", 3), treatment: ref("treatment", 4), template: null,
  nodes: [
    { id: "hero", kind: "image.generate", config: {}, inputs: [] },
    { id: "layout", kind: "image.compose", config: {}, inputs: [
      { port: "media", ordinal: 0, source: { kind: "node", node_id: "hero", output_port: "image" } }] },
  ],
  outputs: [{ id: "square", format_id: "square", node_id: "layout", output_port: "image" }],
  composition: null,
  authoring_view: { last_view: "guided", positions: [], collapsed_node_ids: [] },
};

// commands + inverses
const added = rc.applyCommands(recipe, [rc.recipeCommands.upsertNode({ id: "extra", kind: "asset.load", config: {}, inputs: [] })]);
assert.equal(added.next.nodes.length, 3);
assert.deepEqual(rc.applyCommands(added.next, added.inverse).next, recipe);
const edited = rc.applyCommands(recipe, [rc.recipeCommands.setComposition(ref("composition", 5)),
  rc.recipeCommands.upsertNode({ ...recipe.nodes[0], config: { prompt: "x" } })]);
assert.deepEqual(rc.applyCommands(edited.next, edited.inverse).next, recipe);
assert.throws(() => rc.applyCommand(recipe, rc.recipeCommands.removeNode("hero")), (e) => e.code === "node_has_dependents");
assert.throws(() => rc.applyCommand(recipe, rc.recipeCommands.removeNode("nope")), (e) => e.code === "unknown_node");
assert.equal(rc.isPresentationOnly([rc.recipeCommands.setAuthoringView(recipe.authoring_view)]), true);
assert.equal(rc.isPresentationOnly([rc.recipeCommands.removeNode("x")]), false);
assert.equal(rc.recipeRef(recipe).sha256, recipe.sha256);

// projections from the same source
const sections = rp.builderSections(recipe);
assert.deepEqual(sections.map((s) => s.id), ["generation", "layout", "outputs"]);
const broken = { ...recipe, outputs: [{ id: "o", format_id: "f", node_id: "gone", output_port: "image" }],
  nodes: [recipe.nodes[1]] };
const issues = rp.builderSections(broken).flatMap((s) => s.issues);
assert.ok(issues.some((i) => i.includes("missing step hero")));
assert.ok(issues.some((i) => i.includes("missing step gone")));
const g = rp.workflowGraph(recipe);
assert.deepEqual(g.edges.map((e) => [e.source, e.target, e.targetPort]), [["hero", "layout", "media"]]);

// geometry mirrors the server's rotation and safe-zone rules
const b = geo.layerBounds({ x: 0, y: 0, width: 100, height: 50, rotation_deg: 90, opacity: 1 });
assert.ok(Math.abs(b.x0 - 25) < 1e-9 && Math.abs(b.y0 + 25) < 1e-9 && Math.abs(b.x1 - 75) < 1e-9 && Math.abs(b.y1 - 75) < 1e-9);
const T = (x, y, w, h) => ({ x, y, width: w, height: h, rotation_deg: 0, opacity: 1 });
const board = { id: "story", width: 1080, height: 1920, color_space: "srgb", background: "#000000", layers: [
  { id: "bg", name: "bg", type: "shape", transform: T(0, 0, 1080, 1920), order: 0, locked: false,
    content: { shape: "rectangle", fill: "#000000", stroke: null, stroke_width_px: 0, corner_radius_px: 0 } },
  { id: "g", name: "CTA", type: "group", transform: T(60, 1700, 400, 100), order: 2, locked: false, content: { child_ids: ["t"] } },
  { id: "t", name: "CTA text", type: "text", transform: T(0, 0, 400, 100), order: 0, locked: false,
    content: { text: "Go", copy_id: "cta", font: {}, style: {} } },
] };
assert.deepEqual(geo.topLevel(board).map((l) => l.id), ["bg", "g"]);
assert.deepEqual(geo.placementIssues(board, null), []);
const zone = { top: 269, right: 0, bottom: 269, left: 0, source: "cited channel spec" };
assert.deepEqual(geo.placementIssues(board, zone), ["CTA text is outside the safe area (cited channel spec)"]);
console.log("ok");
