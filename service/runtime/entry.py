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


def _postrun(settings, store):
    """CS-17b post-run stages; the MOS push is configured only when a gateway URL and the assertion key exist
    (otherwise a handoff is queued/readable and run status says `handoff_offered`)."""
    from ..postrun.runtime import PostRunRuntime
    gateway = None
    if settings.mos_gateway_url and settings.mos_assertion_key:
        from .gateway import HttpMarketingOsGateway
        gateway = HttpMarketingOsGateway(settings.mos_gateway_url, settings.mos_service_token,
                                         assertion_key=settings.mos_assertion_key)
    return PostRunRuntime(settings, store, gateway=gateway)


def _brand_import(settings, store):
    """CS-B2 ``import_brand_package`` on the hosted worker. Every media request starts by importing the client's
    brand package; the hosted worker never claimed that stage, so imports stayed queued forever and no media could
    be prepared. One POST still executes at most one work item: a due import goes first (free, a prerequisite)."""
    if not (settings.mos_gateway_url and settings.mos_assertion_key):
        return None
    from ..brand.importer import run_import_once
    from .gateway import HttpMarketingOsGateway
    gateway = HttpMarketingOsGateway(settings.mos_gateway_url, settings.mos_service_token,
                                     assertion_key=settings.mos_assertion_key)
    result = run_import_once(settings, gateway, store)
    return result if result.get("claimed", 1) else None


def make_runtime_wsgi(settings_loader, store_factory=None, fetcher=None, keyring_loader=KeyRing.from_env):
    def app(environ, start_response):
        status, payload = 500, {"error": "internal_error"}
        try:
            settings = settings_loader()
            # POST = db kick / manual; GET = the single daily Vercel Hobby safety-net cron (same bearer)
            if environ.get("REQUEST_METHOD") not in ("POST", "GET"):
                raise ServiceError(405, "method_not_allowed")
            supplied = (environ.get("HTTP_AUTHORIZATION") or "")[len("Bearer "):]
            if not settings.worker_secret:
                raise ServiceError(503, "worker_secret_unconfigured")
            if not supplied or not hmac.compare_digest(supplied.encode(), settings.worker_secret.encode()):
                raise ServiceError(401, "authentication_required")
            if store_factory is None:
                raise ServiceError(503, "object_store_unconfigured")
            store = store_factory(settings)
            imported = _brand_import(settings, store)
            if imported is not None:
                status, payload = 200, imported
            else:
                runtime = ProviderRuntime(settings, store, fetcher or HttpOutputFetcher(), keyring_loader(),
                                          postrun=_postrun(settings, store))
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
