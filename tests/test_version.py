"""Tests for application version discovery and /api/system/version endpoint."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_fleet.version import BASE_VERSION, get_version_info


@pytest.fixture()
def client(fleet_db, monkeypatch):
    monkeypatch.setenv("AGENT_FLEET_INCLUDE_LOCAL_RUNTIME", "false")
    from agent_fleet.main import app

    with TestClient(app) as c:
        yield c


def test_version_from_version_json(tmp_path: Path):
    v_file = tmp_path / "version.json"
    v_file.write_text(
        json.dumps(
            {
                "version": "0.2.0",
                "commit": "a1b2c3d4e5f60718293041526374859607182930",
                "build_time": "2026-10-29T12:00:00Z",
                "is_release": True,
                "secret_token": "SHOULD_NOT_LEAK",
                "builder_path": "/home/runner/work/secret",
            }
        ),
        encoding="utf-8",
    )

    info = get_version_info(version_file=v_file)
    assert info == {
        "version": "0.2.0",
        "commit": "a1b2c3d4e5f60718293041526374859607182930",
        "commit_short": "a1b2c3d",
        "build_time": "2026-10-29T12:00:00Z",
        "is_release": True,
    }
    # Ensure no extra keys leaked
    assert set(info.keys()) == {"version", "commit", "commit_short", "build_time", "is_release"}


def test_version_from_version_json_dev_inferred(tmp_path: Path):
    v_file = tmp_path / "version.json"
    v_file.write_text(
        json.dumps(
            {
                "version": "0.2.0-dev",
                "commit": "dev",
            }
        ),
        encoding="utf-8",
    )

    info = get_version_info(version_file=v_file)
    assert info["version"] == "0.2.0-dev"
    assert info["commit"] == "dev"
    assert info["commit_short"] == "dev"
    assert info["build_time"] is None
    assert info["is_release"] is False


def test_version_from_environment_variables(tmp_path: Path):
    env = {
        "AGENT_FLEET_VERSION": "0.2.5",
        "AGENT_FLEET_COMMIT_SHA": "1234567890abcdef1234567890abcdef12345678",
        "AGENT_FLEET_BUILD_TIME": "2026-10-29T14:30:00Z",
        "AGENT_FLEET_IS_RELEASE": "true",
    }
    # Point version_file to non-existent file so env is evaluated
    info = get_version_info(version_file=tmp_path / "absent.json", env=env)
    assert info == {
        "version": "0.2.5",
        "commit": "1234567890abcdef1234567890abcdef12345678",
        "commit_short": "1234567",
        "build_time": "2026-10-29T14:30:00Z",
        "is_release": True,
    }


def test_version_from_environment_dev_flag(tmp_path: Path):
    env = {
        "AGENT_FLEET_VERSION": "0.2.5-dev",
        "AGENT_FLEET_COMMIT_SHA": "abcdef1111222233334444555566667777888899",
    }
    info = get_version_info(version_file=tmp_path / "absent.json", env=env)
    assert info["version"] == "0.2.5-dev"
    assert info["commit_short"] == "abcdef1"
    assert info["build_time"] is None
    assert info["is_release"] is False


def test_version_from_git_fallback(tmp_path: Path, monkeypatch):
    # Mock _get_git_commit to simulate a git repo checkout
    monkeypatch.setattr(
        "agent_fleet.version._get_git_commit",
        lambda repo_dir=None: "fedcba9876543210fedcba9876543210fedcba98",
    )
    info = get_version_info(version_file=tmp_path / "absent.json", env={})
    assert info == {
        "version": f"{BASE_VERSION}-dev",
        "commit": "fedcba9876543210fedcba9876543210fedcba98",
        "commit_short": "fedcba9",
        "build_time": None,
        "is_release": False,
    }


def test_version_complete_fallback(tmp_path: Path, monkeypatch):
    # Simulate environment where no git and no version.json exist
    monkeypatch.setattr("agent_fleet.version._get_git_commit", lambda repo_dir=None: None)
    info = get_version_info(version_file=tmp_path / "absent.json", env={})
    assert info == {
        "version": f"{BASE_VERSION}-dev",
        "commit": "dev",
        "commit_short": "dev",
        "build_time": None,
        "is_release": False,
    }


def test_api_system_version_endpoint(client):
    res = client.get("/api/system/version")
    assert res.status_code == 200
    data = res.json()
    assert set(data.keys()) == {"version", "commit", "commit_short", "build_time", "is_release"}
    assert isinstance(data["version"], str)
    assert isinstance(data["commit"], str)
    assert isinstance(data["commit_short"], str)
    assert data["build_time"] is None or isinstance(data["build_time"], str)
    assert isinstance(data["is_release"], bool)

    # Security check: verify no path leakage or host username leakage
    for key, val in data.items():
        if isinstance(val, str):
            assert "/Users/" not in val
            assert "/home/" not in val
            assert "\\" not in val


def test_api_system_version_auth_readonly(client, monkeypatch):
    # Test that /api/system/version respects allow_anonymous_readonly=True
    monkeypatch.setenv("AGENT_FLEET_API_SECRET", "super-secret-token")
    monkeypatch.setenv("AGENT_FLEET_ALLOW_ANONYMOUS_READONLY", "true")

    # Clear cached settings
    from agent_fleet.settings import get_settings
    get_settings.cache_clear()

    res = client.get("/api/system/version")
    assert res.status_code == 200

    # If anonymous readonly is disabled, should require token
    monkeypatch.setenv("AGENT_FLEET_ALLOW_ANONYMOUS_READONLY", "false")
    get_settings.cache_clear()

    unauth_res = client.get("/api/system/version")
    assert unauth_res.status_code == 401

    auth_res = client.get(
        "/api/system/version",
        headers={"Authorization": "Bearer super-secret-token"},
    )
    assert auth_res.status_code == 200

    # Reset settings cache
    monkeypatch.delenv("AGENT_FLEET_API_SECRET", raising=False)
    monkeypatch.delenv("AGENT_FLEET_ALLOW_ANONYMOUS_READONLY", raising=False)
    get_settings.cache_clear()
