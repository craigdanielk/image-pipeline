-- Gap found wiring core/run_job.py to the DB (2026-07-03): job_outputs had no
-- field for the engine's own per-image response (Higgsfield job id, raw status
-- payload) — needed to trace a stored image back to Higgsfield's own dashboard
-- when debugging a batch, and to record which candidates in a partial-failure
-- batch actually completed (see core/tracking.py note on the batch bug in
-- engine_higgsfield.py — this column is what lets that bug's damage be audited
-- even though the adapter itself isn't fixed yet).

alter table job_outputs
  add column if not exists engine_response jsonb not null default '{}';
