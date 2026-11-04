"""CS-B4 export (cs-export-v2; v1 still readable) + mos-handoff-v1 (Accept: export -> mos-handoff payload validates).

CS-INT F4: export-v2 adds the 16:9 preset landscape_1920x1080; the 16:9 export is golden-checked."""
from __future__ import annotations

import copy
import dataclasses
import json
import subprocess
import sys
import unittest
import uuid

from service.composition.canonical import artifact_sha256
from service.composition.contracts import errors as schema_errors
from service.composition.errors import CompositionError
from service.composition.export import MemoryExportStore, build_export
from service.composition.handoff import build_handoff, export_ref, verify_ingestion_receipt
from service.composition.renderer import render_artboard
from tests.creative_studio.b4_support import FIXTURES, GOLDENS, World

FORMATS = {
    "square": {"id": "feed-square", "kind": "image", "preset": "square_1080", "width_px": 1080, "height_px": 1080,
               "mime_type": "image/png", "duration_frames": None, "frame_rate": None, "count": 1,
               "channel": "paid_social"},
    "portrait": {"id": "feed-portrait", "kind": "image", "preset": "portrait_1080x1350", "width_px": 1080,
                 "height_px": 1350, "mime_type": "image/png", "duration_frames": None, "frame_rate": None,
                 "count": 1, "channel": "paid_social"},
    "story": {"id": "story", "kind": "image", "preset": "story_1080x1920", "width_px": 1080, "height_px": 1920,
              "mime_type": "image/png", "duration_frames": None, "frame_rate": None, "count": 1,
              "channel": "paid_social"},
    "landscape": {"id": "landscape", "kind": "image", "preset": "landscape_1920x1080", "width_px": 1920,
                  "height_px": 1080, "mime_type": "image/png", "duration_frames": None, "frame_rate": None,
                  "count": 1, "channel": "paid_social"},
}
CREATED = "2026-10-31T10:00:00Z"


class ExportAndHandoff(unittest.TestCase):
    def setUp(self):
        self.w = World()
        self.store = MemoryExportStore()
        self.renders = {b: render_artboard(self.w.composition, b, brand_package=self.w.package, assets=self.w.source,
                                           safe_zone=self.w.zone(b)) for b in ("square", "portrait", "story",
                                                                               "landscape")}

    def export(self, board="square", **over):
        args = dict(composition=self.w.composition, brand_package=self.w.package, fmt=FORMATS.get(board),
                    store=self.store, source_records=list(self.w.records.values()),
                    generated_sources=self.w.lineage["generated_sources"], lineage=self.w.lineage,
                    copy_sources=self.w.copy_sources, cost=self.w.lineage["cost"], created_by=self.w.ids["actor"],
                    created_at=CREATED)
        args.update(over)
        return build_export(self.renders[board], **args)

    def refuse(self, code, fn):
        with self.assertRaises(CompositionError) as cm:
            fn()
        self.assertEqual(cm.exception.code, code, cm.exception.message)
        return cm.exception

    # -- export --
    def test_export_manifest_validates_and_binds_exact_versions(self):
        e = self.export()
        self.assertEqual(e["schema_version"], "cs-export-v2")
        self.assertEqual(schema_errors(e, "export-v2.schema.json"), [])
        self.assertEqual(e["sha256"], artifact_sha256(e))
        self.assertEqual(e["file"]["ref"]["sha256"], self.renders["square"].sha256)
        self.assertEqual(self.store.read(e["file"]["ref"]), self.renders["square"].data)
        lin = e["lineage"]
        self.assertEqual(lin["composition"]["version_id"], self.w.composition["version_id"])
        self.assertEqual(lin["brand_package"]["sha256"], self.w.package["sha256"])
        self.assertEqual(lin["render_engine_version"], self.renders["square"].renderer_version)
        self.assertEqual(lin["font_asset_sha256s"], [self.w.records["font"]["ref"]["sha256"]])
        self.assertEqual([g["asset"]["sha256"] for g in lin["generated_sources"]],
                         [self.w.records["scene"]["ref"]["sha256"]])
        self.assertEqual({a["sha256"] for a in lin["other_assets"]},
                         {self.w.records["logo"]["ref"]["sha256"], self.w.records["font"]["ref"]["sha256"]})
        self.assertEqual([(c["copy_id"], c["source_copy"]["version_id"]) for c in e["exact_copy"]],
                         [("headline", self.w.target["copy_versions"][0]["version_id"]),
                          ("cta", self.w.target["copy_versions"][1]["version_id"])])
        self.assertEqual(e["cost"]["state"], "unknown")             # never rewritten to zero
        self.assertEqual(e["cost"]["unresolved_micros"], 150000)
        self.assertTrue(e["requires_downstream_review"])
        self.assertEqual(e["rights_summary"]["status"], "permitted")
        self.assertEqual(len(e["rights_summary"]["receipt_ids"]), 3)

    def test_export_is_reproducible(self):
        self.assertEqual(self.export(), self.export())

    # -- 16:9 (CS-INT F4: export-v2 preset landscape_1920x1080) --
    def test_16x9_export_matches_golden_and_validates(self):
        golden = json.loads((GOLDENS / "goldens.json").read_text())["artboards"]["landscape"]
        e = self.export("landscape")
        self.assertEqual(schema_errors(e, "export-v2.schema.json"), [])
        self.assertEqual(e["format"]["preset"], "landscape_1920x1080")
        self.assertEqual((e["file"]["width_px"], e["file"]["height_px"]), (1920, 1080))
        self.assertEqual(e["file"]["ref"]["sha256"], golden["sha256"])          # golden bytes, read back from store
        self.assertEqual(self.store.read(e["file"]["ref"]), (GOLDENS / "landscape.png").read_bytes())
        self.assertEqual(e, self.export("landscape"))                             # reproducible
        h = self.handoff([self.export(b) for b in ("square", "portrait", "story", "landscape")])
        self.assertEqual(schema_errors(h, "mos-handoff-v1.schema.json"), [])

    def test_16x9_is_a_v2_only_preset(self):
        """The bump is real: export-v1 cannot express 16:9, and v2 differs from v1 only in format (and in allowing an
        empty exact_copy for Marketing OS image-library exports, which carry no campaign copy)."""
        e = self.export("landscape")
        self.assertNotEqual(schema_errors(dict(e, schema_version="cs-export-v1"), "export-v1.schema.json"), [])
        v1_square = dict(self.export("square"), schema_version="cs-export-v1")
        self.assertEqual(schema_errors(v1_square, "export-v1.schema.json"), [])
        root = FIXTURES.parents[2] / "docs" / "creative-studio" / "contracts"
        v1 = json.loads((root / "export-v1.schema.json").read_text())
        v2 = json.loads((root / "export-v2.schema.json").read_text())
        self.assertEqual({k for k in v1["properties"] if v1["properties"][k] != v2["properties"][k]},
                         {"schema_version", "format", "exact_copy"})
        self.assertEqual({k: v for k, v in v2["properties"]["exact_copy"].items() if k not in ("minItems", "$comment")},
                         {k: v for k, v in v1["properties"]["exact_copy"].items() if k != "minItems"})
        common = json.loads((root / "common.schema.json").read_text())["$defs"]
        self.assertNotIn("landscape_1920x1080", common["format"]["properties"]["preset"]["enum"])   # request/plan v1

    def test_export_refuses_sizes_without_a_preset(self):
        odd = dataclasses.replace(self.renders["square"], width=1200, height=1200)
        e = self.refuse("unsupported_export_preset", lambda: build_export(odd, **self._args("square")))
        self.assertIn("1200x1200", e.message)
        self.refuse("format_mismatch", lambda: self.export("landscape", fmt=FORMATS["square"]))
        self.refuse("format_mismatch", lambda: self.export("square", fmt=FORMATS["landscape"]))
        self.refuse("unsupported_export_preset", lambda: self.export("square", fmt=dict(FORMATS["square"],
                                                                                        preset="landscape_1080")))

    def _args(self, board):
        return dict(composition=self.w.composition, brand_package=self.w.package, fmt=FORMATS[board],
                    store=self.store, source_records=list(self.w.records.values()),
                    generated_sources=self.w.lineage["generated_sources"], lineage=self.w.lineage,
                    copy_sources=self.w.copy_sources, cost=self.w.lineage["cost"], created_by=self.w.ids["actor"],
                    created_at=CREATED)

    def test_format_mismatch(self):
        self.refuse("format_mismatch", lambda: self.export("square", fmt=FORMATS["portrait"] | {
            "preset": "square_1080", "width_px": 1080, "height_px": 1080, "mime_type": "image/jpeg"}))
        self.refuse("format_mismatch", lambda: self.export("square", fmt=FORMATS["portrait"]))

    def test_rights_must_allow_embedding_on_channel(self):
        records = copy.deepcopy(self.w.records)
        records["font"]["rights"]["allowed_uses"] = ["reference"]
        self.refuse("rights_denied", lambda: self.export(source_records=list(records.values())))
        records = copy.deepcopy(self.w.records)
        records["logo"]["rights"]["status"] = "unknown"
        self.refuse("rights_denied", lambda: self.export(source_records=list(records.values())))
        records = copy.deepcopy(self.w.records)
        records["scene"]["rights"]["channels"] = ["email"]
        self.refuse("rights_denied", lambda: self.export(source_records=list(records.values())))
        records = copy.deepcopy(self.w.records)
        records["scene"]["rights"]["expires_at"] = "2026-10-01T00:00:00Z"
        self.refuse("rights_denied", lambda: self.export(source_records=list(records.values())))

    def test_library_export_checks_rights_but_not_publication_channel(self):
        """An image-library export is stored, not published: a logo licensed for other channels is fine here, while
        unknown status, a use other than embed, or expiry still refuse (the channel is checked at placement)."""
        from service.composition.export import _rights_check, LIBRARY_CHANNEL
        from datetime import datetime, timezone
        at = datetime(2026, 11, 5, tzinfo=timezone.utc)
        rec = copy.deepcopy(self.w.records["logo"])
        rec["rights"]["channels"] = ["facebook", "instagram"]
        rec["rights"]["expires_at"] = None
        rec["rights"]["valid_from"] = "2026-01-01T00:00:00Z"
        _rights_check(rec, "embed", None, at, "logo")                     # library: channel scope deferred
        with self.assertRaises(Exception):
            _rights_check(rec, "embed", "email", at, "logo")              # a publication channel still checked
        rec["rights"]["status"] = "unknown"
        with self.assertRaises(Exception):
            _rights_check(rec, "embed", None, at, "logo")
        self.assertEqual(LIBRARY_CHANNEL, "library")

    def test_provider_image_needs_attempt_lineage(self):
        self.refuse("source_lineage_missing", lambda: self.export(generated_sources=[]))
        self.refuse("source_lineage_missing", lambda: self.export(source_records=[self.w.records["logo"]]))

    def test_unbound_copy_and_stale_render(self):
        def unbound():
            comp = self.w.edited(lambda d: self.w.layer(d, "square", "headline")["content"].update(copy_id=None))
            r = render_artboard(comp, "square", brand_package=self.w.package, assets=self.w.source,
                                safe_zone=self.w.zone("square"))
            return build_export(r, composition=comp, brand_package=self.w.package, fmt=FORMATS["square"],
                                store=self.store, source_records=list(self.w.records.values()),
                                generated_sources=self.w.lineage["generated_sources"], lineage=self.w.lineage,
                                copy_sources=self.w.copy_sources, cost=self.w.lineage["cost"],
                                created_by=self.w.ids["actor"])
        self.refuse("copy_id_missing", unbound)
        newer = self.w.edited(lambda d: None)
        self.refuse("source_revision_changed", lambda: self.export(composition=newer))

    def test_readback_mismatch_is_refused(self):
        class CorruptStore(MemoryExportStore):
            def read(self, ref):
                return b"not the bytes"
        e = self.refuse("export_readback_mismatch", lambda: self.export(store=CorruptStore()))
        self.assertTrue(e.retryable)

    # -- handoff --
    def handoff(self, exports, **over):
        args = dict(target=self.w.target, creative_request=self.w.lineage["request"],
                    idempotency_key=str(uuid.uuid5(uuid.NAMESPACE_URL, "b4-handoff-key")),
                    created_by=self.w.ids["actor"], created_at=CREATED)
        args.update(over)
        return build_handoff(exports, **args)

    def test_export_to_handoff_validates_against_schema(self):
        exports = [self.export(b) for b in ("square", "portrait", "story")]
        h = self.handoff(exports)
        self.assertEqual(schema_errors(h, "mos-handoff-v1.schema.json"), [])
        self.assertEqual(h["sha256"], artifact_sha256(h))
        self.assertEqual([r["version_id"] for r in h["exports"]], [e["version_id"] for e in exports])
        self.assertEqual([r["sha256"] for r in h["exports"]], [e["sha256"] for e in exports])
        self.assertTrue(h["requires_mos_review"])
        # three exports of one plan share one cost: counted once, still unknown
        self.assertEqual(h["cost"], self.w.lineage["cost"])
        self.assertEqual(h, self.handoff(exports))  # same key + body => same document

    def test_handoff_refusals(self):
        e = self.export()
        target = copy.deepcopy(self.w.target)
        target["copy_versions"] = target["copy_versions"][:1]
        self.refuse("copy_revision_mismatch", lambda: self.handoff([e], target=target))
        other_request = dict(self.w.lineage["request"], version_id=str(uuid.uuid4()))
        self.refuse("source_revision_changed", lambda: self.handoff([e], creative_request=other_request))
        tampered = copy.deepcopy(e)
        tampered["exact_copy"][0]["text"] = "Fast, friendly home repairs!"
        self.refuse("artifact_hash_mismatch", lambda: self.handoff([tampered]))
        self.refuse("invalid_mos_handoff", lambda: self.handoff([e, e]))
        self.refuse("invalid_mos_handoff", lambda: self.handoff([]))
        future = dict(e, schema_version="cs-export-v9")
        future["sha256"] = artifact_sha256(future)
        self.refuse("unsupported_schema_version", lambda: self.handoff([future]))

    def test_handoff_still_reads_v1_exports(self):
        """Manifests issued as cs-export-v1 before the amendment are handed off unchanged, never rewritten."""
        v1 = dict(self.export("square"), schema_version="cs-export-v1")
        v1["sha256"] = artifact_sha256(v1)
        self.assertEqual(schema_errors(v1, "export-v1.schema.json"), [])
        h = self.handoff([v1, self.export("landscape")])
        self.assertEqual(schema_errors(h, "mos-handoff-v1.schema.json"), [])
        self.assertEqual(h["exports"][0]["sha256"], v1["sha256"])

    def receipt_for(self, h, exports, **over):
        mos_ws = self.w.target["workspace_id"]
        items = []
        for e in exports:
            items.append({"export": export_ref(e), "export_asset": e["file"]["ref"],
                          "mos_asset_version": {"authority": "marketing-os", "workspace_id": mos_ws,
                                                "kind": "asset_version", "id": str(uuid.uuid5(uuid.NAMESPACE_URL, e["id"])),
                                                "version_id": str(uuid.uuid5(uuid.NAMESPACE_URL, e["version_id"])),
                                                "revision": 1, "sha256": e["file"]["ref"]["sha256"]},
                          "stored_bytes_sha256": e["file"]["ref"]["sha256"],
                          "mos_rights_receipt_ids": [str(uuid.uuid5(uuid.NAMESPACE_URL, "mos-rights"))],
                          "mos_copy_versions": self.w.target["copy_versions"]})
        r = {"schema_version": "cs-mos-ingestion-receipt-v1", "id": str(uuid.uuid5(uuid.NAMESPACE_URL, h["id"])),
             "workspace_id": mos_ws, "created_at": CREATED, "created_by": self.w.ids["actor"], "sha256": "",
             "handoff_id": h["id"], "handoff_sha256": h["sha256"], "idempotency_key": h["idempotency_key"],
             "items": items, "review_status": "pending", "publication_eligible": False}
        r.update(over)
        r["sha256"] = artifact_sha256(r)
        return r

    def test_ingestion_receipt_binds_exact_bytes(self):
        exports = [self.export(b) for b in ("square", "story")]
        h = self.handoff(exports)
        verify_ingestion_receipt(self.receipt_for(h, exports), h, exports)
        partial = self.receipt_for(h, exports[:1])
        self.refuse("receipt_incomplete", lambda: verify_ingestion_receipt(partial, h, exports))
        bad = self.receipt_for(h, exports)
        bad["items"][0]["stored_bytes_sha256"] = "0" * 64
        bad["sha256"] = artifact_sha256(bad)
        self.refuse("asset_hash_mismatch", lambda: verify_ingestion_receipt(bad, h, exports))
        approved = self.receipt_for(h, exports, review_status="approved")
        self.refuse("invalid_mos_ingestion_receipt", lambda: verify_ingestion_receipt(approved, h, exports))

    def test_render_bytes_identical_in_fresh_process(self):
        code = ("import json,sys; from tests.creative_studio.b4_support import World; "
                "from service.composition.renderer import render_artboard; w=World(); "
                "print(render_artboard(w.composition,'story',brand_package=w.package,assets=w.source,"
                "safe_zone=w.zone('story')).sha256)")
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                             env={"PYTHONHASHSEED": "12345", "PATH": "/usr/bin:/bin"},
                             cwd=str(FIXTURES.parents[2]))
        self.assertEqual(out.stdout.strip(), self.renders["story"].sha256)


if __name__ == "__main__":
    unittest.main()
