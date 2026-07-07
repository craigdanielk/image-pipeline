# Regenic Label / Hero Image Pipeline

Deterministic, config-driven tool that turns a **photoreal empty-label blank**
plus **product label data** into a finished product hero image. Built to end the
ad-hoc rediscovery loop: change the target template and/or the data, run, done.

> **Design rule:** the realism lives in the *blank* (a photoreal AI-generated
> bottle/canister/etc with an empty label area). The label is composited on
> **flat and crisp** — NO per-pixel displacement warping. (Displacement was the
> historical mistake: it mangled text and never matched the 3D target.)

## Workflow (n8n-style nodes)

```
 [fetch-data]  ──► data/products.json     (matrix Sheet → product label data)
 [fetch-templates] ──► templates/<Tn>.png (Drive _CANONICAL_TEMPLATES → blanks)
                         │
                 config/templates.json    (per-type geometry + fonts)
                         │
        ┌────────────────┴───────────────┐
        ▼                                 ▼
   run.py  (orchestrator, per product):
     1. resolve template config   (defaults ⊕ per-type override)
     2. build label spec          (geometry + this product's text)
     3. render-label  ─► out/_art/<slug>.png     (render_label.mjs, Playwright)
     4. composite     ─► out/<slug>__hero.png    (composite.py, PIL)
        ▼
   [export]  ──► Drive folder + matrix Image State   (export_heroes.py)
```

## The two injection points (this is the whole point)

| Inject | Where | How to change target |
|--------|-------|----------------------|
| **Blank image** (target template) | `templates/<Tn>.png` + `config/templates.json` | swap the PNG / edit geometry; re-run |
| **Product data** | `data/products.json` (from matrix) | edit the Sheet, re-run `fetch_data.py` |

No code edits to retarget. Geometry per type is pure config.

## Usage

```bash
# refresh inputs (when blanks or matrix change)
python pipeline/fetch_templates.py
python pipeline/fetch_data.py

# build heroes
python pipeline/run.py --type T2 --slug sildenafil-100mg   # one product
python pipeline/run.py --type T2                            # all T2 products
python pipeline/run.py --all --active-only                  # every active product w/ a blank

# publish
python pipeline/export_heroes.py --folder <DRIVE_FOLDER_ID> --update-matrix
```

## Files

```
config/templates.json   per-type layout (canvas, label_box, fonts, warp, shading)
templates/<Tn>.png      photoreal empty-label blanks (pulled from Drive)
data/products.json      product label data (pulled from matrix Sheet)
pipeline/
  fetch_templates.py    NODE fetch-templates
  fetch_data.py         NODE fetch-data
  render_label.mjs      NODE render-label  (HTML→PNG, deterministic)
  composite.py          NODE composite     (flat overlay; optional warp/shading)
  run.py                WORKFLOW orchestrator
  export_heroes.py      NODE export
out/<slug>__hero.png    outputs
```

## Calibrating a new packaging type

1. Ensure `templates/<Tn>.png` exists (a photoreal blank with empty label area).
2. In `config/templates.json` set `label_box` {x,y,w} over the label region.
   For angled labels, set `warp` to a 4-point quad [TL,TR,BR,BL] in canvas px.
3. `python pipeline/run.py --type <Tn> --slug <any product on that type>`; view; adjust box.

## Known gaps / notes
- **T9 (airless pump jar)** has no `__BLANK` in Drive yet — generate one.
- Current canonical blanks are studio-flat. The premium target look (gradient
  bg, soft contact shadow, angled ribbed cap, larger logo) needs better AI
  blanks regenerated to that bar — that is a *blank* upgrade, not a code change.
- Brand: GHK-Cu topicals (T9 gel, T10 serum) use **AVID-RA**, not Regenic|Lab.
- Auth: Drive/Sheets via `~/.config/regenic-sheets/token.json`.
```
