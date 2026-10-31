"""Vercel Python entrypoint: Creative Studio worker (-> service/runtime/entry.py).
One authenticated POST executes at most one leased work item."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from service import config  # noqa: E402
from service.runtime.entry import make_runtime_wsgi  # noqa: E402
from service.storage import make_storage_store  # noqa: E402

app = make_runtime_wsgi(config.load, store_factory=make_storage_store)
