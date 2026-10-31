# CS-12 enqueue -> work-started latency (real PG17, stubbed pg_net delivery)

| path | n | p50 | p95 | max |
|---|---|---|---|---|
| kick on (trigger -> http_post -> worker) | 60 | 13 ms | 34 ms | 35 ms |
| cron-only, sweeper every 2s (scaled) | 25 | 1074 ms | 1913 ms | 1914 ms |

Cron-only at the production safety-net cadence (120 s, uniform arrival phase): expected p50 = 60 s, p95 = 114 s, worst case 120 s + grace (analytic, not measured).
Kick path excludes real network/cold-start time of the HTTP hop (stubbed delivery, local DB).
