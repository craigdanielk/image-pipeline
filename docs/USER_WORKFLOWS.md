# Image Pipeline — User Workflows

What a user can actually do in this system, stage by stage, and what happens
under the hood at each step. Grounded in the code as of 2026-07-09
(`ui/server.py`, `core/spec_compiler.py`, `core/graph_runtime.py`,
`core/adapters/*`). Companion to the ratified board design record
(Supabase doc `972c9883`).

## The five objects

| Object | What it is | Where it lives |
|---|---|---|
| **Product** | The physical item + its CAD spec (geometry, materials, logo rules) + reference photos + ONE operator-designated canonical image | Knowledge store spec + `{jobs_root}/{workspace}/{product}/reference/` |
| **Shot** | A saved recipe for ONE image: subject/view, prompt template, optional reference image (the "look anchor"), output settings (type, aspect, resolution), engine choice | `{product}/{shot-id}.job.json` |
| **Campaign** | The style target — scene, lighting, mood, audience. Changes HOW an image looks, never WHAT the product is | Knowledge store campaign spec |
| **Run** | One execution of a compiled shot through the 6-node runtime chain | `{shot-dir}/compile/{shot-id}/` + Runs page |
| **Artifact** | One rendered output, reviewable (pending → approved / rejected) | Artifact registry + Assets page |

## What actually reaches the model on a run

A generation call is assembled from three seams — this is the key to
understanding every workflow below:

1. **Text prompt** — compiled from the shot's `prompt_template` + the
   product's CAD spec (`{cad}` placeholder) + campaign style fields.
   Campaign/style NEVER travels as an image; it is compiled into prompt text.
2. **Shot reference image(s)** (`reference` field in the job spec) — resolved
   by `reference.resolve` and passed to the engine as `--image` inputs. This
   is the "look anchor": for edit-shots it is the image being edited; for new
   compositions it grounds framing/identity. The shot card's thumbnail is
   (usually) exactly this image — so what you see on the card IS fed to the
   model, except when the thumbnail fell back to a product image for display
   (see Thumbnails below).
3. **Product grounding images** (`engine.params.input_images`) — uploaded as
   media inputs alongside the prompt.

Models that don't accept references (capability `accepts_reference: false`)
reject seam 2 at resolve time.

### Thumbnails (display chain, not always model input)

Shot card thumbnail = first image referenced in the job spec → else the
operator's canonical product image → else a `product/flatlay/cad/main`-named
file in the product's reference dir → else first reference → else blank.
Only the FIRST link of that chain is guaranteed to reach the model. Newly
created shots with no reference are auto-grounded on the canonical image
(written into the spec), so for new work display == model input.

## Stage 0 — Onboard a product

Path: **Generate board → "+ New product from photos"** (or Products → New).

1. Upload product photos (flatlay, packshot, screenshot) + optional notes.
2. A vision agent drafts the CAD spec — geometry, materials, branding rules.
3. You confirm/edit the draft, then save. The product node flips to `SPEC ✓`.
4. **Designate the canonical image**: click the product node → inspector →
   click the reference that IS the product. It becomes the node image and the
   default grounding reference for every new shot.

A product without a spec shows `NO SPEC — extract` on its node; the board
never fires the knowledge compile path for spec-less products (it states
what's missing instead of erroring).

## Stage 1 — Define the style (campaign) — optional

Path: **click the empty CAMPAIGN node → picker → "+ New campaign"** (or
toolbar "+ New campaign").

1. Upload one or more look images and/or paste a URL.
2. A vision agent extracts the style — palette, lighting, mood, composition,
   subject treatment — as a drafted style source.
3. Confirm/edit the scene description and mood, then save.
4. The compiler injects the confirmed style into the prompt at
   `prompt.build`. No campaign = product/shot defaults only.

## Stage 2 — Choose or author the shot (which image to make)

Path: **SHOT node** on the Generate board.

- **Browse shots (N)** — modal with every saved shot for the product (version
  iterations like `-v2/-v3` collapsed to latest). Clicking a card selects the
  shot and compiles it immediately (dry-run: builds the prompt, resolves
  references, prices it — generates nothing).
- **+ New shot** — the AI drafter: describe the image in a sentence, optional
  mood; the agent drafts the full prompt using the product's CAD spec and the
  house style of previous prompts; you review/edit the prompt, tick reference
  images (product refs or upload new), set type/aspect/resolution, save.
  Saving writes `{slug-of-your-sentence}.job.json` — the new shot is now a
  permanent, re-runnable card in the browser.
- **Refine with AI** (selected shot) — same drafter, seeded from the selected
  shot, for "take this but change X".

### Why shots like "Reduce Logo Size" exist

They are NOT hardcoded. Every trip through the drafter persists a job spec
named after your request sentence. `reduce-logo-size.job.json` is a one-fix
edit recipe ("make the chest logo smaller, change nothing else") minted
during a previous feedback round; `reduce-the-size-of-the.job.json` is the
same request re-drafted, with the filename slug truncated at five words.
The shot library is the accumulated history of every image request — which
is also why some labels read like sentences. (Cleaning this vocabulary into
tenant templates — plain / PDP / lifestyle / benefits / us-vs-them — is
ratified as delivery-order item 2 of the board design record.)

## Stage 3 — Run it

Two paths exist today (the design record's known dual-path; merging them is
open scope):

- **Shot path (works for everything):** select a shot → it compiles →
  **Generate image** on the SHOT node executes the compile through the
  runtime rail: `Load job → Validate spec → Build prompt → Resolve refs →
  Generate → Persist`. Rail nodes unlock left-to-right as inputs are
  satisfied; click any step to inspect what it needs/produces, including the
  full compiled prompt on `Build prompt`.
- **Intent path (OUTPUT node — Estimate / Generate):** compiles from
  product + campaign + output-type chips via the knowledge store. Requires a
  confirmed product spec.

**Estimate** prices in credits without generating. Cost-before-commit: the
drafter auto-estimates on save so Generate always shows its price.

Optional pre-run additions:
- **Operations** (toolbar chips) — targeted edit verbs (swap subject,
  replace logo) chained between product and output; each op node can re-run
  individually against the last compile.
- **+ Reuse approved output** — attach an approved artifact as an input
  (`artifacts.load` → feeds `reference.resolve`): output-as-input chaining.

## Stage 4 — Review (Assets)

Every render lands in **Assets** as a pending artifact.

- **Approve** — into the deliverable set.
- **Reject** (with reason) — hidden from the default view; file stays on
  disk; the reason is captured as feedback.
- **Comment** — feedback without a verdict.
- **Regenerate** — opens the drafter seeded with the artifact and its
  feedback: the rejection reason becomes the edit instruction, the artifact
  the base. Saving mints a new versioned shot (`…-v2`), closing the loop.
- **Drive ↗** — opens the uploaded copy where one exists (Drive is the
  delivery surface; disk renders serve via `/media`).

## Stage 5 — Iterate

The loop in practice: generate → review → reject with reason → regenerate →
new `-v2` shot → generate → approve. Version chains collapse to the latest
card in the shot browser, so the library shows current recipes, not history.

## Known gaps (open scope, brief #30150 increment 3)

- Composite: multiple asset nodes with roles (product + logo + socks) feeding
  one image — engine supports it, board doesn't yet.
- Batch: N shots × M products in one action.
- Tenant shot-template vocabulary replacing sentence-slug labels.
- One unified run path (retiring the OUTPUT-node intent compile).
