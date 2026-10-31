"""Vercel Python entrypoint: Creative Studio worker (-> service/worker.py).
One authenticated POST executes at most one leased work item."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from service import config  # noqa: E402
from service.worker import make_wsgi  # noqa: E402

app = make_wsgi(config.load)
