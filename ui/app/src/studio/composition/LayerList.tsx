import type { ReactElement } from "react";
import { childrenOf, topLevel, type Artboard, type Layer } from "./geometry";

export function LayerList({ board, selectedId, onSelect }: { board: Artboard; selectedId: string | null; onSelect: (id: string) => void }) {
  const row = (layer: Layer, depth: number): ReactElement[] => [
    <li key={layer.id}>
      <button
        type="button"
        className={selectedId === layer.id ? "cs-layer cs-layer-selected" : "cs-layer"}
        style={{ paddingLeft: 8 + depth * 16 }}
        aria-pressed={selectedId === layer.id}
        onClick={() => onSelect(layer.id)}
      >
        <span className="cs-layer-type">{layer.type}</span> {layer.name}
        {layer.locked ? " 🔒" : ""}
      </button>
    </li>,
    ...childrenOf(board, layer).flatMap((c) => row(c, depth + 1)),
  ];
  // top of the list = drawn last
  return (
    <ol className="cs-layer-list" aria-label="Layers">
      {[...topLevel(board)].reverse().flatMap((l) => row(l, 0))}
    </ol>
  );
}
