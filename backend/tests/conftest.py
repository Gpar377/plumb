import os
import sys
import tempfile
from pathlib import Path

# Environment must be set before the app is imported.
_tmp = tempfile.mkdtemp(prefix="plumb-test-")
os.environ["PLUMB_DATA_DIR"] = _tmp
os.environ["PLUMB_LLM_DISABLED"] = "1"
os.environ["PLUMB_TODAY"] = "2026-10-22"
os.environ["PLUMB_SEED_EXAMPLES"] = "0"
os.environ.pop("GEMINI_API_KEY", None)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="session")
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


def as_user(client, uid):
    class _C:
        def get(self, url, **kw):
            return client.get(url, headers={"X-Plumb-User": uid}, **kw)

        def post(self, url, **kw):
            return client.post(url, headers={"X-Plumb-User": uid}, **kw)

        def put(self, url, **kw):
            return client.put(url, headers={"X-Plumb-User": uid}, **kw)

        def patch(self, url, **kw):
            return client.patch(url, headers={"X-Plumb-User": uid}, **kw)

    return _C()
