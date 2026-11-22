"""Security regression tests for SPA fallback and static directory boundary enforcement (Issue #64)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent_fleet.main import create_app


@pytest.fixture()
def spa_dist(tmp_path: Path) -> Path:
    """Create an isolated temporary web/dist tree so tests never touch the repo's web/dist."""
    root = tmp_path / "repo"
    dist = root / "web" / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)

    (dist / "index.html").write_text("<!doctype html><html><body>Agent Fleet SPA</body></html>")
    (dist / "favicon.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"></svg>')
    (assets / "app.js").write_text('console.log("agent-fleet");')

    # Decoy sensitive files outside web/dist to ensure traversal cannot read them
    (root / ".env").write_text("AGENT_FLEET_API_SECRET=supersecret\n")
    (root / "data").mkdir(parents=True)
    (root / "data" / "fleet.db").write_bytes(b"SQLite format 3\x00dummy")
    (root / "backend" / "agent_fleet").mkdir(parents=True)
    (root / "backend" / "agent_fleet" / "main.py").write_text('"""FastAPI application entry point."""\n')

    return dist


@pytest.fixture()
def client(fleet_db, spa_dist: Path):
    """Test client backed by the per-test database and an isolated tmp_path web/dist."""
    test_app = create_app(web_dist=spa_dist)
    with TestClient(test_app) as c:
        yield c


def test_directory_traversal_blocked_env(client):
    """Path traversal attempting to read .env outside WEB_DIST must be rejected."""
    res = client.get("/..%2F..%2F.env")
    assert res.status_code in (403, 404)
    assert "AGENT_FLEET" not in res.text
    assert "API_SECRET" not in res.text


def test_directory_traversal_blocked_database(client):
    """Path traversal attempting to read fleet.db outside WEB_DIST must be rejected."""
    for path in [
        "/..%2F..%2Fdata/fleet.db",
        "/%2e%2e/%2e%2e/data/fleet.db",
        "/%2e%2e%2f%2e%2e%2fdata/fleet.db",
        "/..%2F..%2F..%2Fdata/fleet.db",
        "/sub/..%2F..%2F..%2Fdata/fleet.db",
    ]:
        res = client.get(path)
        assert res.status_code in (403, 404)
        assert b"SQLite format 3" not in res.content


def test_directory_traversal_blocked_source_code(client):
    """Path traversal attempting to read backend code must be rejected or not leak source."""
    res = client.get("/..%2F..%2Fbackend/agent_fleet/main.py")
    assert res.status_code in (403, 404)
    assert "FastAPI application entry point" not in res.text

    # Via /assets prefix normalized by client
    res_assets = client.get("/assets/../../backend/agent_fleet/main.py")
    if res_assets.status_code == 200:
        assert "FastAPI application entry point" not in res_assets.text
        assert "<!doctype html>" in res_assets.text
    else:
        assert res_assets.status_code in (403, 404)


def test_hidden_and_dotfiles_blocked(client):
    """Hidden files (e.g. .env, .git) must never be served even if within WEB_DIST."""
    res_git = client.get("/.git/config")
    assert res_git.status_code in (403, 404)
    assert "[core]" not in res_git.text

    res_env = client.get("/.env")
    assert res_env.status_code in (403, 404)


def test_dotfile_inside_web_dist_blocked(client, spa_dist: Path):
    """Even if a dotfile is accidentally placed inside WEB_DIST, it must NOT be served."""
    dotfile = spa_dist / ".secret_spa_test"
    dotfile.write_text("SENSITIVE_SECRET_TOKEN=xyz123")
    res = client.get("/.secret_spa_test")
    assert res.status_code in (403, 404)
    assert "SENSITIVE_SECRET_TOKEN" not in res.text


def test_null_byte_path_rejected(client):
    """Paths with embedded null bytes must be rejected without 500 error."""
    res = client.get("/machines%00test")
    assert res.status_code in (400, 403, 404)


def test_legitimate_spa_client_routes(client):
    """Legitimate frontend client routes must return index.html with 200 OK."""
    for path in ["/", "/machines", "/tasks", "/tasks/task-001", "/settings", "/dashboard/overview"]:
        res = client.get(path)
        assert res.status_code == 200
        assert "<!doctype html>" in res.text
        assert res.headers.get("content-type", "").startswith("text/html")


def test_legitimate_static_assets(client):
    """Legitimate static files in WEB_DIST must be served with 200 OK."""
    res_favicon = client.get("/favicon.svg")
    assert res_favicon.status_code == 200
    assert "<svg" in res_favicon.text or "svg" in res_favicon.headers.get("content-type", "")

    res_asset = client.get("/assets/app.js")
    assert res_asset.status_code == 200
    assert "agent-fleet" in res_asset.text


def test_api_endpoints_unaffected(client):
    """API endpoints remain fully operational and are not intercepted by SPA fallback."""
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json().get("ok") is True

    res = client.get("/api/settings")
    assert res.status_code == 200
    assert "db_path" in res.json() or "auth_required" in res.json()


def test_missing_web_dist_returns_placeholder(fleet_db, tmp_path: Path):
    """When web/dist does not exist, root returns the JSON placeholder cleanly."""
    missing_dist = tmp_path / "nonexistent-dist"
    test_app = create_app(web_dist=missing_dist)
    with TestClient(test_app) as c:
        res = c.get("/")
        assert res.status_code == 200
        data = res.json()
        assert data.get("service") == "agent-fleet"
        assert "frontend build missing" in data.get("note", "")
