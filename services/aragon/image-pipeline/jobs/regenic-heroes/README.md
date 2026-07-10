# regenic-heroes — Norwood-Ludwig template-hero job set (T2–T10)

Runnable job-v1 packaging of the regenic template-hero recipe corpus.
Source of truth per template: `config/cad/T<N>_hero.json` (geometry, calibration,
metafield matrix). Each config here carries that template's **example SKU**
metafields; swap `subject` values per SKU at run time.

| Config | Template | Container | Example SKU |
|--------|----------|-----------|-------------|
| `t2-hero.job.json` | T2 | HDPE capsule bottle | Minoxidil + Biotin |
| `t3-hero.job.json` | T3 | Amber spray/solution bottle | Minoxidil 2% |
| `t4-hero.job.json` | T4 | Foam canister | Finasteride 0.1% + Minoxidil 5% |
| `t5-hero.job.json` | T5 | Peptide-pen carry case (closed) | Tirzepatide 5MG/2ML |
| `t6-hero.job.json` | T6 | Glass multidose vial | BPC-157 + TB-500 |
| `t7-hero.job.json` | T7 | Open cartridge-pen kit | BPC-157 |
| `t8-hero.job.json` | T8 | Blister syringe | Tirzepatide 2.5MG |
| `t9-hero.job.json` | T9 | AVID-RA airless pump jar | GHK-Cu PLUS |
| `t10-hero.job.json` | T10 | AVID-RA amber dropper bottle | GHK-Cu Serum Plus |

## Run

```bash
python3 core/run_job.py jobs/regenic-heroes/t2-hero.job.json --dry-run   # cost estimate
python3 core/run_job.py jobs/regenic-heroes/t2-hero.job.json            # generate
```

## References

Each config points at `reference/t<N>-target.png` with `optional: true` —
dry-run passes without it; for on-model production runs, drop the canonical
target render (Drive canon, see the `_doc` field of each cad spec) into
`jobs/regenic-heroes/reference/` first. Without the reference the engine runs
text-to-image from the CAD prompt alone.

**Publish guard:** these jobs only produce image files. Nothing here touches
the regenic live site; publishing to Lovable/live is a human (Craig) action.
