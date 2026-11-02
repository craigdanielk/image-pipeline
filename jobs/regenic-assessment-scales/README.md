# regenic-assessment-scales — Norwood + Ludwig stage tiles (BRIEF #31008 / RL-0241)

Selectable stage-illustration tiles for the regeniclab.co.za hair-assessment
photo step. Male path → Norwood I–VII (7 tiles), female path → Ludwig I–III
(3 tiles). Confirmed priority: Larry 07-13 "hair site" email — "the male and
female pictures haven't been put in — the Ludwig scale for women and the
Norwood scale for men."

## Style contract (identical across all 10 tiles)

- Flat clinical medical illustration, NOT photoreal, no real patient likeness
- Same generic male head (Norwood) / female head (Ludwig) in every stage:
  three-quarter front-left view, minimal facial features, neutral expression
- Hair navy `#1b2846`, scalp/skin neutral warm light tone, background seamless
  very-light grey `#f7f8fa`
- ONLY text permitted: the stage code (e.g. "III") small at bottom-center in
  navy — NO other words, NO logos, NO Novecy, NO watermarks
- Square 1:1, engine nano_banana_2 (2 credits/tile)

## Anchor-first run order (consistency + credit discipline)

1. `norwood-4.job.json` FIRST (mid-stage shows the style best). QA it hard
   (text artifacts, style, medical accuracy).
2. Accepted output becomes `reference/style-anchor.png`; every other job
   points at it (`optional: true` so dry-run passes).
3. Fan out norwood 1–3, 5–7, then ludwig 1–3 (ludwig female head, same style).
4. QA EVERY tile individually (Higgsfield text-artifact gotcha).

```bash
python3 core/run_job.py jobs/regenic-assessment-scales/norwood-4.job.json --dry-run
python3 core/run_job.py jobs/regenic-assessment-scales/norwood-4.job.json
# accept → cp output to reference/style-anchor.png → run the rest
```

## Destination

Accepted PNGs → webp → tenant repo `src/assets/assessment-scales/` as
`norwood-1.webp … norwood-7.webp`, `ludwig-1.webp … ludwig-3.webp`
(oracle contract names — do not rename). Publish to live stays Craig's action.

## Medical stage definitions (canonical)

| Tile | Pattern |
|------|---------|
| norwood-1 | Full juvenile hairline, no visible recession |
| norwood-2 | Slight symmetric triangular recession at both temples |
| norwood-3 | Deep temporal recession, M-shaped hairline (first balding stage) |
| norwood-4 | Deep frontal recession PLUS a distinct bald patch at the crown, separated by a solid band of hair |
| norwood-5 | Larger frontal and crown loss, the separating band narrow and thinning |
| norwood-6 | Frontal and crown areas merged into one large bald area, side band gone |
| norwood-7 | Only a horseshoe band of hair around the sides and back remains |
| ludwig-1 | Mild diffuse thinning on the crown, slightly widened centre parting, front hairline intact |
| ludwig-2 | Pronounced widening of the parting and clearly visible diffuse crown thinning |
| ludwig-3 | Extensive crown hair loss with scalp fully visible, thin front fringe preserved |
