## Manual Executor (BRIEF one-shot plane)

This repo includes the **manual sdk-executor package**. Agents MUST use it for autonomous BRIEF execution — do not hand-assemble env blocks.

| Half | Command |
|------|---------|
| Exec | `./scripts/run-brief.sh --brief-id N` |
| Verify | `./scripts/verify-brief.sh --brief-id N` |
| Preflight | `./scripts/run-brief.sh --brief-id N --dry-run-only` |

- Tenant: **north-star** (`00000000-0000-0000-0000-000000000001`)  
- Default lane: **deepseek**  
- Manifest: `.manual-executor/manifest.json`  
- Full contract: `docs/MANUAL_EXECUTOR.md`  

Programme work: frontier task → scribe brief → dry-run → exec → verify → close task row.

<!-- manual-executor-v1 -->

## Git Oracle Pipeline
<!-- git-oracle-pipeline-v1 -->

Sequential **exec → land gate → verify** chain for programme work. Do not call
`sdk_executor.py` directly — use repo delegates.

| Action | Command |
|--------|---------|
| One brief (exec+verify) | `./scripts/brief-oracle-step.sh --brief-id N` |
| Sequential chain | `./scripts/chain_runner.sh ID [ID...]` |
| Exec only | `./scripts/run-brief.sh --brief-id N` |
| Verify only | `./scripts/verify-brief.sh --brief-id N` |

- Tenant: **north-star** (`00000000-0000-0000-0000-000000000001`)
- Default branch: **main** · Push mode: **pr**
- Manifest: `.git-oracle-pipeline/manifest.json`
- Full contract: `docs/LANDING.md`

<!-- git-oracle-pipeline-v1 -->
