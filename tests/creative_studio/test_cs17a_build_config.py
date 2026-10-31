"""CS-17a: Vercel build config must agree with the UI build and the pins."""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _effective(path: Path) -> list[str]:
    return [l.split("#")[0].strip() for l in path.read_text().splitlines() if l.split("#")[0].strip()]


class VercelBuildConfig(unittest.TestCase):
    def test_root_requirements_self_contained_and_equal_service(self):
        root = _effective(ROOT / "requirements.txt")
        self.assertFalse([l for l in root if l.startswith(("-r", "--requirement", "-c"))], "no includes")
        self.assertEqual(root, _effective(ROOT / "service/requirements.txt"))

    def test_render_pin_matches_readiness(self):
        root = _effective(ROOT / "requirements.txt")
        self.assertIn("Pillow==12.2.0", root)
        self.assertIn("--only-binary Pillow", root)
        api_src = (ROOT / "service/api.py").read_text()
        self.assertIn("pillow/12.2.0", api_src)

    def test_output_directory_matches_vite_outdir(self):
        out = json.loads((ROOT / "vercel.json").read_text())["outputDirectory"]
        vite = (ROOT / "ui/app/vite.config.ts").read_text()
        m = re.search(r'outDir:\s*"([^"]+)"', vite)
        self.assertTrue(m)
        self.assertEqual((ROOT / "ui/app" / m.group(1)).resolve(), (ROOT / out).resolve())

    def test_single_daily_cron_kept(self):
        self.assertEqual(len(json.loads((ROOT / "vercel.json").read_text())["crons"]), 1)
