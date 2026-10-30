# Higgsfield CLI fixtures (engine_higgsfield.py tests)

Recorded-shaped, not recorded. Command surfaces and flags are from `higgsfield … --help` (CLI 1.1.26,
2026-10-30). JSON field names that the help text does not show are assumptions, each confined to one
constant in the adapter:

| Fixture | Assumed field | Adapter constant |
|---|---|---|
| `*.charged.response.json` | `credits_charged` on the job JSON | `_JOB_CHARGE_KEYS` |
| `account.status.*.json` | `credits` = available balance | `_BALANCE_KEYS` |
| `dtc_ads.cost.*.json`, `generate.cost.response.json` | `cost.credits` / `credits` | `_credits_from_cost` |
| `catalogue.fixture.json` | ad-format uuids are invented | operator snapshot of `marketing-studio ad-formats list --json` |

Replace with a credentialed, no-spend operator snapshot before go-live.
