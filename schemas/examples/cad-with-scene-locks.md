# Demo Product — CAD + scene extraction

**Product reference:** `reference/product-hero.png`  
**Plate reference:** `reference/plate-plan.png`

---

## §1 Product geometry (object truth)

- Compact rectangular hardware token, satin-white body
- Flat connector blade with exposed contact pads
- Brushed-metal NFC disc centred on the face

---

## §2 Scene lock — floating hero (`target-hero.png`)

| Invariant | Value |
|-----------|--------|
| Background | Near-black studio sweep |
| Pose | Floating, slight tilt, no surface contact |
| Camera | Three-quarter hero, shallow DOF |
| Lighting | Rim light on top edge, soft reflection below |

---

## §3 Run invariants (all jobs)

- Preserve exact object geometry from product reference
- No props, no hands, no desk surface
- Razor-sharp product focus edge to edge
