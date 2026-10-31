"""Minimal stand-in for the Supabase Auth password grant (POST /auth/v1/token?grant_type=password).
Issues HS256 access tokens whose lifetime the test controls; counts sign-ins. Loopback only."""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import jwt


class FakeAuth:
    def __init__(self, jwt_secret: str, issuer: str):
        self.jwt_secret, self.issuer = jwt_secret, issuer
        self.users: dict[str, tuple[str, str]] = {}      # email -> (password, user id)
        self.ttl = 3600                                    # seconds until exp for the next token
        self.sign_ins = 0
        self.fail_status: int | None = None                # force every sign-in to this HTTP status
        self.seen_bodies: list[bytes] = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/auth/v1"

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.server.shutdown()
        self.server.server_close()

    def _handler(self):
        auth = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                auth.seen_bodies.append(raw)
                if not self.path.startswith("/auth/v1/token?grant_type=password"):
                    return self._send(404, {"error": "not_found"})
                if not self.headers.get("apikey"):
                    return self._send(401, {"message": "No API key found in request"})
                if auth.fail_status:
                    return self._send(auth.fail_status, {"error": "forced"})
                body = json.loads(raw or b"{}")
                known = auth.users.get(body.get("email", ""))
                if not known or known[0] != body.get("password"):
                    return self._send(400, {"error": "invalid_grant", "error_description": "Invalid login credentials"})
                auth.sign_ins += 1
                now = int(time.time())
                token = jwt.encode({"sub": known[1], "role": "authenticated", "aud": "authenticated",
                                    "iss": auth.issuer, "iat": now, "exp": now + auth.ttl,
                                    "jti": str(auth.sign_ins)}, auth.jwt_secret, algorithm="HS256")
                self._send(200, {"access_token": token, "token_type": "bearer", "expires_in": auth.ttl})

        return Handler
