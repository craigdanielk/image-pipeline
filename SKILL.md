---
name: image-pipeline
description: Provider- and tenant-agnostic image-generation service driven by a single JobSpec JSON (schema job-v1). Use when an agent needs to generate a product/scene image from a CAD/engineering spec + prompt + optional reference photo — especially the two-stage "canonical plates then scenario" recipe for keeping a product on-model across renders. Triggers on "generate a product image", "image-pipeline", "run a JobSpec", "make studio plates", "render the hero/card/section image", "on-model product render". Lives at services/aragon/image-pipeline.
---

# image-pipeline — agent playbook

A JobSpec-in / image-out service. The **JobSpec is the public contract**
(`schema_version: "job-v1"`). One `core/run_job.py` backs both the CLI now and a
future HTTP/SaaS endpoint. Nothing tenant- or product-specific lives in the
core — it asks an **adapter registry** by name.

**Repo:** `~/Developer/GitHub/services/aragon/image-pipeline`
**Full docs:** `README.md` (how-to) · `WORKFLOW.md` (node contract) ·
`core/job.schema.json` (schema)

## When to use
- Generate a product image from a CAD/engineering description + prompt.
- Keep a product **on-model** across many scenes → use the **two-stage recipe**.
- Add a new backend (fal/replicate/openai) or output target (s3/webhook) → add
  an adapter, don't touch the core.

## The three inputs (mental model)
| Input | Field | Meaning |
|-------|-------|---------|
| Feeder | `spec.cad` + `spec.prompt_template` | the object truth + prompt (WHAT to render). `spec.cad` = inline text or path → available as `{cad}` in the template. |
| Target | `reference` | look-anchor image (null = text-to-image). path / url / `{image, optional}`. |
| Output requirement | `output.*` | varies per case: `composition`/`background` fold into prompt; `aspect_ratio`/`resolution`/`count` become engine flags. |

## The flow (5 nodes)
`intake → validate → build_prompt → resolve reference → engine(cost|generate) → persist`
- Pure core = `core/run_job.py`. Registry = `core/adapters/__init__.py`.
- Seams: **intake** (cli) · **reference** (local) · **engine** (higgsfield) · **output** (local).
- Output files auto-version: `<id>__<imgtype>__v<N>.png`, never overwritten.

## Run
```bash
python3 core/run_job.py jobs/<name>.job.json --dry-run   # cost only — DO THIS FIRST
python3 core/run_job.py jobs/<name>.job.json             # generate + save to out/
python3 core/run_job.py - < some.job.json                # stdin
```

## Minimal JobSpec
Required: `schema_version`("job-v1"), `id`(lowercase slug), `spec.prompt_template`,
`engine.{name,model}`. Everything else optional (see README anatomy). `{cad}`,
`{composition}`, `{background}`, and any `subject` key are placeholder tokens.

## Engines (higgsfield)
- `recraft_v4_1` — **text-to-image**, rejects `--image`. Has batch_size/model_type/colors.
- `nano_banana_2` — **image-capable** (reference / multi-ref `input_images`).
  No batch_size/model_type/colors. ~50s/gen → run jobs individually with a large
  timeout, never a tight batch loop.

## Two-stage recipe (the core skill)
**Stage 1 — plates.** CAD + one real target photo → 3 studio plates on neutral
grey (plan / hero34 / profile), on-model, no lighting fluff. Curate → promote
keepers to `jobs/<product>/reference/plates/` → record in `cad.md §9`.
**Stage 2 — scenario.** Feed the plates as multi-ref → the real site asset
(hero/card/section). Same plates every scene = product stays consistent.
Worked examples: `jobs/primi-toughkey/`, `jobs/primikey-usba/`.

## Add a job / add an adapter
- **New product:** `jobs/<product>/{cad.md, reference/<target>.png, plate-*.job.json}`;
  dry-run → generate → curate → wire plates into `cad.md §9` → author Stage-2 job.
- **New backend:** add `core/adapters/<seam>_<name>.py` (same seam signature),
  register in `core/adapters/__init__.py`, name it in `engine.name`. Core unchanged.

## Gotchas (hard-won)
- **`--dry-run` first, every time** — real gens cost credits.
- **nano_banana thickness drift** ~2× on side views from a single 3/4 ref → CAD
  dims are authoritative (`cad.md §9`).
- **You can't see images in the CLI** — write a labeled sheet to `~/Desktop/x.png`,
  hand over `! open ~/Desktop/x.png`. Read/SendUserFile don't render inline for the user.
- **Bash mangles multiline** on this machine — single-line only; Write scripts for multiline.
- **`reference/` is gitignore'd** by the legacy pattern — force-add job reference
  photos + curated plates or they won't be tracked.
- **NEVER regenerate an approved "perfect" asset to make a variant** — derive
  (recolor same geometry) via the plates, never generate fresh inconsistent art.
- Bare `python3` (3.9): `from __future__ import annotations`, no `X | Y` unions.
