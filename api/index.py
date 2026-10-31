"""Vercel Python entrypoint: Creative Studio hosted API (-> service/api.py)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from service import config  # noqa: E402
from service.api import create_app  # noqa: E402

_app = None


def app(environ, start_response):
    global _app
    if _app is None:
        try:
            _app = create_app(config.load())
        except config.ConfigError as err:
            body = b'{"error":"service_unconfigured"}'
            print(f"creative-studio api refused start: {err}", file=sys.stderr)
            start_response("503 Service Unavailable", [("Content-Type", "application/json"),
                                                       ("Content-Length", str(len(body)))])
            return [body]
    return _app(environ, start_response)
