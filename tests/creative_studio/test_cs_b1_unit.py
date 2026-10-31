"""CS-B1 unit tests (no database). SQL evidence lives in test_cs_b1_real_db.py."""
from __future__ import annotations

import ast
import io
import json
import re
import time
import unittest
import uuid
from pathlib import Path

import jwt

from service.auth import TokenVerifier
from service.config import Settings
from service.errors import ServiceError, from_db_error
from service.storage import StorageSigner
from service import worker as cs_worker

ROOT = Path(__file__).resolve().parents[2]
HOSTED_FILES = sorted((ROOT / "service").glob("*.py")) + [ROOT / "api" / "index.py", ROOT / "api" / "worker.py"]
NEW_FILES = HOSTED_FILES + sorted(p for p in (ROOT / "tests" / "creative_studio").glob("*.py")
                                  if p.name != "test_cs_b1_unit.py") + [   # this file holds the literal list
    ROOT / "supabase" / "migrations" / "20261030210000_cs_b1_platform_foundation.sql"]
# Same acceptance-set literal list as marketing-os tests/stations/lint_unseen_leakage.test.ts
FORBIDDEN_LITERALS = ["terminix", "posthog", "wealthfront", "staygenerator", "khanacademy", "peakdesign",
                      "stanleysteemer", "stanley steemer", "cotopaxi", "betterment", "citizenm"]

SECRET = "unit-test-secret-value-000000000000000"
ISS = "https://auth.example.test/auth/v1"


def settings(**over) -> Settings:
    base = dict(api_database_url="x", worker_database_url=None, environment_id="test", release_id="r",
                jwt_issuer=ISS, jwt_audience="authenticated", jwt_secret=SECRET, jwks_url=None,
                storage_url="https://proj.example.test/storage/v1", storage_anon_key="k",
                signed_download_seconds=300, download_grant_seconds=60, worker_secret=None,
                runtime_principal="cs-worker:test")
    base.update(over)
    return Settings(**base)


class FakeDbError(Exception):
    def __init__(self, pgcode, message):
        self.pgcode = pgcode
        self.diag = type("D", (), {"message_primary": message})()


class AuthTests(unittest.TestCase):
    def tok(self, **claims):
        base = {"sub": str(uuid.uuid4()), "role": "authenticated", "aud": "authenticated", "iss": ISS,
                "exp": int(time.time()) + 60}
        base.update(claims)
        return jwt.encode({k: v for k, v in base.items() if v is not None}, SECRET, algorithm="HS256")

    def test_valid_token_yields_minimal_db_claims(self):
        sub = str(uuid.uuid4())
        actor = TokenVerifier(settings()).verify("Bearer " + self.tok(sub=sub, user_metadata={"is_admin": True}))
        self.assertEqual(actor.user_id, sub)
        self.assertEqual(actor.db_claims(), {"sub": sub, "role": "authenticated", "aud": "authenticated"})

    def test_refusals(self):
        v = TokenVerifier(settings())
        for label, header in {"missing exp": "Bearer " + self.tok(exp=None),
                              "non-uuid sub": "Bearer " + self.tok(sub="admin"),
                              "basic auth": "Basic dXNlcjpwYXNz", "empty": ""}.items():
            with self.assertRaises(ServiceError, msg=label) as ctx:
                v.verify(header)
            self.assertEqual(ctx.exception.status, 401, label)


class ErrorMappingTests(unittest.TestCase):
    def test_sqlstates(self):
        for state, msg, expect in (("CS401", "authentication_required", (401, "authentication_required")),
                                   ("CS404", "resource_unavailable", (404, "resource_unavailable")),
                                   ("CS409", "lease_lost", (409, "lease_lost")),
                                   ("42501", "new row violates row-level security policy", (403, "capability_denied")),
                                   ("XX000", "boom", (500, "internal_error"))):
            err = from_db_error(FakeDbError(state, msg))
            self.assertEqual((err.status, err.code), expect, state)


class SignerTests(unittest.TestCase):
    def opener_returning(self, payload):
        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        return lambda req, timeout: Resp(json.dumps(payload).encode())

    def test_relative_signed_url_resolves_under_storage_host(self):
        s = StorageSigner(settings(), opener=self.opener_returning({"signedURL": "/object/sign/creative-studio/k?token=t"}))
        self.assertEqual(s.sign_download("u", "k", 30),
                         "https://proj.example.test/storage/v1/object/sign/creative-studio/k?token=t")

    def test_foreign_origin_url_is_never_returned(self):
        for bad in ("https://cdn.provider.example.test/out.png?sig=1", "https://proj.example.test.evil.test/x",
                    "//elsewhere.example.test/x"):
            s = StorageSigner(settings(), opener=self.opener_returning({"signedURL": bad}))
            with self.assertRaises(ServiceError, msg=bad) as ctx:
                s.sign_download("u", "k", 30)
            self.assertEqual(ctx.exception.status, 502)

    def test_ttl_bounds(self):
        s = StorageSigner(settings(), opener=self.opener_returning({"signedURL": "/x"}))
        for ttl in (0, 301):
            with self.assertRaises(ServiceError):
                s.sign_download("u", "k", ttl)


class WorkerTests(unittest.TestCase):
    def test_worker_without_database_refuses(self):
        with self.assertRaises(ServiceError) as ctx:
            cs_worker.run_once(settings(), {"compose_image": lambda i, c: None})
        self.assertEqual(ctx.exception.status, 503)

    def test_worker_without_secret_refuses_every_call(self):
        app = cs_worker.make_wsgi(lambda: settings())
        out = {}
        body = b"".join(app({"REQUEST_METHOD": "POST", "HTTP_AUTHORIZATION": "Bearer "},
                            lambda s, h: out.setdefault("s", s)))
        self.assertEqual((out["s"][:3], json.loads(body)), ("503", {"error": "worker_secret_unconfigured"}))

    def test_b1_ships_no_stage_handlers(self):
        self.assertEqual(cs_worker.HANDLERS, {})


class HostedSurfaceTests(unittest.TestCase):
    def test_hosted_code_never_imports_local_operator_modules(self):
        for path in HOSTED_FILES:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    names = [node.module]
                for name in names:
                    self.assertFalse(name.split(".")[0] in {"ui", "core", "pipeline", "tools", "subprocess"},
                                     f"{path.name} imports {name}")

    def test_no_local_path_or_tenant_selectors_in_hosted_code(self):
        """Executable code only: docstrings and comments are stripped via the AST."""
        pattern = re.compile(r"jobs_root|tenants\.json|load_tenants|/media\?path|FileResponse|\bopen\(", re.I)
        for path in HOSTED_FILES:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                body = getattr(node, "body", None)
                if isinstance(body, list) and body and isinstance(body[0], ast.Expr) \
                        and isinstance(getattr(body[0], "value", None), ast.Constant) \
                        and isinstance(body[0].value.value, str):
                    body.pop(0)
            code = ast.unparse(tree)
            self.assertIsNone(pattern.search(code), f"{path.name}: {pattern.search(code)}")

    def test_no_acceptance_set_names_in_new_files(self):
        for path in NEW_FILES:
            text = path.read_text(encoding="utf-8").lower()
            for literal in FORBIDDEN_LITERALS:
                self.assertNotIn(literal, text, f"{path.relative_to(ROOT)} contains {literal!r}")

    def test_higgsfield_adapters_untouched(self):
        import subprocess as sp
        out = sp.run(["git", "diff", "--name-only", "51e96fd", "--", "core/adapters/"], cwd=ROOT,
                     capture_output=True, text=True, check=True).stdout
        self.assertEqual(out.strip(), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
