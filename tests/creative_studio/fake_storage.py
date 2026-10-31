"""Minimal stand-in for Supabase Storage, backed by the real throwaway Postgres.

It reproduces the behaviour CS-B1 relies on and nothing else:
  * every object operation runs as role `authenticated` with the caller's
    verified JWT claims, so the real storage.objects RLS policies from the
    migration (cs_storage_allowed) decide access;
  * POST /object/sign/... returns a relative signedURL whose token expires
    after expiresIn seconds; GET on it serves bytes only while unexpired;
  * upload never upserts (an existing name is a conflict).
No network beyond loopback; bytes are held in memory.
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import jwt
import psycopg2

_SIGN_SECRET = "fake-storage-internal-url-signing-key"


class FakeStorage:
    def __init__(self, dsn: str, jwt_secret: str, issuer: str):
        self.dsn = dsn
        self.jwt_secret = jwt_secret
        self.issuer = issuer
        self.blobs: dict[str, bytes] = {}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/storage/v1"

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    # -- RLS-evaluated object access ------------------------------------------
    def _as_user(self, claims: dict, sql: str, params: tuple):
        conn = psycopg2.connect(self.dsn)
        try:
            with conn, conn.cursor() as cur:
                cur.execute("set local role authenticated")
                cur.execute("select set_config('request.jwt.claims', %s, true)",
                            (json.dumps({"sub": claims["sub"], "role": "authenticated"}),))
                cur.execute(sql, params)
                return cur.fetchall() if cur.description else None
        finally:
            conn.close()

    def _verify_user(self, header: str | None) -> dict:
        token = (header or "")[len("Bearer "):]
        return jwt.decode(token, self.jwt_secret, algorithms=["HS256"], audience="authenticated",
                          issuer=self.issuer)

    def _handler(self):
        storage = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status: int, body, content_type="application/json"):
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _user(self):
                try:
                    return storage._verify_user(self.headers.get("Authorization"))
                except jwt.PyJWTError:
                    self._send(400, {"error": "InvalidJWT"})
                    return None

            def do_POST(self):
                parsed = urlparse(self.path)
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                if parsed.path.startswith("/storage/v1/object/sign/"):
                    claims = self._user()
                    if claims is None:
                        return
                    bucket, _, name = unquote(parsed.path[len("/storage/v1/object/sign/"):]).partition("/")
                    rows = storage._as_user(claims, "select 1 from storage.objects where bucket_id = %s and name = %s",
                                            (bucket, name))
                    if not rows:
                        return self._send(400, {"error": "not_found", "message": "Object not found"})
                    expires_in = int(json.loads(body or b"{}").get("expiresIn", 0))
                    token = jwt.encode({"url": f"{bucket}/{name}", "exp": int(time.time()) + expires_in},
                                       _SIGN_SECRET, algorithm="HS256")
                    return self._send(200, {"signedURL": f"/object/sign/{bucket}/{name}?token={token}"})
                if parsed.path.startswith("/storage/v1/object/list/"):
                    claims = self._user()
                    if claims is None:
                        return
                    bucket = unquote(parsed.path[len("/storage/v1/object/list/"):])
                    prefix = json.loads(body or b"{}").get("prefix", "")
                    rows = storage._as_user(claims, "select name from storage.objects where bucket_id = %s "
                                                    "and name like %s limit 1", (bucket, prefix + "%"))
                    return self._send(200, [{"name": r[0]} for r in rows])
                if parsed.path.startswith("/storage/v1/object/"):
                    claims = self._user()
                    if claims is None:
                        return
                    if self.headers.get("x-upsert", "false").lower() == "true":
                        return self._send(400, {"error": "upsert_not_permitted"})
                    bucket, _, name = unquote(parsed.path[len("/storage/v1/object/"):]).partition("/")
                    try:
                        storage._as_user(claims, "insert into storage.objects (bucket_id, name, owner, metadata) "
                                                 "values (%s, %s, %s, %s)",
                                         (bucket, name, claims["sub"], json.dumps({"size": len(body)})))
                    except psycopg2.errors.InsufficientPrivilege:
                        return self._send(403, {"error": "Unauthorized", "message": "new row violates row-level security policy"})
                    except psycopg2.errors.UniqueViolation:
                        return self._send(409, {"error": "Duplicate"})
                    storage.blobs[f"{bucket}/{name}"] = body
                    return self._send(200, {"Key": f"{bucket}/{name}"})
                self._send(404, {"error": "not_found"})

            def do_GET(self):
                parsed = urlparse(self.path)
                if parsed.path.startswith("/storage/v1/object/sign/"):
                    path = unquote(parsed.path[len("/storage/v1/object/sign/"):])
                    token = parse_qs(parsed.query).get("token", [""])[0]
                    try:
                        payload = jwt.decode(token, _SIGN_SECRET, algorithms=["HS256"])
                    except jwt.ExpiredSignatureError:
                        return self._send(400, {"error": "InvalidJWT", "message": "jwt expired"})
                    except jwt.PyJWTError:
                        return self._send(400, {"error": "InvalidJWT"})
                    if payload.get("url") != path or path not in storage.blobs:
                        return self._send(400, {"error": "InvalidSignature"})
                    return self._send(200, storage.blobs[path], "application/octet-stream")
                if parsed.path.startswith("/storage/v1/object/authenticated/"):
                    claims = self._user()
                    if claims is None:
                        return
                    bucket, _, name = unquote(parsed.path[len("/storage/v1/object/authenticated/"):]).partition("/")
                    rows = storage._as_user(claims, "select 1 from storage.objects where bucket_id = %s and name = %s",
                                            (bucket, name))
                    if not rows:
                        return self._send(400, {"error": "not_found"})
                    return self._send(200, storage.blobs[f"{bucket}/{name}"], "application/octet-stream")
                self._send(404, {"error": "not_found"})

        return Handler
