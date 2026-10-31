"""Run the hosted API (api/index.py, real entrypoint) in its own process for the
restart test. Configuration comes from the environment exactly as in hosting."""
import sys
from pathlib import Path
from wsgiref.simple_server import WSGIRequestHandler, make_server

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api.index import app  # noqa: E402


class Quiet(WSGIRequestHandler):
    def log_message(self, *args):
        pass


if __name__ == "__main__":
    server = make_server("127.0.0.1", int(sys.argv[1]), app, handler_class=Quiet)
    print("ready", flush=True)
    server.serve_forever()
