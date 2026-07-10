# joga-hairbands — JOGA recipe set (5 recipes)

Runnable job-v1 packaging of the JOGA recipe corpus. Sources of truth:

- Product: `knowledge/products/hairbands.json` (product-spec-v2, immutable)
- Campaign: `knowledge/campaigns/starz-kid.json` (creative-spec-v2)
- Edit grammar: `schemas/examples/edit-spec-v2-hairbands-logo-fix.json`

| Config | Recipe | Compiler path exercised |
|--------|--------|------------------------|
| `product-hero.job.json` | Hairbands product hero | product spec → generate |
| `starz-kid-campaign.job.json` | Starz Kid campaign lifestyle | creative spec → generate |
| `edit-logo-fix.job.json` | Logo fix (replace chest mark) | surgical block → EditSpec `replace_mark` |
| `edit-remove-element.job.json` | Remove element (Joma J + swoosh) | surgical block → EditSpec `remove_element` |
| `edit-subject-swap.job.json` | Subject swap (adult → child 10–13) | surgical block → EditSpec `subject_swap` |

## Run

```bash
python3 core/run_job.py jobs/joga-hairbands/edit-logo-fix.job.json --dry-run   # cost estimate
python3 core/run_job.py jobs/joga-hairbands/edit-logo-fix.job.json            # generate
```

## References

All references are `optional: true` so dry-run passes on a clean checkout.
For production runs drop assets into `jobs/joga-hairbands/reference/`:

- `flatlay.png` — hairband product flat-lay (product ref)
- `parent-shot-1-base.png` — the parent generation being edited (edit jobs, reference image 1)
- `joga-chest-logo-stacked-yellow.png` — stacked JO/GA chest logo (logo-fix, reference image 2)
- `starz-s-crest-blue.png` — STARZ S-crest (subject-swap, reference image 3)

Edit jobs iterate: each new version references the prior `out/<id>__edit__vN.png`
as reference image 1 (see JOGA Starz operational workflow).
