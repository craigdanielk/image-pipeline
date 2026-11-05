-- Admission refused a workspace's third concurrent run (active_run_limit 2), so a MOS plan of 20 draft posts failed
-- 17 of them with active_run_limit_reached (2026-11-05, Regenic). Provider concurrency is governed separately by
-- heavy_slots / max_dispatch_batch and spend by budget reservations at admission; admission only needs to bound the
-- queue. 40 lets one plan's drafts queue and dispatch in turn.
update cs_private.cs_runtime_limits
   set active_run_limit = 40, revision = revision + 1, updated_at = now()
 where environment_id = 'production' and active_run_limit < 40;
