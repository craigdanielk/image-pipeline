"""A real, killable CS-B3 worker process for the worker-death acceptance tests.
argv[1] = JSON config {dsn, state_dir, store_dir, hang_fetch}. Runs exactly one
run_once() with the mocked provider transport, then prints the JSON result."""
import base64
import json
import sys
from types import SimpleNamespace
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from service.providers.adapter import engine_higgsfield  # noqa: E402
from service.config import Settings  # noqa: E402
from service.providers.credentials import KeyRing  # noqa: E402
from service.runtime.worker import ProviderRuntime  # noqa: E402
from tests.creative_studio.b3_support import TEST_KEYS, DirStore, FakeFetcher, FakeHiggsfield  # noqa: E402

cfg = json.loads(sys.argv[1])
settings = Settings(api_database_url="unused", worker_database_url=cfg["dsn"], environment_id="test",
                    release_id="test-release", jwt_issuer="unused", jwt_audience="authenticated", jwt_secret="unused",
                    jwks_url=None, storage_url="http://127.0.0.1/storage/v1", storage_anon_key="unused",
                    signed_download_seconds=300, download_grant_seconds=60, worker_secret=None,
                    runtime_principal="cs-worker:test")
fake = FakeHiggsfield(cfg["state_dir"])
runtime = ProviderRuntime(settings, DirStore(cfg["store_dir"]), FakeFetcher(cfg["state_dir"], hang=cfg.get("hang_fetch", False)),
                          KeyRing({k: base64.b64decode(v) for k, v in TEST_KEYS.items()}))
with mock.patch.object(engine_higgsfield, "subprocess", SimpleNamespace(run=fake)):
    print(json.dumps(runtime.run_once(), default=str), flush=True)
