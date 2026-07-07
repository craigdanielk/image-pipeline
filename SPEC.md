# Regenic Lab — Official Product Label Spec

Source of truth for label typography, color, and layout. Derived from the real
Regenic Lab products (reference photos in `reference/`, primary = Biotin capsule
front+back render `17.39.02`) + brand tokens extracted from the website codebase
(`src/index.css`, `tailwind.config.ts`). Foreign compounder branding on physical
samples (e.g. "Novecy", a contract compounder named in fine print) is IGNORED —
only the Regenic|Lab design applies.

## Brand tokens (authoritative — from codebase)
| Token | Value | Use |
|-------|-------|-----|
| Font  | **Montserrat** (300–800) | all label text |
| Navy  | `#1b2846` (`--navy` 221 45% 19%) | logo, product name, dose, headings |
| Cyan  | `#00a1d6` (`--accent` 195 100% 42%) | logo leaf/divider accent only |
| Muted | `#5b687b` (`--muted-foreground` 215 15% 42%) | FORM line, secondary text |

## FRONT label hierarchy (from ref5)
Vertical order, left-aligned, sharing the logo's left margin:
1. **Schedule badge** — boxed, small, top-left (`S0`/`S2`/`S3`/`S4`; none for Section 21 / S0 general where applicable). On the blank this is baked.
2. **Regenic|Lab logo** — large, top. Baked into the blank.
3. *(generous gap — ~1.2–1.5× logo height)*
4. **Product name** — Montserrat 700, navy, largest. Wraps for multi-ingredient.
5. **Dose** — Montserrat 600, navy, ~0.6× name size. UPPERCASE units (`10MG`, `0.5MG + 5MG`, `7% + 0.1%`).
6. **Form** — Montserrat 600, muted grey, ~0.35× name, letter-spaced (`CAPSULES`, `SPRAY`, `FOAM`, `SC`).

**NOT on front:** pack-size / count (e.g. "30 capsules") — that belongs on the BACK.

## BACK label hierarchy (from ref5 — phase 2)
`[badge]` → **Name** + `DOSE FORM` subhead → bold section headers + body:
- **Each capsule contains:** <active ingredients>
- **Suggested Use:** <directions>
- **Store:** <storage + keep out of reach>
- divider (cyan) → **Manufactured for:** Regenic Lab address · Batch No. / Mfg / Exp
Headers = Montserrat 700 navy; body = Montserrat 400 navy/grey.

## Layout geometry
Per-template label zone {x, y_top, y_bottom, w} lives in `config/templates.json`,
calibrated to each blank's baked logo position + label panel. Name+dose anchored
high (below logo); form anchored to a consistent baseline. NO displacement warp —
flat composite onto the photoreal blank.

## Per-type packaging map
| T | Container | Ref | Blank |
|---|-----------|-----|-------|
| T2 | capsule bottle | ✅ 17.39.02 | ✅ |
| T3 | amber spray | ✅ 17.24.07 | ✅ |
| T4 | foam canister | ✅ 17.24.08 | ✅ |
| T5 | peptide pen (navy case) | ✅ 17.24.10 | ✅ |
| T6 | glass vial | — | ✅ |
| T7 | cartridge pen | ❌ | ❌ |
| T8 | blister syringe | ✅ 17.24.09 | ❌ (ref only) |
| T9 | airless pump jar | ❌ | ❌ |
| T10 | amber dropper | ❌ | ❌ (flat only) |
