"""Hosted worker entry for CS-B3 stages (one authenticated POST = recovery sweep
+ at most one leased item). Mirrors service/worker.make_wsgi's authentication.

Hosting wiring is CS-B5: api/worker.py (a CS-B1 file) should serve
`make_runtime_wsgi(config.load, store_factory)` instead of the B1 shell, with a
Storage object store bound to the runtime principal. Until a store is supplied
this entry refuses with 503 object_store_unconfigured; it never runs a paid
stage it cannot finish.
"""
from __future__ import annotations

import hmac
import json
import logging

from ..config import ConfigError
from ..errors import ServiceError
from ..providers.credentials import CredentialError, KeyRing
from .worker import HttpOutputFetcher, ProviderRuntime

log = logging.getLogger("creative_studio.runtime.entry")


def make_runtime_wsgi(settings_loader, store_factory=None, fetcher=None, keyring_loader=KeyRing.from_env):
    def app(environ, start_response):
        status, payload = 500, {"error": "internal_error"}
        try:
            settings = settings_loader()
            if environ.get("REQUEST_METHOD") != "POST":
                raise ServiceError(405, "method_not_allowed")
            supplied = (environ.get("HTTP_AUTHORIZATION") or "")[len("Bearer "):]
            if not settings.worker_secret:
                raise ServiceError(503, "worker_secret_unconfigured")
            if not supplied or not hmac.compare_digest(supplied.encode(), settings.worker_secret.encode()):
                raise ServiceError(401, "authentication_required")
            if store_factory is None:
                raise ServiceError(503, "object_store_unconfigured")
            runtime = ProviderRuntime(settings, store_factory(settings), fetcher or HttpOutputFetcher(),
                                      keyring_loader())
            status, payload = 200, runtime.run_once()
        except ServiceError as err:
            status, payload = err.status, err.body()
        except CredentialError as err:
            log.error("worker refused: %s", err.code)
            status, payload = 503, {"error": err.code}
        except ConfigError as err:
            log.error("worker refused: %s", err)
            status, payload = 503, {"error": "service_unconfigured"}
        except Exception:
            log.exception("worker invocation failed")
        body = json.dumps(payload, default=str).encode()
        start_response(f"{status} {'OK' if status == 200 else 'Error'}",
                       [("Content-Type", "application/json"), ("Content-Length", str(len(body))),
                        ("Cache-Control", "no-store")])
        return [body]
    return app
