#!/usr/bin/env python3
"""Creative Studio provisioning 05: dedicated storage worker identity (idempotent).

  (a) creates the worker auth user through the Auth admin API (service key from env, never printed),
      or, if it already exists, sets its password to the env value;
  (b) registers it through cs_private.cs_register_runtime_principal() for CS_ENVIRONMENT_ID (upsert, 'active');
  (c) reads back that cs_storage_allowed() PERMITS the worker (select + insert under its environment
      prefix) and DENIES an unrelated authenticated user, and denies the worker outside its prefix.

Environment (all required; none is ever printed):
  PGDATABASE_ADMIN_URL         admin DSN (role that bypasses RLS, e.g. the Supabase postgres role)
  CS_ADMIN_AUTH_URL            https://<ref>.supabase.co/auth/v1
  CS_ADMIN_SERVICE_KEY         project service key, used only here, only for the Auth admin API
  CS_STORAGE_WORKER_EMAIL / CS_STORAGE_WORKER_PASSWORD   identity to create (same values go to the worker env)
  CS_ENVIRONMENT_ID            e.g. production
Exit 0 only if every readback passes.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
import uuid

REQUIRED = ("PGDATABASE_ADMIN_URL", "CS_ADMIN_AUTH_URL", "CS_ADMIN_SERVICE_KEY",
            "CS_STORAGE_WORKER_EMAIL", "CS_STORAGE_WORKER_PASSWORD", "CS_ENVIRONMENT_ID")
BUCKET = "creative-studio"


class ProvisionError(RuntimeError):
    """Message never contains the service key or the worker password."""


def _admin(env, method: str, path: str, body: dict | None, opener=urllib.request.urlopen):
    key = env["CS_ADMIN_SERVICE_KEY"]
    req = urllib.request.Request(
        env["CS_ADMIN_AUTH_URL"].rstrip("/") + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with opener(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        return exc.code, {}
    except (urllib.error.URLError, OSError) as exc:
        raise ProvisionError(f"auth admin API unreachable ({type(exc).__name__})") from None


def ensure_auth_user(env, cur, opener=urllib.request.urlopen) -> str:
    email, password = env["CS_STORAGE_WORKER_EMAIL"], env["CS_STORAGE_WORKER_PASSWORD"]
    status, data = _admin(env, "POST", "/admin/users",
                          {"email": email, "password": password, "email_confirm": True}, opener)
    if status in (200, 201) and data.get("id"):
        return str(data["id"])
    if status in (409, 422):                                   # already registered -> converge password
        cur.execute("select id from auth.users where lower(email) = lower(%s)", (email,))
        row = cur.fetchone()
        if not row:
            raise ProvisionError("auth user reported as existing but not found in auth.users")
        uid = str(row[0])
        status, _ = _admin(env, "PUT", f"/admin/users/{uid}", {"password": password, "email_confirm": True}, opener)
        if status not in (200, 201):
            raise ProvisionError(f"could not update existing worker user (status {status})")
        return uid
    raise ProvisionError(f"auth admin create failed (status {status})")


def register_principal(cur, uid: str, environment_id: str) -> None:
    cur.execute("select 1 from cs_private.cs_runtime_limits where environment_id = %s", (environment_id,))
    if not cur.fetchone():
        raise ProvisionError(f"no cs_runtime_limits row for environment {environment_id!r} (run provisioning 02)")
    # Sanctioned path (migration 20261031190200): SECURITY DEFINER, owner/service_role only, idempotent.
    cur.execute("select cs_private.cs_register_runtime_principal(%s, %s)", (uid, environment_id))


def _allowed(cur, uid: str, name: str, op: str) -> bool:
    cur.execute("select set_config('request.jwt.claims', %s, true)", (json.dumps({"sub": uid, "role": "authenticated"}),))
    cur.execute("select cs_private.cs_storage_allowed(%s, %s, %s)", (BUCKET, name, op))
    return bool(cur.fetchone()[0])


def readback(cur, uid: str, environment_id: str) -> list[tuple[str, bool]]:
    probe = f"{environment_id}/provisioning-readback/{uuid.uuid4()}.bin"
    stranger = str(uuid.uuid4())      # an authenticated user with no principal row, grant or intent
    return [
        ("worker may select under its environment prefix", _allowed(cur, uid, probe, "select") is True),
        ("worker may insert under its environment prefix", _allowed(cur, uid, probe, "insert") is True),
        ("worker denied outside its environment prefix", _allowed(cur, uid, f"other-{environment_id}/x.bin", "select") is False),
        ("worker denied delete (no update/delete)", _allowed(cur, uid, probe, "delete") is False),
        ("unrelated authenticated user denied select", _allowed(cur, stranger, probe, "select") is False),
        ("unrelated authenticated user denied insert", _allowed(cur, stranger, probe, "insert") is False),
    ]


def main(env=None, out=sys.stdout, opener=urllib.request.urlopen) -> int:
    env = os.environ if env is None else env
    missing = [n for n in REQUIRED if not (env.get(n) or "").strip()]
    if missing:
        print("missing required environment: " + ", ".join(missing), file=out)
        return 2
    import psycopg2
    conn = psycopg2.connect(env["PGDATABASE_ADMIN_URL"])
    try:
        with conn, conn.cursor() as cur:
            uid = ensure_auth_user(env, cur, opener)
            register_principal(cur, uid, env["CS_ENVIRONMENT_ID"])
            results = readback(cur, uid, env["CS_ENVIRONMENT_ID"])
    except ProvisionError as exc:
        print(f"FAILED: {exc}", file=out)
        return 1
    finally:
        conn.close()
    print(f"worker user id: {uid}  environment: {env['CS_ENVIRONMENT_ID']}", file=out)
    ok = True
    for label, passed in results:
        print(f"[{'PASS' if passed else 'FAIL'}] {label}", file=out)
        ok = ok and passed
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
