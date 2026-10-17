import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tests"))


@pytest.fixture()
def fleet_db(tmp_path, monkeypatch):
    """Every test gets its own SQLite file."""
    monkeypatch.setenv("AGENT_FLEET_DB_PATH", str(tmp_path / "fleet-test.db"))
    monkeypatch.setenv("AGENT_FLEET_INVENTORY_PATHS", "[]")
    monkeypatch.setenv("AGENT_FLEET_SSH_CONFIG_PATH", str(tmp_path / "no-ssh-config"))
    monkeypatch.setenv("AGENT_FLEET_INCLUDE_LOCAL_RUNTIME", "false")
    from agent_fleet.db import create_all, init_engine
    from agent_fleet.settings import get_settings

    get_settings.cache_clear()
    settings = get_settings()
    init_engine(settings.db_path)
    create_all()
    yield settings
    get_settings.cache_clear()
