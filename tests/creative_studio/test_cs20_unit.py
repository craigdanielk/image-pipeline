"""CS-20 unit suite: Higgsfield account-login validation, refresh and verification (no network, no CLI)."""
from __future__ import annotations

import json
import os
import stat
import tempfile
import time
import unittest
import urllib.error
from types import SimpleNamespace
from unittest import mock

from service.providers import higgsfield_oauth as hl
from service.providers.adapter import engine_higgsfield as hf

SCOPE = "user:org:read email profile offline_access"


def login(**over):
    base = {"auth_version": 2, "access_token": "oat_" + "a" * 32, "refresh_token": "R" * 48,
            "expires_at": int(time.time()) + 86400, "token_type": "bearer", "scope": SCOPE}
    base.update(over)
    return base


class ParseLogin(unittest.TestCase):
    def test_accepts_cli_credentials_file_shape(self):
        self.assertEqual(hl.parse_login(json.dumps(login()))["auth_version"], 2)

    def test_refuses_api_keys_old_versions_and_unrefreshable_logins(self):
        bad = ["hf-api-key-1234567890", json.dumps(login(auth_version=1)), json.dumps(login(scope="email profile")),
               json.dumps(login(expires_at="soon")), json.dumps(login(expires_at=True)), json.dumps(login(extra=1)),
               json.dumps({k: v for k, v in login().items() if k != "refresh_token"}), json.dumps(login(access_token=" ")),
               "[]", "null"]
        for raw in bad:
            with self.assertRaises(hl.LoginError, msg=raw) as ctx:
                hl.parse_login(raw)
            self.assertEqual(ctx.exception.code, "invalid_login")

    def test_token_response_builds_v2_file(self):
        out = hl.login_from_token_response({"access_token": "oat_x" * 7, "refresh_token": "r" * 40, "expires_in": 86400,
                                            "token_type": "Bearer", "scope": SCOPE}, now=1000)
        self.assertEqual((out["auth_version"], out["expires_at"], out["token_type"]), (2, 87400, "bearer"))
        with self.assertRaises(hl.LoginError):
            hl.login_from_token_response({"access_token": "x", "expires_in": 5, "scope": SCOPE})   # no refresh token

    def test_needs_refresh_margin(self):
        self.assertFalse(hl.needs_refresh(login(expires_at=10_000), now=10_000 - 3601))
        self.assertTrue(hl.needs_refresh(login(expires_at=10_000), now=10_000 - 3599))


class Refresh(unittest.TestCase):
    def test_success_rotates_and_sends_named_user_agent_form(self):
        calls = []

        def post(url, form):
            calls.append((url, form))
            return 200, {"access_token": "oat_new" + "b" * 20, "refresh_token": "N" * 48, "expires_in": 86400,
                         "token_type": "bearer", "scope": SCOPE}
        out = hl.refresh(login(), client_id="cid", post=post, now=50)
        self.assertEqual((out["refresh_token"], out["expires_at"]), ("N" * 48, 86450))
        self.assertEqual(calls[0][0], hl.TOKEN_URL)
        self.assertEqual(calls[0][1], {"grant_type": "refresh_token", "refresh_token": "R" * 48, "client_id": "cid"})
        self.assertNotIn("Python-urllib", hl.USER_AGENT)

    def test_refused_grant_means_reconnect(self):
        for status, body in ((400, {"error": "invalid_grant"}), (401, {"error": "invalid_client"})):
            with self.assertRaises(hl.LoginError) as ctx:
                hl.refresh(login(), client_id="cid", post=lambda u, f, s=status, b=body: (s, b))
            self.assertEqual(ctx.exception.code, "reconnect_required")

    def test_transient_failures_keep_the_login(self):
        def boom(u, f):
            raise urllib.error.URLError("down")
        for post in (lambda u, f: (503, {}), lambda u, f: (403, {}), boom):
            with self.assertRaises(hl.LoginError) as ctx:
                hl.refresh(login(), client_id="cid", post=post)
            self.assertEqual(ctx.exception.code, "refresh_unavailable")

    def test_missing_client_id_is_unavailable_not_reconnect(self):
        with mock.patch.dict(os.environ, {hl.CLIENT_ID_ENV: ""}):
            with self.assertRaises(hl.LoginError) as ctx:
                hl.refresh(login(), post=lambda u, f: self.fail("must not call Higgsfield"))
        self.assertEqual(ctx.exception.code, "refresh_unavailable")


class AccountStatus(unittest.TestCase):
    def test_success_reads_credits_and_email_and_cleans_up_file(self):
        seen = {}

        def status(credential):
            path = credential.credentials_path
            seen.update(path=path, mode=stat.S_IMODE(os.stat(path).st_mode), body=open(path).read())
            return {"credits": 1149.25, "email": "a@b.test", "subscription_plan_type": "x"}
        out = hl.account_status(json.dumps(login()).encode(), status=status)
        self.assertEqual(out, {"credits": 1149.25, "email": "a@b.test"})
        self.assertEqual(seen["mode"], 0o600)
        self.assertEqual(json.loads(seen["body"])["auth_version"], 2)
        self.assertFalse(os.path.exists(seen["path"]))

    def test_rejected_unreadable_and_no_binary(self):
        def rejected(c):
            raise RuntimeError("higgsfield failed (2): Session expired.")

        def no_cli(c):
            raise hf.ProviderRefusal("provider_runtime_unavailable", "no CLI")
        for status, code in ((rejected, "login_rejected"), (lambda c: {"credits": 1}, "verification_unavailable"),
                             (lambda c: {"credits": True, "email": "a@b"}, "verification_unavailable"),
                             (no_cli, "provider_runtime_unavailable")):
            with self.assertRaises(hl.LoginError) as ctx:
                hl.account_status(b"{}", status=status)
            self.assertEqual(ctx.exception.code, code)

    def test_adapter_check_runs_account_status_with_the_login_env(self):
        calls = []

        def run(cmd, **kw):
            calls.append((cmd, kw["env"]["HIGGSFIELD_CREDENTIALS_PATH"], kw["env"]["HOME"]))
            return SimpleNamespace(returncode=0, stdout='{"credits": 3, "email": "x@y.z"}', stderr="")
        with tempfile.NamedTemporaryFile() as cred, mock.patch.object(hf, "cli_binary", lambda: "/opt/hf"), \
                mock.patch.object(hf, "subprocess", SimpleNamespace(run=run)):
            out = hf.account_status(hf.WorkspaceCredential(workspace_id="w", credential_id="c",
                                                           credentials_path=cred.name))
        self.assertEqual(out["credits"], 3)
        self.assertEqual(calls[0][0], ["/opt/hf", "account", "status", "--json"])
        self.assertEqual(calls[0][1:], (cred.name, os.path.dirname(cred.name)))


class Identity(unittest.TestCase):
    def test_fingerprint_is_per_account_and_matches_store_constraint(self):
        import re
        a = hl.account_fingerprint("Ashton@Trend.Digital ")
        self.assertEqual(a, hl.account_fingerprint("ashton@trend.digital"))
        self.assertNotEqual(a, hl.account_fingerprint("other@trend.digital"))
        self.assertRegex(a, re.compile(r"^[A-Za-z0-9_.:-]{1,128}$"))
        self.assertEqual(hl.masked_email("ashton@trend.digital"), "as***@trend.digital")


class CliBinary(unittest.TestCase):
    def test_env_binary_and_non_executable_bundle_copy(self):
        with tempfile.TemporaryDirectory() as d:
            exe = os.path.join(d, "hf")
            with open(exe, "wb") as fh:
                fh.write(b"#!/bin/sh\nexit 0\n")
            os.chmod(exe, 0o755)
            with mock.patch.dict(os.environ, {hf.CLI_BIN_ENV: exe}):
                self.assertEqual(hf.cli_binary(), exe)
            os.chmod(exe, 0o644)                                       # bundle lost its execute bit
            with mock.patch.dict(os.environ, {hf.CLI_BIN_ENV: exe}):
                copy = hf.cli_binary()
            self.assertNotEqual(copy, exe)
            self.assertTrue(os.access(copy, os.X_OK))
            with open(copy, "rb") as fh:
                self.assertEqual(fh.read(), b"#!/bin/sh\nexit 0\n")

    def test_missing_binary_refuses_before_dispatch(self):
        from service.providers import higgsfield
        with tempfile.NamedTemporaryFile() as cred, mock.patch.object(hf, "cli_binary", lambda: None):
            credential = hf.WorkspaceCredential(workspace_id="w", credential_id="c", credentials_path=cred.name)
            with self.assertRaises(hf.ProviderRefusal) as ctx:
                hf._cli_env(credential)
        self.assertEqual(ctx.exception.code, "provider_runtime_unavailable")
        self.assertEqual(higgsfield.classify(ctx.exception)[0], "refused")


if __name__ == "__main__":
    unittest.main()
