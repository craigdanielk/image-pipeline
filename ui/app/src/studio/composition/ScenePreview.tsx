// Declarative browser preview of one artboard. It is NOT the export renderer:
// fonts are the browser's until the server render resolves the pinned font
// bytes, and crops are approximate. The saved server render is the export.
import type { AssetRef } from "../contracts/types.gen";
import { useAssetUrl } from "../components/useAssetUrl";
import { childrenOf, safeRect, topLevel, type Artboard, type Layer, type SafeZoneInsets } from "./geometry";

type Props = {
  workspaceId: string;
  board: Artboard;
  selectedId: string | null;
  onSelect: (id: string) => void;
  zone: SafeZoneInsets | null;
};

function AssetImage({ workspaceId, asset, w, h, fit }: { workspaceId: string; asset: AssetRef; w: number; h: number; fit: string }) {
  const { url, failed } = useAssetUrl(workspaceId, asset);
  if (!url) {
    return (
      <g>
        <rect width={w} height={h} fill="#8884" stroke="#888" strokeDasharray="8 6" />
        <text x={12} y={32} fontSize={24} fill="#fff">
          {failed ? "File unavailable" : "Loading…"}
        </text>
      </g>
    );
  }
  const par = fit === "cover" ? "xMidYMid slice" : fit === "native" ? "xMinYMin slice" : "xMidYMid meet";
  return <image href={url} width={w} height={h} preserveAspectRatio={par} />;
}

function LayerNode({ workspaceId, board, layer, selectedId, onSelect }: { workspaceId: string; board: Artboard; layer: Layer; selectedId: string | null; onSelect: (id: string) => void }) {
  const t = layer.transform;
  const transform = `translate(${t.x} ${t.y}) rotate(${t.rotation_deg} ${t.width / 2} ${t.height / 2})`;
  let body = null;
  switch (layer.type) {
    case "image":
    case "logo":
      body = <AssetImage workspaceId={workspaceId} asset={layer.content.asset} w={t.width} h={t.height} fit={layer.content.fit} />;
      break;
    case "shape": {
      const c = layer.content;
      const stroke = c.stroke ?? undefined;
      body =
        c.shape === "ellipse" ? (
          <ellipse cx={t.width / 2} cy={t.height / 2} rx={t.width / 2} ry={t.height / 2} fill={c.fill} stroke={stroke} strokeWidth={c.stroke_width_px} />
        ) : c.shape === "line" ? (
          <line x1={0} y1={t.height / 2} x2={t.width} y2={t.height / 2} stroke={c.stroke ?? c.fill} strokeWidth={c.stroke_width_px || t.height} />
        ) : (
          <rect width={t.width} height={t.height} rx={c.corner_radius_px} fill={c.fill} stroke={stroke} strokeWidth={c.stroke_width_px} />
        );
      break;
    }
    case "text": {
      const s = layer.content.style;
      body = (
        <foreignObject width={t.width} height={t.height}>
          <div
            className="cs-preview-text"
            style={{
              fontSize: s.font_size_px,
              lineHeight: s.line_height,
              letterSpacing: s.letter_spacing_px,
              color: s.fill,
              textAlign: s.align,
              justifyContent: s.vertical_align === "top" ? "flex-start" : s.vertical_align === "bottom" ? "flex-end" : "center",
              fontWeight: s.weight,
            }}
          >
            {layer.content.text}
          </div>
        </foreignObject>
      );
      break;
    }
    case "group":
      body = childrenOf(board, layer).map((child) => (
        <LayerNode key={child.id} workspaceId={workspaceId} board={board} layer={child} selectedId={selectedId} onSelect={onSelect} />
      ));
      break;
  }
  return (
    <g
      transform={transform}
      opacity={t.opacity}
      onClick={(e) => {
        e.stopPropagation();
        onSelect(layer.id);
      }}
    >
      {body}
      {selectedId === layer.id ? (
        <rect width={t.width} height={t.height} fill="none" stroke="#3b82f6" strokeWidth={4} strokeDasharray="12 8" />
      ) : null}
    </g>
  );
}

export function ScenePreview({ workspaceId, board, selectedId, onSelect, zone }: Props) {
  const safe = safeRect(board, zone);
  return (
    <svg
      className="cs-scene"
      viewBox={`0 0 ${board.width} ${board.height}`}
      role="img"
      aria-label={`Preview of ${board.id} (${board.width}×${board.height})`}
    >
      <rect width={board.width} height={board.height} fill={board.background} />
      {topLevel(board).map((layer) => (
        <LayerNode key={layer.id} workspaceId={workspaceId} board={board} layer={layer} selectedId={selectedId} onSelect={onSelect} />
      ))}
      {zone ? (
        <rect x={safe.x0} y={safe.y0} width={safe.x1 - safe.x0} height={safe.y1 - safe.y0} fill="none" stroke="#f59e0b" strokeWidth={3} strokeDasharray="16 10" pointerEvents="none" />
      ) : null}
    </svg>
  );
}
