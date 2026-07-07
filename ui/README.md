# Creative Compiler UI

Operator surface for **creators and brand ops** — pick a product shot, compile the workflow, estimate credits, and generate images. Developer mode exposes paths, JSON artifacts, and compile hashes.

## Run

```bash
# From repo root — builds React app if npm is installed
./ui/run.sh

# Or manually
cd ui/app && npm install && npm run build && cd ../..
python3 -m pip install -r ui/requirements.txt
python3 ui/server.py
```

Open **http://127.0.0.1:8787**

## Environment

| Variable | Purpose |
|----------|---------|
| `IMAGE_PIPELINE_UI_PORT` | HTTP port (default `8787`) |
| `IMAGE_PIPELINE_JOBS_ROOT` | Extra job scan root — e.g. `~/Developer/GitHub/tenants/joga/tools/image-pipeline/jobs` |

Example with JOGA tenant jobs:

```bash
export IMAGE_PIPELINE_JOBS_ROOT="$HOME/Developer/GitHub/tenants/joga/tools/image-pipeline/jobs"
./ui/run.sh
```

## Non-dev workflow (Creator mode)

Creator mode is the default. No JSON editing required.

1. **Browse products** — left sidebar groups shots by product folder (`jobs/<product>/`).
2. **Create a new shot** — click **+ New Shot**:
   - Pick an existing product or enter a new product name
   - Choose a template: **Studio Plate**, **Hero Scene**, or **Blank Canvas**
   - Name the shot, edit the prompt, optionally upload a reference image
   - **Create Shot** writes `jobs/<product>/<shot-id>.job.json` and `reference/` dirs
3. **Select a shot** — click a shot card in the sidebar.
4. **Compile** — sticky run bar compiles the job into a visual workflow graph.
5. **Estimate cost** — dry-run shows credit estimate on the Generate button.
6. **Generate** — runs the engine; outputs appear in the filmstrip below the canvas.
7. **Inspect prompt** — right panel **Prompt** tab shows the readable generation prompt. **Technical** tab has compile JSON (for engineers).

Toggle **Developer mode** in the header to show file paths, workspace roots, and raw job paths in the sidebar.

## Panels

| Panel | Source |
|-------|--------|
| **Products / Shots** | `GET /api/products` — grouped by `jobs/<product>/` |
| **Workflow graph** | `graph.json` via `tools/graph_to_reactflow.py` → React Flow (friendly node labels) |
| **Prompt / Technical** | Compiled prompt + per-stage JSON from `compile/<job-id>/` |
| **Output filmstrip** | `out/*.png` gallery with inline preview |
| **Run bar** | Compile · Estimate cost · Generate (with credit badge) |

## Job templates

Templates live in `ui/templates/*.json`:

| Template | Use case |
|----------|----------|
| `studio-plate` | Neutral catalogue reference plate |
| `hero-scene` | Dark floating hero with rim light |
| `blank` | Minimal starter prompt |

## API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/config` | GET | Workspace + jobs_root |
| `/api/jobs` | GET | List jobs (includes `label`, `product`) |
| `/api/products` | GET | Jobs grouped by product folder |
| `/api/templates` | GET | Job templates with human labels |
| `/api/jobs/create` | POST | Create new `.job.json` + dirs |
| `/api/upload` | POST | Upload reference image (`?product_slug=`) |
| `/api/compile` | POST | Compile job → manifest + graph + prompt |
| `/api/execute` | POST | Run graph from compile dir |
| `/api/compile-tree` | GET | List compile artifacts |
| `/api/artifact` | GET | Read stage JSON |
| `/api/graph-reactflow` | GET | React Flow nodes/edges |
| `/api/outputs` | GET | List output PNGs |
| `/media?path=` | GET | Serve image (repo or JOBS_ROOT) |

### Create job example

```bash
curl -X POST http://127.0.0.1:8787/api/jobs/create \
  -H 'Content-Type: application/json' \
  -d '{
    "product_slug": "my-product",
    "shot_id": "hero-float-a",
    "template_id": "hero-scene",
    "subject": { "product_name": "My Product" },
    "prompt_template": "Ultra-premium studio shot of {product_name}..."
  }'
```

### Upload reference

```bash
curl -X POST "http://127.0.0.1:8787/api/upload?product_slug=my-product" \
  -F "file=@reference.png"
```

## Dev (hot reload)

Vite proxies `/api` and `/media` to the FastAPI server (default `:8787`).

**One command (recommended):**

```bash
chmod +x ui/dev.sh   # once
./ui/dev.sh
```

Open **http://127.0.0.1:5173** — UI hot-reloads on save; API stays on `:8787`.

**Or two terminals:**

```bash
# Terminal 1 — API
python3 ui/server.py

# Terminal 2 — UI with HMR
cd ui/app && npm run dev
```

Set `IMAGE_PIPELINE_UI_PORT` if the API is not on `8787` — Vite reads the same var for the proxy target.

## Stack

- **Backend:** FastAPI (`ui/server.py`) — core compiler stays dep-free
- **Frontend:** React + `@xyflow/react` (Weave-inspired canvas tokens)
- **Graph tool:** `tools/graph_to_reactflow.py` — graph-v1 → React Flow JSON with `displayLabel`
- **Templates:** `ui/templates/` — packaged prompt starters for non-dev job creation
