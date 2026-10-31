# CS-B3 runtime — provider execution, admission, receipts, cost (NS-10805 / PT-9641)

Migration: `supabase/migrations/20261031140300_cs_b3_provider_execution.sql` (not applied anywhere).

| Step | Where | Rule |
|---|---|---|
| Catalogue | `service/providers/catalogue.py` → `cs_publish_catalogue_entry` (operator only) | price = adapter cost quote (`generate cost` / `dtc-ads --cost-only`) × cited credit price; unreadable quote ⇒ not published ⇒ `price_unknown` |
| Admission | `POST /api/v1/workspaces/{ws}/runs` → `cs_admit_run` | plan schema + compile hash + sha256 recomputed; account budget lock; per-run + rolling-window ceiling (unknown holds count in full); credential for the policy's funding source; final tier needs an approved draft used as an input. Refusal ⇒ no rows |
| Dispatch | `service/runtime/worker.py` `provider_submit` | prepare attempt (immutable) → references/credential (retryable) → `cs_mark_dispatching` (frozen envelope sealed AES-256-GCM at rest, bound to workspace/attempt/hash; cancel/eligibility/credential re-check) → one adapter call → `cs_record_provider_outcome` |
| Money | `cs_settle` / `cs_mark_unknown` / `cs_release_unspent` | provider_reported / balance_delta ⇒ settle (+release remainder, overrun ⇒ billing exception); estimated / unknown ⇒ reservation stays held (`unknown`) until `cs_reconcile_attempt` (operator, actor + rationale) |
| Ingest | `ingest_original` | allowed origins only, magic-byte media check, exact asset key, immutable versions, node succeeded, DAG advance |
| Recovery | `cs_recover_expired_leases` (every invocation) | expired paid lease: no dispatch marker ⇒ requeue; marker without outcome ⇒ `uncertain` + `needs_reconciliation`; never resubmitted |
| Export | `GET …/runs/{run}/costs` | `cost` (common `cost` shape) + `provider-receipt-v1` receipts + `ledger_lines` for the MOS E10 ledger (reconcile on `billing_reference`) |

Hosted wiring (CS-B5): serve `service.runtime.entry.make_runtime_wsgi(config.load, store_factory)` from
`api/worker.py`, set `CS_CREDENTIAL_KEYS`, and supply a Storage object store bound to the runtime principal.
