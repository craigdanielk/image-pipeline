# Manual Executor — north-star

Canonical **one-shot BRIEF execution plane** for this tenant. Fleet `scheduler.py` is optional; manual one-shots are the supported operator path when Docker lanes are dark.

## Entrypoints

| Half | Command |
|------|---------|
| **Exec** | `tenants/north-star/scripts/run-brief.sh --brief-id N` |
| **Verify** | `tenants/north-star/scripts/verify-brief.sh --brief-id N` |
| **Dry-run** | `run-brief.sh --brief-id N --dry-run-only [--skip-proxy-check]` |

From a product repo with delegates installed:

```bash
./scripts/run-brief.sh --brief-id N
./scripts/verify-brief.sh --brief-id N
```

## Operator loop

1. Programme frontier → pick task  
2. Scribe disposable BRIEF (`fn_author_brief` / `/scribe`)  
3. `run-brief.sh --dry-run-only`  
4. `run-brief.sh --brief-id N`  
5. `verify-brief.sh --brief-id N` (when `NEEDS_REVIEW`)  
6. Close durable programme / task state from evidence  

## Env layers (do not hand-assemble)

1. Platform infra — `control_tower/scripts/ops/source_global_env.sh`  
2. Lane bind — `lane_spec.py deepseek --bind` inside `set -a`  
3. Tenant scope — `EXECUTOR_TENANT_ALLOWLIST=00000000-0000-0000-0000-000000000001`  

Default lane: **deepseek**. Override: `--lane grok|gemini|system`.

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | Closed |
| 1 | Failed (see `~/.local/state/sdk-executor/`) |
| 3 | Preflight / proxy refused |
| 4 | Claim gate refused (brief untouched) |

## Git Oracle chain (sequential programmes)

For ordered multi-brief runs, use the git-oracle pipeline (exec → land gate → verify):

```bash
tenants/north-star/scripts/brief-oracle-step.sh --brief-id N
tenants/north-star/scripts/chain_runner.sh ID [ID...]
```

Docs: `tenants/north-star/docs/GIT_ORACLE_PIPELINE.md`. Install with
`install_git_oracle_pipeline.py` (requires this package first).

## Reinstall

```bash
python3 control_tower/scripts/ops/install_manual_executor.py --tenant north-star
python3 control_tower/scripts/ops/install_git_oracle_pipeline.py --tenant north-star
```

Installed by `install_manual_executor.py` — schema `manual-executor-v1`.
