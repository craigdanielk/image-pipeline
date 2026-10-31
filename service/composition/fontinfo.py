"""Minimal, dependency-free reader for the sfnt tables the renderer must check.

Exact-font policy (contract.md §5, §10): the renderer may only use the font
bytes pinned in the composition, must know which code points those bytes
cover (cmap) and which weight they are (OS/2 usWeightClass). No system font,
synthesized bold or substituted glyph is ever used.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from .errors import CompositionError

_SFNT_TAGS = (b"\x00\x01\x00\x00", b"OTTO", b"true")


@dataclass(frozen=True)
class FontInfo:
    weight_class: int
    family: str | None
    code_points: frozenset[int]

    def missing(self, text: str) -> list[str]:
        return sorted({f"U+{ord(ch):04X}" for ch in text if ch != "\n" and ord(ch) not in self.code_points})


def _tables(data: bytes) -> dict[bytes, tuple[int, int]]:
    if len(data) < 12 or data[:4] not in _SFNT_TAGS:
        raise CompositionError("font_format_unsupported",
                               "font asset is not a TrueType/OpenType (sfnt) file; collections and web formats "
                               "must be imported as an sfnt derivative")
    (num_tables,) = struct.unpack(">H", data[4:6])
    tables = {}
    for i in range(num_tables):
        rec = data[12 + 16 * i: 28 + 16 * i]
        if len(rec) < 16:
            raise CompositionError("font_format_unsupported", "truncated sfnt table directory")
        tag, _checksum, offset, length = struct.unpack(">4sIII", rec)
        if offset + length > len(data):
            raise CompositionError("font_format_unsupported", f"sfnt table {tag!r} out of range")
        tables[tag] = (offset, length)
    return tables


def _cmap(data: bytes, offset: int) -> frozenset[int]:
    (_version, count) = struct.unpack(">HH", data[offset:offset + 4])
    best = None
    for i in range(count):
        platform, encoding, sub = struct.unpack(">HHI", data[offset + 4 + 8 * i: offset + 12 + 8 * i])
        fmt = struct.unpack(">H", data[offset + sub: offset + sub + 2])[0]
        unicode_table = platform == 0 or (platform == 3 and encoding in (1, 10))
        if unicode_table and fmt in (4, 12) and (best is None or fmt > best[0]):
            best = (fmt, offset + sub)
    if best is None:
        raise CompositionError("font_format_unsupported", "font has no Unicode cmap (format 4 or 12)")
    fmt, base = best
    points: set[int] = set()
    if fmt == 4:
        seg_x2 = struct.unpack(">H", data[base + 6: base + 8])[0]
        seg = seg_x2 // 2
        ends = struct.unpack(f">{seg}H", data[base + 14: base + 14 + seg_x2])
        starts_at = base + 16 + seg_x2
        starts = struct.unpack(f">{seg}H", data[starts_at: starts_at + seg_x2])
        deltas = struct.unpack(f">{seg}h", data[starts_at + seg_x2: starts_at + 2 * seg_x2])
        ro_at = starts_at + 2 * seg_x2
        range_offsets = struct.unpack(f">{seg}H", data[ro_at: ro_at + seg_x2])
        for i in range(seg):
            for cp in range(starts[i], ends[i] + 1):
                if cp == 0xFFFF:
                    continue
                if range_offsets[i] == 0:
                    glyph = (cp + deltas[i]) & 0xFFFF
                else:
                    at = ro_at + 2 * i + range_offsets[i] + 2 * (cp - starts[i])
                    glyph = struct.unpack(">H", data[at: at + 2])[0]
                    if glyph:
                        glyph = (glyph + deltas[i]) & 0xFFFF
                if glyph:
                    points.add(cp)
    else:
        (n_groups,) = struct.unpack(">I", data[base + 12: base + 16])
        for g in range(n_groups):
            start, end, glyph = struct.unpack(">III", data[base + 16 + 12 * g: base + 28 + 12 * g])
            for cp in range(start, end + 1):
                if glyph + (cp - start):
                    points.add(cp)
    return frozenset(points)


def _family(data: bytes, offset: int) -> str | None:
    _fmt, count, string_offset = struct.unpack(">HHH", data[offset:offset + 6])
    found = {}
    for i in range(count):
        platform, encoding, _lang, name_id, length, str_off = struct.unpack(
            ">HHHHHH", data[offset + 6 + 12 * i: offset + 18 + 12 * i])
        if name_id not in (1, 16):
            continue
        raw = data[offset + string_offset + str_off: offset + string_offset + str_off + length]
        if platform in (0, 3):
            found.setdefault(name_id, raw.decode("utf-16-be", errors="replace"))
        elif platform == 1 and encoding == 0:
            found.setdefault(name_id, raw.decode("latin-1"))
    return found.get(16) or found.get(1)


def inspect(data: bytes) -> FontInfo:
    tables = _tables(data)
    if b"cmap" not in tables:
        raise CompositionError("font_format_unsupported", "font has no cmap table")
    if b"OS/2" not in tables:
        raise CompositionError("font_format_unsupported", "font has no OS/2 table; weight cannot be verified")
    os2_off, _ = tables[b"OS/2"]
    (weight,) = struct.unpack(">H", data[os2_off + 4: os2_off + 6])
    family = _family(data, tables[b"name"][0]) if b"name" in tables else None
    return FontInfo(weight_class=weight, family=family, code_points=_cmap(data, tables[b"cmap"][0]))
