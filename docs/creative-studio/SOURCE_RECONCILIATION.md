# Creative Studio source reconciliation

Recorded 30 October 2026. Scope: CS-A1 / NS-10804, project `95f25a77-9b10-4703-9945-7447a992eec9` (`CREATIVE-STUDIO`). This is observed architecture evidence, not a product test or a canonical Task-completion receipt.

## Source decision

Continue `craigdanielk/image-pipeline`, using its root `core/`, `schemas/` and `ui/`. The selected baseline is `e98cf4bf3558f87279ca518d71ee58530f6cf039`. A fresh connected GitHub comparison of this revision with `main` returned `identical`, zero commits ahead/behind. Local `main` and `origin/main` resolve to that same revision. No branch divergence was inferred from names or stale documentation.

Commit `7038441` is the recorded extraction of the creative compiler into this dedicated service repository. Its README explicitly describes that extraction from `services/aragon/image-pipeline`. Control Tower's existing `registry/repo-workspace-contracts/image-pipeline.yaml` binds `craigdanielk/image-pipeline` to this root, owner North Star. `registry/service-sdk-router.yaml` maps both the ARAGON image-pipeline capability and Creative Compiler service to this same root.

The nested `services/aragon/image-pipeline` directory remains historical source. It does not contain the current FastAPI/React operator application. The source manifest inventories 108 current compiler/schema/UI files; 63 have nested counterparts, of which 58 match the working root bytes. No nested copy will be independently evolved or deleted as part of this decision.

## Existing changes to preserve

The local checkout already has modifications to `config/tenants.json`, `core/adapters/engine_higgsfield.py`, and `jobs/primi-toughkey/cad.md`, plus untracked client job directories and a plans directory. They are retained and recorded in `source-manifest.json`. No reset, clean, stash, client-data migration or overwrite was performed.

The selected compiler/UI inventory differs from HEAD only in the existing Higgsfield adapter. That patch contains 46 additions and 16 deletions: current CLI image-reference flags, prompt-length logging, rejection of invalid JSON, and reference resolution changes. It belongs to the local CLI compatibility path. Preserve it when branching or landing new work. The hosted service must use scoped provider API credentials and safe media fetches; it must not expose this adapter's arbitrary URL/local-path handling or machine login to customers.

## Maintained implementation boundaries

| Source | Disposition |
|---|---|
| `core/compile_bundle.py`, source languages and graph compiler | Preserve the deterministic compile boundary and hashed artifacts. |
| `core/graph_runtime.py` and node registry | Extend with durable per-node scheduling; preserve frozen-plan execution. |
| Root `ui/app/src/pages/BoardPage.tsx`, gallery and React Flow components | Reuse as the optional canvas and library foundation. |
| `ui/server.py` | Preserve local operator functionality; publish separate authenticated hosted routes. Local job paths/tenant selectors are not hosted authority. |
| `ui/cloud_store.py`, generic tables and old buckets | Historical/local adapter; replace with a service-owned, scoped repository in hosted mode. Do not silently adopt its records. |
| Nested ARAGON source and `pipeline/` | Historical provenance and compatibility reference. |
| Marketing OS thin compiler | Consumer integration to migrate through existing NS-10631, retaining versioned agency contracts. |

## Evidence and practical limit

`source-manifest.json` contains the file hashes, working-versus-HEAD comparison and preservation inventory. The remote comparison and canonical project/session observations are recorded in the checkpoint receipt. This establishes which source to build upon; it does not establish hosted readiness, performance, generation quality or account capacity. Product testing remains CS-M3, after the hosted build.
