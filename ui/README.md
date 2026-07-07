# Operator UI

Local-only web console for the creative compiler.

## Setup

```bash
pip install -r ui/requirements.txt
```

## Run

```bash
python ui/server.py
```

Open http://127.0.0.1:8787

Set `IMAGE_PIPELINE_UI_PORT` to change the port.

## What it does

- Lists `jobs/**/*.job.json`
- Compiles to `jobs/<dir>/compile/<job-id>/`
- Dry-run / generate via `core/run_graph.py`
- Serves output images from `jobs/<dir>/out/*.png`

The UI wraps existing CLI entry points — it does not reimplement compile or execute logic.
