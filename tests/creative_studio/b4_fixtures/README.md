# CS-B4 provisional fixtures

Built only from contract text (`docs/creative-studio/contracts`) because CS-B2 has no committed fixtures yet.
Regenerate: `python3 -m tests.creative_studio.b4_fixtures.make_fixtures`. Business is invented ("Example Home
Services Co"); every id is a uuid5 of a fixed name.

Assets (`assets/`):
- `aileron-regular.ttf` — Aileron Regular by Sora Sagano (dotcolon.net), CC0 1.0, the reduced subset Pillow embeds
  for `ImageFont.load_default()`; ASCII + a few symbols, OS/2 weight 400. No "é" (used by the glyph_missing test).
- `example-logo-primary.png` — generic geometric mark drawn with Pillow (no words).
- `example-generated-scene.png` — synthetic gradient/shapes standing in for a provider output (no provider call).
- `example-guide.pdf` — placeholder bytes for the package's approved PDF reference (never rendered).

Assumptions:
1. Studio mirrors of Brand CI assets keep identical bytes/sha256 and carry the BCI ref in `parent_assets`; the logo layer
   matches the package logo by sha256 + MIME (contract §4 mirror rule).
2. Font MIME is `font/ttf` (matches CS-B1 bucket allow-list / `cs_extension_for_mime`).
3. Rights receipts on fixtures are `permitted`, channels/territories `["*"]` — explicit wildcard evidence for tests only.
4. Lineage refs (request, treatment, recipe, plan, catalogue) and MOS target refs are stand-ins with fixed hashes; cost is
   `unknown` (150000 µ$ unresolved) to prove unknown cost is never rewritten to zero.
5. The 9:16 safe zone cites marketing-os `registry/channel-specs.yaml@9a9caa07` (meta_ads image 9:16: 14% top/bottom).
   The other canvases use the explicit `artboard-bounds-only` choice; no inset is invented.
