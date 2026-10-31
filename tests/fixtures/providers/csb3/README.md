# CS-B3 provider fixtures (tests only)

Recorded-shaped, invented values (ENG-03 §4: provider mocks, no network).

- `catalogue-source.fixture.json` — catalogue source with `provenance.kind = fixture`. The builder
  refuses to publish a fixture source unless a test passes `allow_fixture=True`. The credit price
  (50 000 µUSD/credit) is invented; per-entry prices are the adapter's own `generate cost` /
  `dtc-ads --cost-only` quote (see `tests/fixtures/higgsfield/*cost*`) × that price.
- `_provisional/execution-plan.draft.v1.json` — provisional `cs-execution-plan-v1` document built
  only from `docs/creative-studio/contracts/execution-plan-v1.schema.json` (CS-B2 owns plan
  production). Source VersionRefs are invented ids; only the program is executed.
