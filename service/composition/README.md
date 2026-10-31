# service/composition — exact composition, export, MOS handoff (CS-B4 / NS-10801)

| Module | Role |
|---|---|
| `renderer.py` | Deterministic image compositor (Pillow, FreeType BASIC layout, no system fonts). `render_artboard()` → bytes + sha256 + receipt (source hashes, font sha/weight/glyph coverage, exact copy hashes, safe zone, renderer version). |
| `validate.py` | cs-composition-v1 schema + semantic checks (unique ids/sibling order, single-parent acyclic groups, crop bounds, workspace scope, artifact hash); semantic projection hash. |
| `fontinfo.py` | Dependency-free sfnt reader: cmap coverage + OS/2 weight. |
| `safe_zones.py` | Explicit per-canvas safe zone with a citation; `artboard_bounds()` is a recorded choice, never a default. |
| `export.py` | cs-export-v2 manifest (1:1, 4:5, 9:16, 16:9 image presets; handoff also reads earlier cs-export-v1) after persist + read-back (hash/MIME/dimensions), rights (embed on channel, validity), exact copy, lineage, unknown cost carried as-is. |
| `handoff.py` | cs-mos-handoff-v1 (copy must be bound to MOS copy versions; cost counted once per plan) and ingestion-receipt verification. |
| `routes.py` | HTTP routes (fenced block in `service/api.py`). |
| `typegen.py` | Generates `ui/app/src/studio/contracts/types.gen.ts` from the contract schemas (`--check` for drift). |
| `canonical.py` | RFC 8785 JCS + digest rules (contract.md §3). |

Typed refusals (`CompositionError.code`): `font_asset_missing`, `font_weight_missing`, `glyph_missing`,
`font_format_unsupported`, `logo_asset_missing`, `logo_not_from_brand_package`, `logo_below_min_width`,
`logo_transform_unsupported`, `logo_clear_space_violation`, `text_overflow`, `text_empty`, `outside_safe_zone`,
`layer_out_of_bounds`, `safe_zone_missing`, `asset_hash_mismatch`, `scope_mismatch`, `source_unavailable`,
`brand_package_not_approved`, `brand_decision_unresolved`, `unsupported_export_preset`, `format_mismatch`,
`rights_denied`, `source_lineage_missing`, `copy_id_missing`, `copy_revision_mismatch`, `export_readback_mismatch`,
`jpeg_requires_opaque_background`, `invalid_composition`, `artifact_hash_mismatch`.

Interpretations (contract leaves them open): logo clear space = `clear_space_ratio × min(drawn width, drawn height)`
around the drawn logo, inside the artboard and free of other copy/logo boxes; `allowed_backgrounds` is recorded but not
enforced (no vocabulary defined); `line` shapes draw the box's horizontal centre line.

Golden images: `tests/creative_studio/b4_goldens/` (regenerate deliberately with `CS_B4_REGEN_GOLDENS=1`; the renderer
version string pins Pillow/FreeType/zlib/libjpeg/libwebp so a stack change is a visible failure, not a silent skip).
