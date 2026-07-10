# Toughkey™ — Engineering-Grade CAD Extraction (object model)

**Purpose:** capture the *physical object* — dimensions, proportions, materials,
and 3D geometry — so any render is anchored to a real, buildable model. This is
the FEEDER (`spec.cad`), NOT a lighting/photo reference.

**Source:** `reference/toughkey-target.png` (3/4 perspective).
**Archetype:** USB-C contactless (NFC) hardware security key, YubiKey 5C NFC-class
form factor.
**Scale anchor:** USB-C Type-C plug shell = **8.34 mm W × 2.56 mm H** (fixed
industry standard). All other dimensions are derived proportionally from this
anchor and the archetype; perspective foreshortening in the source is corrected
to orthographic estimates. Tolerance on derived dims: **±0.5 mm** unless noted.

---

## 1. Overall envelope (orthographic)

| Axis | Feature | Dimension |
|------|---------|-----------|
| X (long) | full length, keyring end → USB-C tip | **44.8 mm** |
| X | body slab only (excl. connector + neck) | **≈ 35.8 mm** |
| X | USB-C plug + overmold neck protrusion | **≈ 9.0 mm** |
| Y (width) | body width | **18.3 mm** |
| Z (thickness) | body thickness | **3.35 mm** |
| — | mass (archetype) | ≈ 4.5–5 g |

Coordinate frame: **X** = long axis (loop→connector), **Y** = width, **Z** =
thickness. Object is **mirror-symmetric about the X–Z plane** (the long
centerline) in plan view; the NFC disc and connector are centered on Y.

## 2. Body slab

- **Form:** single monolithic rounded-rectangle slab (stadium-ended). Both short
  ends fully radiused; corner radius ≈ **9.15 mm** (= Y/2, i.e. semicircular ends).
- **Cross-section (Y–Z):** flat faces with a **full-radius edge** on both long
  sides → lozenge/racetrack section, edge radius ≈ 1.6 mm (≈ Z/2). No sharp edges.
- **Material:** matte **glass-fibre-reinforced nylon** (PA-GF), soft-touch
  micro-textured, non-glossy, fingerprint-resistant. Colour near-black (#0d0d0f).
- **Draft:** generous fillets throughout; overmolded, seamless (no visible parting
  line on the show faces).

## 3. Keyring loop (far end, -X)

- **Type:** elongated **through-hole slot molded into the slab** (part of the body,
  not a separate metal ring).
- **Aperture:** stadium/oblong ≈ **10.5 mm (X) × 5.0 mm (Y)**, radiused ends.
- **Wall:** ≈ 2.0–2.5 mm of body material surrounds the slot on the outboard end.
- **Through the full Z thickness**, walls radiused (no sharp bore edges).

## 4. Face features between loop and NFC disc (+X of loop)

Two small **molded, debossed** marks on the top face, same black as the body
(tactile, not printed), aligned on the centerline:

- **Upper:** a small **stadium / rounded-rectangle outline** ≈ 4 × 2 mm (recessed ring).
- **Lower:** a short **horizontal bar / slot** ≈ 4 × 1.5 mm.

Function: molded orientation / grip indices. Depth ≈ 0.3 mm.

## 5. NFC contact disc (center face)

- **Location:** centered on Y, offset toward mid-body (≈ 20–21 mm from the -X keyring end).
- **Diameter:** **Ø ≈ 9.0 mm**.
- **Seat:** shallow **circular counterbore**, recess depth ≈ 0.3 mm, with a thin
  black rim/bezel (≈ 0.5 mm) around the disc.
- **Material:** **brushed brass / gold-plated** contactless touch pad; concentric
  brush grain; slight dome.
- **Glyph:** embossed **contactless / NFC "wave" mark** = three concentric arcs
  radiating from the lower-left, dark on gold. Arc set spans ≈ 5 mm.

## 6. USB-C connector (+X end)

- **Plug shell:** nickel-plated **steel**, oval-cornered Type-C, **8.34 mm W ×
  2.56 mm H**, exposed length ≈ **6.5 mm**, brushed metal finish.
- **Tongue:** internal black plastic tongue visible in the plug cavity.
- **Neck/overmold:** short **black co-molded collar** (≈ 2.5 mm) fairing the plug
  into the slab; centered on Z (connector on the mid-thickness plane).

## 7. 3D-model build notes (for a CAD / DCC rebuild)

1. Sketch the plan outline: 35.8 × 18.3 mm racetrack; extrude Z = 3.35 mm.
2. Apply full edge-radius (≈1.6 mm) to both long edges → racetrack cross-section.
3. Cut the keyring stadium slot (10.5 × 5.0 mm) at the -X end; radius the bore walls.
4. Counterbore Ø9.0 mm × 0.3 mm at the disc location; inset the brass disc + 0.5 mm bezel.
5. Deboss the wave glyph on the disc and the two orientation marks on the face.
6. Co-mold the USB-C: 8.34 × 2.56 mm steel shell, 6.5 mm exposed, 2.5 mm neck,
   centered on Y and Z.
7. Materials: body PA-GF matte (#0d0d0f); disc brushed brass; connector nickel steel.

## 8. Fixed invariants (must not drift in any render)

- Keyring = a **molded slot through the body**, never a separate wire ring.
- NFC disc = **brass/gold**, single disc, three-arc wave, centered on width.
- Connector = **USB-C** (oval), never USB-A, never micro/mini.
- Body = **matte** textured black, never glossy piano-black.
- Proportions: length:width ≈ **2.45 : 1**; disc Ø ≈ **0.49 × body width**.

---

## 9. Canonical reference plates (on-model truth — generated 2026-07-01)

Clean studio renders derived from §1–8 + the object reference photo, promoted as
the object's canonical multi-angle reference set. Stage-2 scenario jobs pass
these as `input_images` (nano_banana multi-ref) so every scene stays on-model.

| Plate | View | File | Fidelity |
|-------|------|------|----------|
| 1 | Plan / top face (all features square to camera) | `reference/plates/plate-1-plan.png` | excellent |
| 2 | Hero three-quarter (connector up-right) | `reference/plates/plate-2-hero34.png` | excellent |
| 3 | Side profile (edge / thickness) | `reference/plates/plate-3-profile.png` | usable — thickness rendered ~2× spec; treat §1 (3.35 mm) as authoritative over this plate |

Source object photo: `reference/toughkey-target.png`. Engine: higgsfield
`nano_banana_2`, reference-driven, neutral studio (no rim/drama).
