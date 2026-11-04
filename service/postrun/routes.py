"""CS-17b routes.

  POST /api/v1/workspaces/{W}/exports/{version}/download-ticket   short-lived signed URL for the exported file
        body {expected_sha256, disposition}; the file hash must equal expected_sha256 (MOS pulls with this).
  POST /api/v1/workspaces/{W}/handoffs/marketing-os/{id}/receipt  MOS's cs-mos-ingestion-receipt-v1 for a handoff it
        pulled; verified against the exact offered bytes/versions before the handoff is marked `received`.
Both use the user's bearer token; the receipt route is gateway-only (signed assertion action `handoff.receipt`).
"""
from __future__ import annotations

from ..brand.routes import W, _UUID
from ..composition.errors import CompositionError
from ..composition.handoff import verify_ingestion_receipt
from ..errors import ServiceError



def download_token(settings, req) -> str:
    """Who signs the storage URL. A user call signs with the user's own session. A verified Marketing OS server
    callback has no session: the download grant above was already created in SQL as the user the assertion names,
    so the URL is signed by the runtime principal that writes these objects (Storage RLS admits it)."""
    if getattr(req, "_service_actor", None) is not None and not req.bearer_token():
        from ..storage import worker_token_provider
        return worker_token_provider(settings)()
    return req.bearer_token()

def register(api_cls, route):
    from service.api import _uuid

    @route("POST", W + rf"/exports/({_UUID})/download-ticket")
    def cs17b_export_download_ticket(self, req, workspace_id, version_id):
        workspace_id, version_id = _uuid(workspace_id), _uuid(version_id)
        body = req.json()
        expected = body.get("expected_sha256")
        _, tx = self._tx(req)
        with tx as cur:
            cur.execute("select public.cs_get_export(%s, %s, null) as manifest", (workspace_id, version_id))
            ref = cur.fetchone()["manifest"]["file"]["ref"]
            if expected is not None and expected != ref["sha256"]:
                raise ServiceError(409, "asset_hash_mismatch", "expected_sha256 is not the exported file's hash")
            cur.execute("select * from public.cs_create_download_grant(%s, %s, %s, %s, %s)",
                        (workspace_id, ref["asset_id"], ref["asset_version_id"], ref["sha256"],
                         self.settings.download_grant_seconds))
            grant = cur.fetchone()
        url = self.signer.sign_download(download_token(self.settings, req), grant["storage_key"],
                                        self.settings.signed_download_seconds)
        return 200, {"url": url, "download_url": url, "expires_in": self.settings.signed_download_seconds,
                     "sha256": grant["sha256"], "mime_type": grant["mime_type"], "size_bytes": grant["size_bytes"]}

    @route("POST", W + rf"/handoffs/marketing-os/({_UUID})/receipt")
    def cs17b_record_handoff_receipt(self, req, workspace_id, handoff_id):
        workspace_id, handoff_id = _uuid(workspace_id), _uuid(handoff_id)
        receipt = req.json()
        if isinstance(receipt.get("receipt"), dict) and "schema_version" not in receipt:
            receipt = receipt["receipt"]
        _, tx = self._tx(req)
        with tx as cur:
            cur.execute("select * from public.cs_get_handoff(%s, %s)", (workspace_id, handoff_id))
            stored = cur.fetchone()
            handoff = stored["payload"]
            exports = []
            for ref in handoff["exports"]:
                cur.execute("select public.cs_get_export(%s, %s, %s) as manifest",
                            (workspace_id, ref["version_id"], ref["sha256"]))
                exports.append(cur.fetchone()["manifest"])
            try:
                verify_ingestion_receipt(receipt, handoff, exports)
            except CompositionError as err:
                raise ServiceError(err.http_status, err.code, err.message) from None
            cur.execute("select * from public.cs_record_handoff_receipt(%s, %s, %s::jsonb)",
                        (workspace_id, handoff["id"], __import__("json").dumps(receipt)))
            row = cur.fetchone()
        return (200 if row["replayed"] else 201), {"data": {"handoff_id": handoff["id"], "state": row["state"]}}

    for fn in (cs17b_export_download_ticket, cs17b_record_handoff_receipt):
        setattr(api_cls, fn.__name__, fn)
