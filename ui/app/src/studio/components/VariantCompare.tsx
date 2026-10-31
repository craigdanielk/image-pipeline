// Side-by-side comparison of two exact asset versions with synchronized zoom.
import { useState } from "react";
import type { AssetRow } from "../StudioApi";
import { useAssetUrl } from "./useAssetUrl";

function Pane({ workspaceId, asset, zoom }: { workspaceId: string; asset: AssetRow; zoom: number }) {
  const ref =
    asset.latest_version_id && asset.sha256
      ? { asset_id: asset.id, asset_version_id: asset.latest_version_id, sha256: asset.sha256 }
      : null;
  const { url, failed } = useAssetUrl(workspaceId, ref);
  return (
    <figure className="cs-compare-pane">
      <div className="cs-compare-frame">
        {url ? (
          <img src={url} alt={asset.label} style={{ transform: `scale(${zoom})` }} />
        ) : (
          <span>{failed ? "File unavailable" : "Loading…"}</span>
        )}
      </div>
      <figcaption>
        {asset.label} · v{asset.revision ?? "?"} · <code>{asset.sha256?.slice(0, 12)}</code>
      </figcaption>
    </figure>
  );
}

export function VariantCompare({ workspaceId, left, right }: { workspaceId: string; left: AssetRow; right: AssetRow }) {
  const [zoom, setZoom] = useState(1);
  return (
    <section className="cs-compare" aria-label="Compare versions">
      <label>
        Zoom{" "}
        <input type="range" min={1} max={4} step={0.25} value={zoom} onChange={(e) => setZoom(Number(e.target.value))} />
      </label>
      <div className="cs-compare-grid">
        <Pane workspaceId={workspaceId} asset={left} zoom={zoom} />
        <Pane workspaceId={workspaceId} asset={right} zoom={zoom} />
      </div>
    </section>
  );
}
