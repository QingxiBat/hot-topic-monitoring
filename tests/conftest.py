import os

os.environ["RADAR_DISABLE_SCHEDULER"] = "1"

import pytest
from fastapi.testclient import TestClient

from app import db


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    db.init_db()
    yield
    for path in tmp_path.glob("test.db*"):
        try:
            path.unlink()
        except OSError:
            pass


@pytest.fixture
def client(temp_db, monkeypatch):
    import app.main as main

    async def noop(*args, **kwargs):
        return {"status": "skipped"}

    monkeypatch.setattr(main, "run_collect", noop)
    monkeypatch.setattr(main, "run_keyword_check", noop)
    with TestClient(main.app) as test_client:
        yield test_client
