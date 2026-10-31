"""CS-B3 HTTP routes, registered on the CS-B1 API through its single fenced block.

  POST /api/v1/accounts/{account}/budget-policies            account admin sets a ceiling (Idempotency-Key)
  POST /api/v1/workspaces/{ws}/quotes                        quote a frozen plan or {plan_id} (no reservation)
  POST /api/v1/workspaces/{ws}/runs                          admit (Idempotency-Key) -> 202 + Location
  POST /api/v1/workspaces/{ws}/runs/{run}/cancel             request cancellation
  POST /api/v1/workspaces/{ws}/runs/{run}/nodes/{node}/draft-approvals   reviewer approves a draft output
  GET  /api/v1/workspaces/{ws}/runs/{run}/costs              cost totals + provider-receipt-v1 list (E10 export)
"""
from __future__ import annotations

import uuid

from ..errors import ServiceError
from ..plans import quoting
from . import admission, costs

_UUID = r"[0-9a-fA-F-]{36}"
_SLUG = r"[a-z][a-z0-9_-]{0,63}"


def _uuid(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (ValueError, TypeError):
        raise ServiceError(404, "resource_unavailable") from None


def _int(body: dict, key: str) -> int:
    value = body.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ServiceError(422, "invalid_budget_policy", f"{key} must be a non-negative integer (USD micros)")
    return value


def register(api_cls, route) -> None:
    @route("POST", rf"/api/v1/accounts/({_UUID})/budget-policies")
    def cs_b3_set_budget_policy(self, req, account_id):
        account_id = _uuid(account_id)
        key, digest = req.require_idempotency()
        body = req.json()
        ws = body.get("workspace_id")
        if body.get("funding_source") not in ("workspace", "platform"):
            raise ServiceError(422, "invalid_budget_policy", "funding_source must be workspace or platform")
        _, tx = self._tx(req)
        with tx as cur:
            cur.execute("""select * from public.cs_set_budget_policy(%s, %s, %s::public.cs_credential_mode, %s, %s, %s,
                                                                   %s, %s, %s)""",
                        (account_id, _uuid(ws) if ws else None, body["funding_source"], _int(body, "window_seconds"),
                         _int(body, "ceiling_micros"), _int(body, "per_run_ceiling_micros"), body.get("expires_at"),
                         key, digest))
            row = cur.fetchone()
        return (200 if row["replayed"] else 201), row

    @route("POST", rf"/api/v1/workspaces/({_UUID})/quotes")
    def cs_b3_quote(self, req, workspace_id):
        workspace_id = _uuid(workspace_id)
        _, tx = self._tx(req)
        body = req.json()
        with tx as cur:
            if "plan_id" in body:      # CS-16: quote a plan CS compiled (no caller-held plan)
                return 200, quoting.quote_plan(cur, self.settings, workspace_id, body, admission.quote)
            return 200, admission.quote(cur, workspace_id, body)

    @route("POST", rf"/api/v1/workspaces/({_UUID})/runs")
    def cs_b3_admit_run(self, req, workspace_id):
        workspace_id = _uuid(workspace_id)
        key, digest = req.require_idempotency()
        _, tx = self._tx(req)
        body = req.json()
        with tx as cur:
            if "plan_id" in body:      # CS-16: admit only a quoted, current, CS-compiled plan
                body = quoting.plan_for_admission(cur, self.settings, workspace_id, body)
            row = admission.admit(cur, self.settings, workspace_id, body, key, digest)
        row["location"] = f"/api/v1/workspaces/{workspace_id}/runs/{row['run_id']}"
        return (200 if row["replayed"] else 202), row

    @route("POST", rf"/api/v1/workspaces/({_UUID})/runs/({_UUID})/cancel")
    def cs_b3_cancel_run(self, req, workspace_id, run_id):
        workspace_id, run_id = _uuid(workspace_id), _uuid(run_id)
        reason = req.json().get("reason")
        _, tx = self._tx(req)
        with tx as cur:
            cur.execute("select public.cs_request_cancel(%s, %s, %s)::text as state",
                        (workspace_id, run_id, reason if isinstance(reason, str) else None))
            return 202, {"run_id": run_id, "state": cur.fetchone()["state"]}

    @route("POST", rf"/api/v1/workspaces/({_UUID})/runs/({_UUID})/nodes/({_SLUG})/draft-approvals")
    def cs_b3_approve_draft(self, req, workspace_id, run_id, node_id):
        workspace_id, run_id = _uuid(workspace_id), _uuid(run_id)
        body = req.json()
        _, tx = self._tx(req)
        with tx as cur:
            cur.execute("select public.cs_approve_draft(%s, %s, %s, %s, %s) as approval_id",
                        (workspace_id, run_id, node_id, _uuid(body.get("asset_version_id") or ""),
                         body.get("rationale") if isinstance(body.get("rationale"), str) else None))
            return 201, {"approval_id": cur.fetchone()["approval_id"]}

    @route("GET", rf"/api/v1/workspaces/({_UUID})/runs/({_UUID})/costs")
    def cs_b3_run_costs(self, req, workspace_id, run_id):
        workspace_id, run_id = _uuid(workspace_id), _uuid(run_id)
        _, tx = self._tx(req)
        with tx as cur:
            return 200, costs.run_costs(cur, workspace_id, run_id)

    for name, fn in list(locals().items()):
        if name.startswith("cs_b3_"):
            setattr(api_cls, name, fn)
