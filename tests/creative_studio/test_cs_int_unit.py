"""CS-INT: integrated hosted API surface (no database)."""
from __future__ import annotations

import json
import re
import unittest
import uuid
from pathlib import Path

from service import api

ROOT = Path(__file__).resolve().parents[2]

_GROUP = re.compile(r"\(([^()]*)\)")


def _sample_path(pattern: str) -> str:
    """Fill each capture group with a value of its own shape (uuid or slug)."""
    def fill(m):
        return str(uuid.uuid4()) if "{36}" in m.group(1) else "n1"
    return _GROUP.sub(fill, pattern.removeprefix("^").removesuffix("$"))


class IntegratedRouteTable(unittest.TestCase):
    def test_every_domain_module_registered_once_in_order(self):
        self.assertEqual([m.__name__ for m in api.DOMAIN_ROUTE_MODULES],
                         ["service.brand.routes", "service.requests.routes", "service.runtime.routes",
                          "service.composition.routes"])
        names = [n for _, _, n in api._ROUTES]
        self.assertEqual(len(names), len(set(names)), "one handler per name")
        keys = [(m, p.pattern) for m, p, _ in api._ROUTES]
        self.assertEqual(len(keys), len(set(keys)), "one handler per method + pattern")
        for prefix in ("b2_", "cs_b3_", "cs_b4_"):
            self.assertTrue(any(n.startswith(prefix) for n in names), f"{prefix} routes registered")

    def test_every_route_resolves_to_its_own_handler(self):
        """No earlier pattern shadows a later route (dispatch is first-match per method)."""
        for method, pattern, name in api._ROUTES:
            path = _sample_path(pattern.pattern)
            self.assertTrue(pattern.match(path), (name, path))
            first = next(n for m, p, n in api._ROUTES if m == method and p.match(path))
            self.assertEqual(first, name, f"{method} {path} is shadowed by {first}")
            self.assertTrue(callable(getattr(api.CreativeStudioApi, name)), name)

    def test_b1_surface_not_replaced(self):
        for attr in ("dispatch", "_tx", "_require_read", "health", "my_workspaces", "list_runs", "get_run"):
            self.assertEqual(getattr(api.CreativeStudioApi, attr).__module__, "service.api", attr)

    def test_reason_phrases_for_domain_statuses(self):
        self.assertEqual(api._REASONS[202], "Accepted")
        self.assertEqual(api._REASONS[428], "Precondition Required")


class PinnedRuntime(unittest.TestCase):
    """F3: the environment the suite runs in is the one service/requirements.txt describes."""

    def requirement_lines(self):
        return [l.split("#")[0].strip() for l in (ROOT / "service/requirements.txt").read_text().splitlines()
                if l.split("#")[0].strip()]

    def test_render_stack_pin_matches_goldens_and_runtime(self):
        import PIL
        from service.composition.renderer import renderer_version
        pins = [l for l in self.requirement_lines() if l.lower().startswith("pillow")]
        self.assertEqual(len(pins), 1, pins)
        self.assertRegex(pins[0], r"^Pillow==\d+\.\d+\.\d+$", "render stack is pinned exactly")
        self.assertIn("--only-binary Pillow", self.requirement_lines())
        pinned = pins[0].split("==")[1]
        self.assertEqual(PIL.__version__, pinned, "installed Pillow != pinned Pillow")
        golden = json.loads((ROOT / "tests/creative_studio/b4_goldens/goldens.json").read_text())
        self.assertEqual(golden["renderer_version"], renderer_version())
        self.assertIn(f" pillow/{pinned} ", golden["renderer_version"])

    def test_contract_runtime_dependencies_declared(self):
        lines = " ".join(self.requirement_lines())
        for dep in ("jsonschema[format-nongpl]>=4.18", "referencing", "cryptography", "psycopg2-binary", "PyJWT"):
            self.assertIn(dep, lines)

    def test_contract_formats_are_enforced(self):
        """Without the format extras jsonschema silently accepts any string for these formats."""
        from jsonschema import FormatChecker
        fc = FormatChecker()
        for fmt, bad, good in (("date-time", "31/10/2026", "2026-10-31T07:00:00Z"),
                               ("uri", "not a uri", "https://example-studio.test/a"),
                               ("uuid", "nope", str(uuid.uuid4()))):
            self.assertFalse(fc.conforms(bad, fmt), fmt)
            self.assertTrue(fc.conforms(good, fmt), fmt)


class CanonicalArchitecturePackage(unittest.TestCase):
    """The accepted architecture document embeds every contract file; amendments keep it exact (F4)."""
    DOC = ROOT / "docs/creative-studio/canonical-architecture.md"

    def sections(self):
        text = self.DOC.read_text(encoding="utf-8")
        parts = re.split(r"^## Packaged file: (\S+)\n", text, flags=re.M)
        return dict(zip(parts[1::2], parts[2::2])), text

    def test_packaged_copies_equal_files(self):
        sections, _ = self.sections()
        on_disk = sorted(str(p.relative_to(ROOT / "docs/creative-studio"))
                         for p in (ROOT / "docs/creative-studio/contracts").iterdir() if p.is_file())
        self.assertEqual([n for n in on_disk if n not in sections], [], "every contract file is packaged")
        for name, body in sections.items():
            path = ROOT / "docs/creative-studio" / name
            if not path.exists():
                continue                      # package-only documents (README.md, ARCHITECTURE_EVIDENCE.md)
            if name.endswith(".json"):
                block = re.search(r"```json\n(.*?)\n```", body, re.S).group(1)
                self.assertEqual(json.loads(block), json.loads(path.read_text()), name)
            else:
                self.assertEqual(body.strip("\n"), path.read_text(encoding="utf-8").strip("\n"), name)

    def test_latest_manifest_hashes_match_files(self):
        import hashlib
        _, text = self.sections()
        latest = {}
        for block in re.findall(r"## Package (?:amendment )?manifest[^\n]*\n\n```json\n(.*?)\n```", text, re.S):
            for f in json.loads(block)["files"]:
                latest[f["path"]] = f["sha256"]                  # later manifests supersede earlier entries
        self.assertIn("contracts/export-v2.schema.json", latest)
        for rel, digest in latest.items():
            path = ROOT / "docs/creative-studio" / rel
            if path.exists():
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest, rel)


def jcs_corpus(seed: int = 8785, n: int = 400) -> list:
    """Fixed edge cases + seeded random documents inside the SQL canonicaliser's domain."""
    import random
    rnd = random.Random(seed)
    fixed = [0, 1, -1, 2.0, 0.5, -0.5, 1.5, 3.5, 0.1, 0.2, 0.30000000000000004, 0.0001, 0.00012, 0.001, 123.456,
             333333333.3333333, 99999999999999.9, 123456789012345.6, 9007199254740991, -9007199254740991,
             "", "plain", "quote\" back\\ slash/", "\b\f\n\r\t", "\u0001\u001f\u007f", "é 中 ü", "😀 astral",
             "\u2028\u2029", True, False, None, [], {}, [1, [2, [3, {}]]],
             {"b": 1, "a": 2, "A": 3, "é": 4, "中": 5, "aa": 6, "a_b": 7, "a-b": 8, "": 9},
             {"compile_hash": "0" * 64, "node_id": "n1", "inputs": [{"port": "ref", "asset": {"sha256": "f" * 64,
              "revision": 1}}], "provider_call": {"model": "m", "params": {"strength": 0.35, "seed": 42}}}]
    def value(depth):
        kind = rnd.choice(["int", "float", "str", "bool", "null", "arr", "obj"] if depth < 3 else
                          ["int", "float", "str", "bool", "null"])
        if kind == "int":
            return rnd.randint(-9007199254740991, 9007199254740991) if rnd.random() < .2 else rnd.randint(-1000, 1000)
        if kind == "float":
            mag = 10 ** rnd.uniform(-4, 14.9)
            return round(rnd.choice([-1, 1]) * mag, rnd.randint(0, 12)) or 0.5
        if kind == "str":
            return "".join(rnd.choice("aZ09 _-\"\\/\n\téüß中") for _ in range(rnd.randint(0, 8)))
        if kind == "bool":
            return rnd.random() < .5
        if kind == "null":
            return None
        if kind == "arr":
            return [value(depth + 1) for _ in range(rnd.randint(0, 4))]
        return {"".join(rnd.choice("abcAB_é中") for _ in range(rnd.randint(1, 4))): value(depth + 1)
                for _ in range(rnd.randint(0, 4))}
    return fixed + [value(0) for _ in range(n)]


class SingleCanonicalJson(unittest.TestCase):
    """CS-INT: one RFC 8785 implementation, checked against ECMAScript itself."""

    def test_modules_share_one_implementation(self):
        from service import jcs
        from service.composition import canonical as b4
        from service.providers import canonical as b3
        from service.requests import canonical as b2
        self.assertIs(b2.canonical_bytes, jcs.canonical_bytes)
        self.assertIs(b3.canonical, jcs.canonical_bytes)
        self.assertIs(b4.canonical_bytes, jcs.canonical_bytes)
        self.assertEqual(b3.canonical(1e-5), b"0.00001")      # CS-B3 used to emit 1e-5

    def test_python_matches_ecmascript_reference(self):
        import subprocess
        from service import jcs
        corpus = jcs_corpus()
        ref = subprocess.run(["node", str(ROOT / "tests/creative_studio/cs_int/jcs_reference.mjs")],
                             input=json.dumps(corpus), capture_output=True, text=True, check=True).stdout
        expected = json.loads(ref)
        for doc, want in zip(corpus, expected, strict=True):
            self.assertEqual(jcs.canonical_text(doc), want, doc)
        for extra in (1e-7, 1.5e-7, 1e-6, 1e-5, 1e21, 1.5e300, 5e-324, -0.0):     # outside the SQL domain
            want = subprocess.run(["node", "-e", f"process.stdout.write(JSON.stringify({extra!r}))"],
                                  capture_output=True, text=True, check=True).stdout
            self.assertEqual(jcs.canonical_text(extra), want, extra)


if __name__ == "__main__":
    unittest.main(verbosity=2)
