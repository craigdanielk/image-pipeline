"""Creative Studio exact composition (CS-B4 / NS-10801).

Deterministic layer renderer (exact generated image + exact brand logo file +
exact copy in the brand package's own font bytes), export-v1 manifests and
mos-handoff-v1 payloads. Contracts in docs/creative-studio/contracts are law;
this package validates against those bundled schema files and never defines a
second schema.
"""
from .errors import CompositionError  # noqa: F401
