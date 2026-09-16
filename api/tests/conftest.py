"""Keep tests away from developer history, source documents and credentials."""
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_test_cache = Path(__file__).resolve().parents[2] / ".cache/tests"
_test_cache.mkdir(parents=True, exist_ok=True)
_session = tempfile.TemporaryDirectory(prefix="session-", dir=_test_cache)
# Module imports construct routes/services before fixtures run.
os.environ["DATABASE_PATH"] = str(Path(_session.name) / "collection.db")
os.environ["SOURCE_STORAGE_PATH"] = str(Path(_session.name) / "sources")
os.environ["PROJECT_CACHE_DIR"] = str(Path(_session.name) / "cache")
os.environ["AUDIT_LOG_ENABLED"] = "false"
os.environ["LANGFUSE_PUBLIC_KEY"] = ""
os.environ["LANGFUSE_SECRET_KEY"] = ""


@pytest.fixture(autouse=True)
def isolate_runtime(monkeypatch, tmp_path):
    monkeypatch.setenv("PROJECT_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "history.db"))
    monkeypatch.setenv("SOURCE_STORAGE_PATH", str(tmp_path / "sources"))
