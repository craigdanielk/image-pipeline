# USB-A PrimiKey™ — Engineering-Grade CAD Extraction (object model)

**Purpose:** capture the *physical object* — dimensions, proportions, materials,
and 3D geometry — so any render is anchored to a real, buildable model. This is
the FEEDER (`spec.cad`), NOT a lighting/photo reference.

**Source:** `reference/primikey-usba-target.png` (held in hand, USB-A to the right).
**Archetype:** USB-**A** contactless (NFC) hardware security key, YubiKey 5 NFC
(USB-A)-class form factor. **Sibling of the USB-C Toughkey** — same family; the
deltas are (a) **white body**, (b) **USB-A flat-blade** connector, (c) a slightly
longer/thinner nose.
**Scale anchor:** USB-A contact blade width = **≈ 12.0 mm** (standard USB-A tongue).
All other dimensions derived proportionally from this anchor + the archetype;
perspective foreshortening corrected to orthographic. Tolerance: **±0.5 mm**.

---

## 1. Overall envelope (orthographic)

| Axis | Feature | Dimension |
|------|---------|-----------|
| X (long) | full length, keyring end → USB-A tip | **≈ 45.0 mm** |
| X | body slab only (excl. connector blade) | **≈ 33.0 mm** |
| X | USB-A flat blade protrusion | **≈ 12.0 mm** |
| Y (width) | body width | **18.0 mm** |
| Y | USB-A blade width | **≈ 12.0 mm** |
| Z (thickness) | body thickness | **3.35 mm** |
| Z | USB-A blade thickness | **≈ 2.0 mm** |
| — | mass (archetype) | ≈ 3 g |

Coordinate frame: **X** = long axis (loop→connector), **Y** = width, **Z** =
thickness. Mirror-symmetric about the X–Z plane; NFC disc and blade centered on Y.

## 2. Body slab

- **Form:** monolithic rounded-rectangle slab (stadium-ended). Keyring end fully
  radiused; corner radius ≈ **9.0 mm** (= Y/2).
- **Cross-section (Y–Z):** flat faces, **full-radius long edges** (edge radius
  ≈ 1.6 mm ≈ Z/2) → racetrack section. No sharp edges.
- **Material:** **satin / matte WHITE** molded glass-fibre nylon (PA-GF), smooth
  low-sheen (NOT gloss piano-white, NOT dead-flat chalk). Colour ≈ #f2f2f0.
- **Draft:** generous fillets, overmolded, seamless show faces.

## 3. Keyring loop (far end, -X)

- **Type:** elongated **through-hole slot molded into the slab** (part of the body,
  NOT a separate wire ring).
- **Aperture:** stadium/oblong ≈ **10.0 mm (X) × 4.5 mm (Y)**, radiused ends.
- **Wall:** ≈ 2.0 mm outboard; slot passes through full Z, walls radiused.

## 4. Face feature between loop and NFC disc (+X of loop)

- A **single small debossed rounded-rectangle / stadium mark** ≈ 4 × 2 mm on the
  top face, same white as the body (tactile, not printed), on the centerline,
  depth ≈ 0.3 mm. (Fewer face marks than the USB-C Toughkey, which has two.)

## 5. NFC contact disc (center face)

- **Location:** centered on Y, mid-body (≈ 19–20 mm from the -X keyring end).
- **Diameter:** **Ø ≈ 8.5 mm**.
- **Seat:** shallow **circular counterbore**, recess ≈ 0.3 mm, thin bezel (≈ 0.5 mm).
- **Material:** **brushed brass / gold** contactless touch pad; concentric brush
  grain; slight dome. Strong gold-on-white contrast.
- **Glyph:** embossed **contactless "wave"** = three concentric arcs from the
  lower-left, dark on gold.

## 6. USB-A connector (+X end) — FLAT BLADE

- **Type:** **exposed flat USB-A contact blade** (reversible-style), NOT a full
  metal USB-A shell. The white body extends into a thin tongue.
- **Blade:** ≈ **12.0 mm W × 2.0 mm thick**, protruding ≈ 12 mm, corners eased.
- **Contacts:** **four gold-plated rectangular pads** in a row on the TOP face of
  the blade, set in a dark recessed window; pads ≈ 2 mm × 5 mm, evenly spaced.
- **Underside:** plain white; the pad window is on one face only.
- Blade centered on Y; its top face is flush-ish with the slab top plane.

## 7. 3D-model build notes (for a CAD / DCC rebuild)

1. Plan outline: 33 × 18 mm racetrack; extrude Z = 3.35 mm.
2. Full edge-radius (≈1.6 mm) on both long edges → racetrack section.
3. Cut keyring stadium slot (10.0 × 4.5 mm) at the -X end; radius bore walls.
4. Counterbore Ø8.5 mm × 0.3 mm at disc location; inset brass disc + 0.5 mm bezel.
5. Deboss the wave glyph on the disc + the single stadium mark on the face.
6. Extend the +X end into a 12 × 2 mm flat blade; cut a recessed window; inset
   four gold contact pads on the top face.
7. Materials: body PA-GF satin WHITE (#f2f2f0); disc brushed brass; pads gold.

## 8. Fixed invariants (must not drift in any render)

- Body = **satin WHITE**, never black, never glossy piano-white.
- Keyring = a **molded slot through the body**, never a separate wire ring.
- NFC disc = **brass/gold**, single disc, three-arc wave, centered on width.
- Connector = **USB-A FLAT BLADE with four exposed gold pads**, never USB-C,
  never a boxed metal USB-A shell, never micro/mini.
- One debossed face mark (not two).
- Proportions: length:width ≈ **2.5 : 1**; disc Ø ≈ **0.47 × body width**.

## 9. Canonical reference plates (on-model truth — generated 2026-07-01)

Clean studio renders derived from §1–8 + the object reference photo, promoted as
the object's canonical multi-angle reference set. Stage-2 scenario jobs pass
these as `input_images` (nano_banana multi-ref) so every scene stays on-model.

| Plate | View | File | Fidelity |
|-------|------|------|----------|
| 1 | Plan / top face (all features square to camera) | `reference/plates/plate-1-plan.png` | excellent |
| 2 | Hero three-quarter (blade up-right) | `reference/plates/plate-2-hero34.png` | excellent — minor: face mark rendered slightly logo-like; CAD §4 (single plain stadium mark) authoritative |
| 3 | Side profile (edge / thickness) | `reference/plates/plate-3-profile.png` | usable — thickness rendered ~2× spec; treat §1 (3.35 mm) as authoritative over this plate |

Source object photo: `reference/primikey-usba-target.png`. Engine: higgsfield
`nano_banana_2`, reference-driven, neutral studio (no rim/drama).
