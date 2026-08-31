# Git Oracle Pipeline — north-star

Installable verify/landing plane for manual BRIEF execution. Pairs with
[MANUAL_EXECUTOR.md](./MANUAL_EXECUTOR.md) (exec half).

## Pipeline

```
run-brief.sh → check_brief_landed.py → verify-brief.sh (if NEEDS_REVIEW) → merge_train (optional)
```

| Step | Script | Role |
|------|--------|------|
| Exec | `./scripts/run-brief.sh` | sdk_executor one-shot |
| Land gate | `check_brief_landed.py` | requires `completion_commit_sha` |
| Verify | `./scripts/verify-brief.sh` | oracle_runner when NEEDS_REVIEW |
| Merge | `merge_train.py --drain` | when `GIT_ORACLE_MERGE_DRAIN=1` |

## Entrypoints

```bash
# One brief (full pipeline)
./scripts/brief-oracle-step.sh --brief-id N

# Sequential chain
./scripts/chain_runner.sh 36960 36961 36962
BRIEF_CHAIN="36960 36961" ./scripts/chain_runner.sh
```

## Landing policy

| Key | Value |
|-----|-------|
| DEFAULT_BRANCH | main |
| PUSH_MODE | pr |
| GIT_ORACLE_MERGE_DRAIN | 0 |

**PUSH_MODE** vocab: `inline` (direct push allowed), `pr` (gitmaster handoff + PR),
`operator-only` (no auto-push; land gate only).

## Logs

`~/.local/state/git-oracle-pipeline/`

## Reinstall

```bash
python3 control_tower/scripts/ops/install_git_oracle_pipeline.py --tenant north-star --repo-path .
```

Installed by `install_git_oracle_pipeline.py` — schema `git-oracle-pipeline-v1`.

## Canon refs

- `control_tower/scripts/ops/merge_train.py` — GITMASTER landing
- `control_tower/scripts/ops/gitmaster_agent.py` — branch/commit/PR handoff
- `control_tower/registry/end-state-contracts/repo-canon.yaml` — repo landing rules
