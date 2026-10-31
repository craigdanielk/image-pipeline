"""Test support for ENG-15: signs MOS-gateway calls the way the real MOS gateway does.

``GatewayApp`` wraps the WSGI app: for a route whose policy REQUIRES an assertion and a request carrying none, it
signs a fresh one (user from the bearer, action from the route policy, binding from the workspace's active
connection). Requests that already carry the header, or routes that do not require one, pass through untouched.
Negative tests talk to the unwrapped app.
"""
from __future__ import annotations

import base64
import io
import json
import os
import time
import uuid

import psycopg2

from service import assertion

KEY = "t" * 48          # test-only key, >= 32 bytes


def payload_for(*, user_id, action, binding, body: bytes, now=None, ttl=45, nonce=None, aud="creative-studio") -> dict:
    now = int(now if now is not None else time.time())
    import hashlib
    return {"aud": aud, "sub": user_id, "act": action, "bnd": binding, "bsha": hashlib.sha256(body).hexdigest(),
            "nonce": nonce or assertion.b64e(os.urandom(16)), "iat": now, "exp": now + ttl}


def header_for(**kw) -> str:
    key = kw.pop("key", KEY)
    return assertion.sign(key, payload_for(**kw))


def bearer_sub(token: str) -> str:
    seg = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4)))["sub"]


class GatewayApp:
    def __init__(self, app, admin_dsn: str, key: str = KEY):
        object.__setattr__(self, "_app", app)
        object.__setattr__(self, "_dsn", admin_dsn)
        object.__setattr__(self, "_key", key)

    def __getattr__(self, name):
        return getattr(self._app, name)

    def __setattr__(self, name, value):
        setattr(self._app, name, value)

    def _binding(self, workspace_id: str, sub: str | None = None):
        conn = psycopg2.connect(self._dsn)
        try:
            cur = conn.cursor()
            cur.execute("select external_workspace_id from public.cs_external_bindings "
                        "where workspace_id = %s and status = 'active'", (workspace_id,))
            row = cur.fetchone()
            if row is None and sub:
                # workspaces created by suites that never connected MOS get a verified-looking active binding
                ext = str(uuid.uuid4())
                cur.execute("insert into public.cs_external_bindings (workspace_id, authority, external_workspace_id,"
                            " verification, verification_sha256, created_by) values (%s, 'marketing-os', %s, '{}'::jsonb,"
                            " repeat('0', 64), %s)", (workspace_id, ext, sub))
                conn.commit()
                return ext
            return row[0] if row else None
        finally:
            conn.close()

    def __call__(self, environ, start_response):
        from service import api
        if assertion.HEADER_ENV_KEY not in environ and environ.get("HTTP_AUTHORIZATION"):
            for method, pattern, name in api._ROUTES:
                m = pattern.match(environ.get("PATH_INFO", ""))
                policy = assertion.POLICY.get(name)
                if m and method == environ.get("REQUEST_METHOD") and policy and policy.required:
                    raw = environ["wsgi.input"].read(int(environ.get("CONTENT_LENGTH") or 0))
                    environ["wsgi.input"] = io.BytesIO(raw)
                    try:
                        bnd = (json.loads(raw or b"{}").get("external_workspace_id") if policy.binds
                               else None)
                        sub = bearer_sub(environ["HTTP_AUTHORIZATION"][7:])
                        if not policy.binds:
                            bnd = self._binding(m.group(1), sub)
                    except Exception:
                        bnd = None
                    if bnd:
                        environ[assertion.HEADER_ENV_KEY] = header_for(
                            user_id=sub, action=policy.action, binding=bnd, body=raw, key=self._key)
                    break
        return self._app(environ, start_response)
