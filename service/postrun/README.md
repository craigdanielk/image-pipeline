# service/postrun — hosted compose → export → MOS handoff (CS-17b)

| Piece | Role |
|---|---|
| `cs_complete_ingest` (migration 230100) | provider output persisted ⇒ queues `post.compose/export/handoff.<canvas>` nodes + the first work item, idempotent per (run, canvas). Post nodes never gate the provider run (`cs_advance_run` ignores them). |
| `layout.py` | deterministic layout (scene, scrim, exact brand logo, exact-copy text layers, CTA) inside the canvas safe zone |
| `runtime.py` | `compose_image` → `publish_export` → `deliver_handoff`; typed states; no provider call, no reservation |
| `routes.py` | `POST …/exports/{v}/download-ticket` (MOS pull), `POST …/handoffs/marketing-os/{id}/receipt` (MOS receipt, gateway-only) |
| `cs_post_run_state` | the visible per-canvas state (`queued, composed, compose_blocked, exported, export_blocked, handoff_offered, handoff_delivered, handoff_blocked, handoff_failed, handoff_not_applicable`) |

Run status (`GET …/runs/{id}`) adds `post_run`, `handoff` (exact reference MOS pulls by `version_id`), `actual_cost`
(`certainty: settled|unknown`, micros only when settled), `actual_usd`, `charge`.

Delivery truth: `handoff_delivered` only with a verified `cs-mos-ingestion-receipt-v1` (pushed reply or the receipt route).
`handoff_offered` = queued and pullable, no receipt yet. Retries: 3 bounded attempts with backoff, then `handoff_failed`.

CS → MOS push assertion (same ENG-15 format, audience `marketing-os`): `{aud, sub = run requester, act = handoff.deliver,
bnd = MOS workspace, bsha = sha256(raw body), nonce, iat, exp ≤ 60 s}` in `X-CS-Service-Assertion`, plus the service token.
Interpretations: first headline / first body / first CTA and every disclaimer are laid out; other copy is listed in the
compose detail (`copy_not_rendered`). Generated-image rights are recorded with basis `provider_terms` (operator confirms).
