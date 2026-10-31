"""CS-B4 UI + hygiene checks.

* generated TS contract types are current (no handwritten second schema);
* studio UI pure logic (commands/inverses, projections, geometry) via node;
* UI typecheck + production build (Accept);
* product law: no acceptance-set/client names in CS-B4 shipped files;
* studio UI never calls /api/execute, jobs_root or /media?path.
Requires `npm ci` in ui/app (node_modules) for the node/tsc/vite checks.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / "ui" / "app"
STUDIO = UI / "src" / "studio"
B4_SHIPPED = [ROOT / "service" / "composition", STUDIO,
              ROOT / "supabase" / "migrations" / "20261031140400_cs_b4_composition_exports.sql",
              ROOT / "tests" / "creative_studio" / "b4_fixtures"]
# One literal list (B1's copy of marketing-os lint_unseen_leakage.test.ts); imported so this file never holds the
# literals itself. Legacy-source words are assembled at runtime for the same reason.
from tests.creative_studio.test_cs_b1_unit import FORBIDDEN_LITERALS  # noqa: E402

FORBIDDEN = FORBIDDEN_LITERALS + ["re" + "genic", "ara" + "gon"]


def _files(paths, exts=(".py", ".ts", ".tsx", ".sql", ".json", ".css", ".md")):
    for p in paths:
        if p.is_file():
            yield p
        else:
            yield from (f for f in p.rglob("*") if f.is_file() and f.suffix in exts and "__pycache__" not in f.parts)


class UiAndHygiene(unittest.TestCase):
    def test_generated_types_are_current(self):
        out = subprocess.run([sys.executable, "-m", "service.composition.typegen", "--check"], cwd=ROOT,
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_no_leakage_in_b4_files(self):
        hits = []
        for f in _files(B4_SHIPPED):
            text = f.read_text(encoding="utf-8", errors="ignore").lower()
            hits += [(str(f.relative_to(ROOT)), w) for w in FORBIDDEN if w in text]
        self.assertEqual(hits, [])

    def test_studio_ui_has_no_legacy_execution_paths(self):
        bad = []
        for f in _files([STUDIO], (".ts", ".tsx")):
            text = f.read_text(encoding="utf-8")
            bad += [(f.name, s) for s in ("/api/execute", "jobs_root", "/media?path", "service_role") if s in text]
        self.assertEqual(bad, [])

    def test_ui_pure_logic_with_node(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = [str(STUDIO / f) for f in ("recipeCommands.ts", "recipeProjection.ts", "composition/geometry.ts",
                                               "contracts/types.gen.ts")]
            tsc = subprocess.run([str(UI / "node_modules" / ".bin" / "tsc"), "--outDir", tmp, "--rootDir", str(STUDIO),
                                  "--module", "es2022", "--target", "es2022", "--moduleResolution", "bundler",
                                  "--strict", "--skipLibCheck", *files], capture_output=True, text=True)
            self.assertEqual(tsc.returncode, 0, tsc.stdout + tsc.stderr)
            Path(tmp, "package.json").write_text(json.dumps({"type": "module"}))
            run = subprocess.run(["node", str(Path(__file__).parent / "b4_ui" / "logic.test.mjs"), tmp],
                                 capture_output=True, text=True)
            self.assertEqual((run.returncode, run.stdout.strip()), (0, "ok"), run.stderr)

    def test_ui_typecheck_and_build(self):
        info = UI / "tsconfig.tsbuildinfo"
        before = info.read_bytes() if info.exists() else None
        try:
            tsc = subprocess.run([str(UI / "node_modules" / ".bin" / "tsc"), "-b", "--force"], cwd=UI,
                                 capture_output=True, text=True)
            self.assertEqual(tsc.returncode, 0, tsc.stdout + tsc.stderr)
        finally:
            if before is not None:
                info.write_bytes(before)   # tracked build artefact; leave the tree unchanged
        with tempfile.TemporaryDirectory() as tmp:
            build = subprocess.run([str(UI / "node_modules" / ".bin" / "vite"), "build", "--outDir", tmp,
                                    "--emptyOutDir"], cwd=UI, capture_output=True, text=True)
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            self.assertTrue(Path(tmp, "index.html").exists())


if __name__ == "__main__":
    unittest.main()
