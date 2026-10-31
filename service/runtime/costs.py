"""Per-run cost totals and provider receipts exported as provider-receipt-v1
(the MOS E10 ledger consumes these; reconcile on billing_reference)."""
from __future__ import annotations

from datetime import timezone

from ..providers.canonical import artifact_digest
from ..providers.contracts import validate

RECEIPT_SCHEMA = "provider-receipt-v1.schema.json"


def _ts(value) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def receipt_document(row: dict, output_assets: list) -> dict:
    doc = {
        "schema_version": "cs-provider-receipt-v1", "id": str(row["id"]), "workspace_id": str(row["workspace_id"]),
        "run_id": str(row["run_id"]), "node_id": row["node_id"], "attempt_id": str(row["attempt_id"]),
        "observed_at": _ts(row["observed_at"]), "sha256": "", "provider": row["provider"],
        "provider_account_binding_id": str(row["credential_version_id"]),
        "provider_request_id": row["provider_request_id"], "kind": row["kind"], "status": row["status"],
        "provider_event_id": None, "dispatch_envelope_sha256": row["dispatch_envelope_sha256"],
        "raw_receipt_sha256": row["raw_receipt_sha256"],
        "output_assets": output_assets if row["kind"] == "output" else [],
        "charge": {"currency": "USD", "amount_micros": row["charge_amount_micros"],
                   "evidence": row["charge_evidence"], "billing_reference": row["billing_reference"]},
        "signature_verified": row["signature_verified"], "source": row["source"],
        "actor_id": str(row["actor_id"]) if row["actor_id"] else None, "note": row["note"],
        "operation_id": None, "operation_step_key": None,
    }
    doc["sha256"] = artifact_digest(doc)
    validate(RECEIPT_SCHEMA, doc)
    return doc


def run_costs(cur, workspace_id: str, run_id: str) -> dict:
    """cs_api transaction: authority is checked in SQL (definer reads re-check current membership)."""
    cur.execute("select public.cs_get_run_cost(%s, %s) as cost", (workspace_id, run_id))
    cost = cur.fetchone()["cost"]
    cur.execute("select * from public.cs_list_run_receipts(%s, %s)", (workspace_id, run_id))
    receipts, ledger_lines = [], []
    for row in cur.fetchall():
        outputs = row["output_refs"] if row["attempt_state"] == "succeeded" else []
        doc = receipt_document(row, [dict(r, revision=r.get("revision", 1)) for r in outputs])
        receipts.append(doc)
        if row["kind"] in ("billing", "reconciliation"):
            ledger_lines.append({"receipt_id": doc["id"], "billing_reference": row["billing_reference"],
                                 "amount_micros": row["charge_amount_micros"], "evidence": row["charge_evidence"],
                                 "certainty": row["charge_certainty"],
                                 "provider_units": None if row["provider_units"] is None else str(row["provider_units"]),
                                 "provider_unit_name": "credits" if row["provider_units"] is not None else None})
    return {"run_id": run_id, "cost": cost, "receipts": receipts, "ledger_lines": ledger_lines}
