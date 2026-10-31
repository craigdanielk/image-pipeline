-- Creative Studio worker dispatch: REPLACEMENT for the per-minute poller. NOT applied by CS-12;
-- the operator runs this after the first live use-time loop works (DEPLOY_RUNBOOK.md, "Use-time kick").
-- Requires: migration 20261031190100_cs_12_use_time_kick.sql, pg_cron.
--
-- Work normally starts at enqueue (trigger -> net.http_post). This job is only the safety net:
-- it re-kicks due work nobody claimed within worker_sweep_grace_seconds and expired non-paid leases.
-- Cadence = cs_platform_settings.worker_safety_net_seconds (default 120). pg_cron schedules at
-- minute granularity here, so the value is rounded UP to whole minutes.
--
-- Pause:   select cron.unschedule('cs-worker-drain');
-- Re-time: update cs_private.cs_platform_settings set value = '300' where key = 'worker_safety_net_seconds';
--          then re-run this file.
do $$
declare
  v_secs integer := cs_private.cs_setting('worker_safety_net_seconds', '120')::integer;
  v_expr text := format('*/%s * * * *', least(59, greatest(1, ceil(v_secs / 60.0)::integer)));
begin
  perform cron.unschedule(j.jobid) from cron.job j where j.jobname = 'cs-worker-drain';
  perform cron.schedule('cs-worker-drain', v_expr, 'select cs_private.cs_safety_net_sweep()');
end $$;
