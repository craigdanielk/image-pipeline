# Enterprise Greenfield MVP

**Status:** Implemented (disk-backed workspace v1)  
**Date:** 2026-07-07

## Greenfield flow (creator)

```text
Dashboard → Start greenfield
  → /products/new     Create ProductSpec v2
  → /products/:slug   Upload references
  → /campaigns/new    Create CreativeSpec v2 (linked product)
  → /generate         Estimate → Generate
  → /runs             Run status (queued → running → complete|failed)
  → /assets           Review outputs → Approve / Reject
```

## API surface

| Endpoint | Purpose |
|----------|---------|
| `GET /api/workspace` | Workspace meta + stats |
| `POST /api/knowledge/products` | Greenfield product |
| `POST /api/knowledge/campaigns` | Greenfield campaign |
| `POST /api/knowledge/products/{slug}/refs` | Product DAM upload |
| `GET /api/products` | Knowledge-first product list |
| `POST /api/generate/intent` | Compile + execute + run registry |
| `GET /api/runs/registry` | Run queue |
| `PATCH /api/assets/approval` | Asset approval state |

## Disk layout

```text
knowledge/products/{slug}.json
knowledge/products/{slug}/refs/*
knowledge/campaigns/{id}.json
workspace/default/workspace.json
workspace/default/runs/run-*.json
workspace/default/asset_approvals.json
jobs/{product_slug}/*.job.json   # execution mirror (JOBS_ROOT aware)
```

## Environment

| Var | Purpose |
|-----|---------|
| `IMAGE_PIPELINE_JOBS_ROOT` | Tenant job output root |
| `IMAGE_PIPELINE_WORKSPACE_ID` | Workspace slug (default `default`) |

## Not in v1 (next)

- Supabase cloud mirror for products/campaigns/runs
- Multi-workspace picker UI
- Full Product Explorer tree editor
- Async job worker (runs are sync HTTP today)
- OpenAPI publish + `docs/sdk/image-pipeline` in Control Tower
