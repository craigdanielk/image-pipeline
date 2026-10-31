// Shared pure layout arithmetic for editing controls and preview guides. The
// server renderer independently enforces every rule; this never decides export.
import type { Composition } from "../contracts/types.gen";

export type Artboard = Composition["artboards"][number];
export type Layer = Artboard["layers"][number];
export type Transform = Layer["transform"];
export type Rect = { x0: number; y0: number; x1: number; y1: number };
export type SafeZoneInsets = { top: number; right: number; bottom: number; left: number; source: string };

export function rotatePoint(px: number, py: number, cx: number, cy: number, deg: number): [number, number] {
  const r = (deg * Math.PI) / 180; // clockwise in screen space (y down)
  const dx = px - cx;
  const dy = py - cy;
  return [cx + dx * Math.cos(r) - dy * Math.sin(r), cy + dx * Math.sin(r) + dy * Math.cos(r)];
}

export function layerBounds(t: Transform, ox = 0, oy = 0): Rect {
  const x = ox + t.x;
  const y = oy + t.y;
  const cx = x + t.width / 2;
  const cy = y + t.height / 2;
  const pts: [number, number][] = [
    [x, y],
    [x + t.width, y],
    [x, y + t.height],
    [x + t.width, y + t.height],
  ].map(([px, py]) => (t.rotation_deg ? rotatePoint(px, py, cx, cy, t.rotation_deg) : [px, py]));
  return {
    x0: Math.min(...pts.map((p) => p[0])),
    y0: Math.min(...pts.map((p) => p[1])),
    x1: Math.max(...pts.map((p) => p[0])),
    y1: Math.max(...pts.map((p) => p[1])),
  };
}

export function safeRect(board: Artboard, zone: SafeZoneInsets | null): Rect {
  const z = zone ?? { top: 0, right: 0, bottom: 0, left: 0, source: "artboard-bounds-only" };
  return { x0: z.left, y0: z.top, x1: board.width - z.right, y1: board.height - z.bottom };
}

export function inside(r: Rect, outer: Rect): boolean {
  return r.x0 >= outer.x0 && r.y0 >= outer.y0 && r.x1 <= outer.x1 && r.y1 <= outer.y1;
}

/** Top-level layers (not referenced by any group), drawing order ascending (order, id). */
export function topLevel(board: Artboard): Layer[] {
  const children = new Set(board.layers.flatMap((l) => (l.type === "group" ? l.content.child_ids : [])));
  return sortLayers(board.layers.filter((l) => !children.has(l.id)));
}

export function sortLayers(layers: Layer[]): Layer[] {
  return [...layers].sort((a, b) => a.order - b.order || a.id.localeCompare(b.id));
}

export function childrenOf(board: Artboard, group: Layer): Layer[] {
  if (group.type !== "group") return [];
  const ids = new Set(group.content.child_ids);
  return sortLayers(board.layers.filter((l) => ids.has(l.id)));
}

/** Copy/logo layers outside the safe area (preview guidance; the server refuses export). */
export function placementIssues(board: Artboard, zone: SafeZoneInsets | null): string[] {
  const issues: string[] = [];
  const safe = safeRect(board, zone);
  const art: Rect = { x0: 0, y0: 0, x1: board.width, y1: board.height };
  const walk = (layers: Layer[], ox: number, oy: number) => {
    for (const l of layers) {
      if (l.type === "group") {
        walk(childrenOf(board, l), ox + l.transform.x, oy + l.transform.y);
        continue;
      }
      if (l.type !== "text" && l.type !== "logo") continue;
      const b = layerBounds(l.transform, ox, oy);
      if (!inside(b, art)) issues.push(`${l.name} extends outside the artboard`);
      else if (!inside(b, safe)) issues.push(`${l.name} is outside the safe area (${zone?.source ?? "artboard"})`);
    }
  };
  walk(topLevel(board), 0, 0);
  return issues;
}
