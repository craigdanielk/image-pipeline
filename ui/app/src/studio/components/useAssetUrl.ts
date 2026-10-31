// Resolve an exact AssetRef to a short-lived signed URL (download ticket).
// The URL is transport only; identity stays the AssetRef + sha256.
import { useEffect, useState } from "react";
import type { AssetRef } from "../contracts/types.gen";
import { StudioApi } from "../StudioApi";

export function useAssetUrl(workspaceId: string, ref: Pick<AssetRef, "asset_id" | "asset_version_id" | "sha256"> | null) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  const key = ref ? `${ref.asset_id}/${ref.asset_version_id}/${ref.sha256}` : null;
  useEffect(() => {
    setUrl(null);
    setFailed(false);
    if (!ref) return;
    let live = true;
    StudioApi.downloadTicket(workspaceId, ref.asset_id, ref.asset_version_id, ref.sha256)
      .then((t) => live && setUrl(t.url))
      .catch(() => live && setFailed(true));
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId, key]);
  return { url, failed };
}
