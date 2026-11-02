# CS-12 enqueue -> work-started latency (real PG17, stubbed pg_net delivery)

| path | n | p50 | p95 | max |
|---|---|---|---|---|
| kick on (trigger -> http_post -> worker) | 60 | 19 ms | 24 ms | 27 ms |
| cron-only, sweeper every 2s (scaled) | 25 | 1080 ms | 1896 ms | 1921 ms |

Cron-only at the production safety-net cadence (120 s, uniform arrival phase): expected p50 = 60 s, p95 = 114 s, worst case 120 s + grace (analytic, not measured).
Kick path excludes real network/cold-start time of the HTTP hop (stubbed delivery, local DB).
