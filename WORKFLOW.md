# image-pipeline — generic image-generation workflow (job-v1)

Provider- and tenant-agnostic. **The JobSpec is the public contract**: the same
spec drives the CLI today and an HTTP endpoint later. "Any person's image +
prompt" = a JobSpec with `reference` = their upload and `spec.prompt_template` =
theirs.

## The template (n8n-style nodes)

```
[TRIGGER]  a JobSpec (jobs/<name>.job.json)
   │
   ▼   ── PURE CORE (core/run_job.py) ─────────────────────────
① validate      jobspec.validate()          contract check, fail loud
② build prompt  build_prompt()              feeder(spec.prompt_template) + subject
                                            + output.composition/background  → prompt
③ reference     adapters.reference          desired target image → local path (optional)
④ engine        adapters.engine             prompt (+ ref) → image(s); dry-run = cost
⑤ persist       adapters.output             versioned save  out/<id>__<imgtype>__vN.png
   ──────────────────────────────────────────────────────────
```

## The three inputs (your mental model)

| Input | JobSpec field | Node |
|-------|---------------|------|
| CAD extraction **+ prompt** (the feeder) | `spec.cad`, `spec.prompt_template` | ② |
| the **desired target image** | `reference` | ③ |
| the **output required** (varies case to case) | `output.*` | ②/④ |

## Four adapter seams (the only things that touch the outside world)

| Seam | Now | SaaS swap |
|------|-----|-----------|
| intake | `cli` (file / stdin) | `http` (POST body + upload) |
| reference | `local` (path / URL) | `s3` / `drive` |
| engine | `higgsfield` (CLI) | `fal` / `replicate` / `openai` |
| output | `local` (versioned file) | `s3` + `webhook` |

The pure core never imports a concrete adapter — it asks the registry
(`core/adapters/__init__.py`) by name. Add a backend = add a registry entry.

## Run

```bash
python core/run_job.py jobs/_example.job.json --dry-run   # cost only, no credits
python core/run_job.py jobs/_example.job.json             # generate + save to out/
python core/run_job.py - < some.job.json                  # JobSpec from stdin
```

## Notes / hardening backlog

- `recraft_v4_1` is text-to-image (rejects `--image`); `nano_banana_2` accepts a
  reference. The engine adapter passes `--image` only when a `reference` is set.
- `output.colors` → `--colors` passes a JSON array; verify the CLI's accepted
  shape per model (hardening).
- SaaS-only concerns (auth, credit metering, rate limit, queue, tenant
  isolation) live OUTSIDE the core as a thin API shell — the core is unchanged.
- `pipeline/` + `config/` + `SPEC.md` are the original **regenic** pipeline,
  kept as the extraction source; being generalized into this `core/`.
