# Creative Studio — image and campaign-video composition

Status: CS-M1 authored architecture. The wire definition is [composition-v1.schema.json](../contracts/composition-v1.schema.json); this document defines the editing and rendering semantics. The product offers a bounded campaign editor, not full Figma or professional NLE parity.

## Source and renderer boundary

Provider output is an immutable original asset in owned storage. The composition references that asset; it never overwrites it. A generated image/clip and an exported creative are different assets. The export includes the exact logo, copy and layout the user reviewed, not a UI-only headline overlay placed above an unmodified download.

The browser displays and edits a declarative scene. It is not the authoritative renderer. A saved composition is compiled into deterministic image/video assembly work; the hosted renderer pins its version, fonts, asset hashes and output settings. The last export preview is a rendering of the saved composition from that same pipeline. Browser preview may be immediate while a field is changing; it must not claim export fidelity before fonts/assets have resolved and the saved preview exists.

The generation compiler remains responsible for provider prompts and plans. Composition is a registered deterministic operation in that compiler/runtime, not a second prompt compiler in the frontend. Text and logo positioning is never passed back through generative rendering merely to move a layer.

## Image document

`kind: "image"` requires at least one artboard and no video body. Each artboard is an independent channel layout with a stable slug ID. Initial supported format presets are:

| Format label | Pixels | Default export |
|---|---:|---|
| Square | 1080 × 1080 | PNG |
| Portrait feed | 1080 × 1350 | PNG |
| Story / vertical | 1080 × 1920 | PNG |

Custom dimensions are unavailable in the first release. The schema's broader structural bounds are not a promise that every width/height combination is supported. A named recipe output binds the selected format preset; server preflight enforces that preset. Input/generated source images may be larger within their separately declared ingestion/provider/worker bounds; output artboard limits do not truncate their stored originals. Variants can reuse one original image in different crops, but each artboard retains independent layout/copy bounds. “Apply to all sizes” is an explicit multi-artboard edit with a preview; resizing one artboard does not silently reflow the others.

```text
cs-composition-v1
  id / version_id / revision / workspace_id / sha256
  kind = image
  basis_recipe = exact prior recipe VersionRef
  basis_run = run/plan/compile/event-cursor reference or null
  artboards[]
    id, width, height, color_space = srgb, background
    layers[]
      id, name, type, transform, order, locked
      content = type-specific object
  video = null
```

Common layer transform is `{x, y, width, height, rotation_deg, opacity}` in artboard pixels; width and height must be positive, opacity 0–1. The origin is the top-left. Rotation is clockwise about the layer box center. Drawing order is ascending `order`, unique among siblings. No perspective/skew, arbitrary blend modes, filters or animation curves are exposed in v1.

| Layer type | `content` | Supported controls |
|---|---|---|
| `image` | `{asset: AssetRef, fit: cover\|contain\|native, crop: normalizedRect\|null}` | Place, scale, rotate, crop, opacity, duplicate, replace with exact asset version |
| `logo` | `{asset: AssetRef, brand_logo_id, fit: contain}` | Pick approved variant, position, proportional size; inspect clear space. Original graphic is a single exact layer, not an editable vector drawing. |
| `text` | `{text, copy_id, font: AssetRef, style}` | Exact words/newlines, font face/weight, size, line height, letter spacing, fill, horizontal/vertical alignment and bounding box |
| `shape` | `{shape: rectangle\|ellipse\|line, fill, stroke, stroke_width_px, corner_radius_px}` | Simple background/CTA/line shapes, dimensions, color, stroke; no freeform pen tool |
| `group` | `{child_ids}` | Group/ungroup, order, lock; move/rotate together; no cycles or cross-artboard children |

Text style is `{font_size_px, weight, line_height, letter_spacing_px, fill, align, vertical_align}`. The exact font file/version/hash must be available and permitted for the intended use. Missing weights or characters do not silently fall back to system fonts, synthesized bold or substituted glyphs. The field shows the missing face/glyph issue and an explicit replacement choice. Text is UTF-8 and preserves entered content and intentional line breaks. Wrapping is deterministic within the saved text box; overflow blocks final export until the user changes text/box/style. No automatic shortening or silent font-size reduction.

`copy_id` relates the layer to the request/export copy entry, not directly to an unversioned campaign row. A new text phrase creates a new local copy entry and, on MOS handoff, requires a corresponding copy version and new review. A logo's brand role and usage constraints come from the pinned Brand CI package. Lock is an editing convenience, not a security or approval control; unlocking cannot waive brand constraints. Logo distortion, crop, arbitrary recolor and forbidden placement are refused. The user can choose a different approved logo variant or revise the upstream approved package.

Normalized crop is `{x, y, width, height}` in source image coordinates with values within 0–1 and `x + width <= 1`, `y + height <= 1`. Orientation is normalized once at asset ingest and that derivative is recorded; crop never depends on a browser's EXIF interpretation. `contain` preserves all source pixels inside the target box; `cover` uses the explicit crop/focal choice to fill it; `native` maps one source pixel to one composition pixel before the declared transform. Logos always use contain.

Grouping has one parent per child, no cycles, and explicit sibling order. Child coordinates are local to the group. Group transforms translate/rotate/adjust opacity; group resizing is a command that scales child boxes and text metrics once and stores the resulting geometry. Group width/height describe bounds and do not introduce a second implicit scale at render. Group bounds do not clip children. Guides and safe areas are editor metadata derived from pinned channel/brand presets, not hidden export pixels.

The artboard is sRGB. PNG and WebP can retain alpha when the chosen format allows it; JPEG explicitly flattens against the chosen artboard background. Image stage and export apply the same normal alpha compositing order. The user sees the chosen flattening color before export. Original files and their profiles remain in the library with normalized derivative lineage.

## Image editing functions

| Function | Required behavior | Boundary |
|---|---|---|
| Text-conditioned or reference-conditioned image | Current catalogue determines available model/roles/counts; original result stored | Prompt text does not create exact copy/logo layers by itself |
| Mask/region edit | User selects an exact base image; rectangle/brush creates a versioned mask at its normalized pixel dimensions | White = editable, black = preserve; mask/base hashes and dimensions pinned. Partial edits are model-dependent and can affect other pixels; no exact preservation guarantee is fabricated. |
| Crop/resize | Non-destructive geometry in composition; review all selected formats | No regeneration or automatic content-aware fill |
| Upscale | Explicit operation with supported provider/tool, target size and quoted cost if charged | A larger raster is not proof that product geometry or fine text became correct |
| Exact text/logo/shape layout | Editable layers with numeric and pointer controls, align/distribute, order, lock, duplicate, undo/redo | No generative redraw of a logo, headline or CTA for layout edits |
| Variants | New variant ID; compare original and proposed result; choose one for composition | No replacement of an already exported file under the same version |
| Reuse output | “Use as reference” pins exact version and intended role | A signed download URL is never the reference's identity |

A mask applies only to the generated image layer, before separate brand/text overlays are composed. Mask creation is separate from hiding/removing a composition layer. Failed edits keep the source and the failed attempt's cost/provenance. Any resulting image is reviewed again before export.

## Video document and authoring envelope

Video uses one picture sequence with up to six clips, each at most ten seconds after normalization, up to thirty seconds total. Title and end-card picture segments count toward six and thirty. Output is 1080 × 1920 at `frame_rate: {num: 30, den: 1}`. A shorter provider maximum takes precedence. The execution blueprint's stage/resource bound governs rendering; reaching it is an explicit recoverable job failure, not permission to silently lower resolution or skip a shot.

Storyboard and timeline edit the same shot IDs. The storyboard presents purpose, shot direction, source, planned length and exact on-screen copy. Generation requests for shots belong in the accepted treatment/recipe, and generated clips become source assets. The timeline consumes chosen clips; it does not hide separate prompt text or a second generation path.

```text
cs-composition-v1
  kind = video
  basis_recipe / basis_run / identity fields as above
  artboards[] = optional exact title/end-card layer documents
  video
    width = 1080, height = 1920
    frame_rate = {num: 30, den: 1}
    duration_frames <= 900
    shots[]
      id, source: AssetRef
      source_in_frame, source_out_frame, source_fps: {num, den}
      sequence_start_frame, fit, crop, muted
    transitions[]
      id, from_shot_id, to_shot_id, type: cut|crossfade, duration_frames
    overlays[]
      id, layer, start_frame, end_frame
    captions[]
      id, start_frame, end_frame, text, copy_id, font_ref, style
      x, y, width, height
    audio[]
      id, source: AssetRef, offset_frames, trim_in_frame, trim_out_frame
      gain_db, fade_in_frames, fade_out_frames
```

All composition intervals are half-open `[start, end)`. Timeline values are integer 30 fps ticks; the UI displays seconds/timecode and snaps edits to a frame. Shot trims refer to the original source frame rate and must lie inside the retained clip. The normalized duration is round-half-up of `(source_out_frame - source_in_frame) × 30 × source_fps.den / source_fps.num`; a normalized derivative records that exact time mapping. It must have at least one frame and no more than 300. Still title/end cards are first rendered to normalized clip assets with an explicit duration, so shot records consistently reference playable media.

The first shot starts at zero. Adjacent cuts use `next.start = current.end`; crossfades use `next.start = current.end - transition.duration`. Cut duration is zero; crossfade is 1–30 frames and fits inside both adjacent shots. An interior shot's incoming and outgoing overlaps cannot consume its entire length. There are no unexplained gaps, overlaps or arbitrary picture tracks. Explicit blank/color cards are possible picture segments. `duration_frames` equals the end of the last segment. The browser and worker use identical arithmetic; export does not infer timing from array order alone.

Video overlays reuse exact image-layer semantics and are active only within their saved interval. An exact brand logo may span the full sequence; title and end cards use the same font/logo assets as images. They are static or participate in the specified picture transition; there are no unrestricted keyframes in v1. End-card copy remains editable text, even when a preview clip derivative is materialized for assembly.

Captions are timed exact text, with an editable caption list, placement, font and safe-area preview. They can be authored manually from approved copy; automated transcription is not assumed available. Cue overlap in the same caption lane, zero duration, out-of-range times and text overflow are explicit issues. Copy edits or timing changes are saved revisions. Replacing a shot does not silently rewrite captions.

Audio supports uploaded/permitted or separately generated and retained assets, plus mute for original clip audio. The first-release editor presents voice and music lanes; the wire contract permits a bounded set of up to eight audio segments across them. Trims and offsets are integer composition ticks, converted to exact 48 kHz sample positions by the renderer; gain is in dB and fades cannot exceed a segment. Audio is not silently stretched or looped to fill the video. Generated audio, if offered by an admitted catalogue operation, is a separate paid step with its own controls/receipt. Its availability is not implied by the presence of an audio track.

## Video functions and affected-work behavior

| Function | Supported v1 behavior | Retained / affected work |
|---|---|---|
| Shot planning | Add/reorder/delete up to six shots including cards; purpose/direction/source/duration per shot | Unsatisfied shots remain placeholders; no hidden auto-generation |
| Text-to-video / image-to-video | Catalogue-gated inputs; explicit start/end/reference roles where supported | No unsupported controls or model-specific default duration invented |
| Review/replace one shot | Compare clip alternatives and choose exact version | Other clips retained; replacement requires explicit timing choice if length differs |
| Trim/reorder | In/out numeric fields and timeline controls, 1× playback only | Reassembles retained clips; no provider regeneration |
| Fit/crop | Contain or explicit cover crop to the vertical format | Retains original clip and records geometry |
| Transitions | Cut and crossfade up to one second | Small defined overlaps; no transition plug-ins/VFX |
| Captions / logos / CTA | Exact timed editable layers; brand safe-area guidance | Overlay/assembly render only |
| Voice/music | Choose retained asset, trim, offset, gain, fade, mute clip audio | Rerender audio mix/assembly; no automatic generation or copyrighted track procurement |
| Title/end cards | Exact image composition rendered for an explicit duration | Deterministic card render; counts as a sequence segment |
| Export | Saved MP4 and thumbnail, plus version/provenance manifest | Complete file in owned storage before success |

When a replacement clip is shorter than its slot, offer **Trim the slot and move later shots** or **Choose a different clip**. When it is longer, offer a trim preview. Ripple changes explicitly list later captions, overlays and audio segments whose timing will move; the user sees the new duration. Fixed absolute timing is retained unless the user chooses to move it. No hidden freeze frame, time stretch or content deletion resolves the mismatch.

## Export representation and preflight

Each completed media file has a `cs-export-v1` with immutable file ID/version/hash, format/dimensions/duration, thumbnail, exact request/treatment/recipe/composition/brand/plan/catalogue lineage, original generated source attempts, font hashes, exact copy/text hashes, costs and rights summary. A pack is a named collection of those exports; an optional ZIP download includes the files and a readable/JSON manifest. It does not create a new approval or erase individual export identities.

| Export | Settings / deliverable |
|---|---|
| Image | PNG, JPEG or WebP at the exact selected preset; sRGB; explicit alpha/flattening and quality preset |
| Video | MP4, H.264 picture, AAC audio when present, yuv420p, 1080 × 1920, 30 fps; no-audio export remains valid when deliberately chosen |
| Poster | Exact selected frame or retained card preview, with its source/time recorded |
| Editable source | Recipe and composition JSON plus references/manifest; include original/font bytes only where redistribution permission allows. No claim of PSD/Figma round-trip. |

Preflight checks saved/current composition, complete source assets, matching hashes/scope, brand applicability, permitted logo/font use, actual text fit, format limits, duration/transition/time bounds, required output selection and unresolved job/charge status. Source and cost issues are shown separately. A known unresolved charge can be shown in provenance only if the product policy explicitly permits delivery; it is never silently represented as confirmed zero. Production/review eligibility is decided by the service/MOS contracts.

The user reviews the rendered preview and exact copy, then requests **Export pack**. This is a saved deterministic job; no model call is needed unless the user explicitly selected an outstanding media operation. Final download success requires stored bytes and a manifest. Changing any layer/shot after export shows **Newer changes not exported** and creates a new export version. **Send to MOS** binds that exact export, copy and source versions; Studio selection and successful rendering are not downstream approval.
