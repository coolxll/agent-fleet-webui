"""Security regression tests for SPA fallback and static directory boundary enforcement (Issue #64)."""

from __future__ import annotations

import pytest
from agent_fleet.main import WEB_DIST


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


def test_dotfile_inside_web_dist_blocked(client):
    """Even if a dotfile is accidentally placed inside WEB_DIST, it must NOT be served."""
    dotfile = WEB_DIST / ".secret_spa_test"
    try:
        dotfile.write_text("SENSITIVE_SECRET_TOKEN=xyz123")
        res = client.get("/.secret_spa_test")
        assert res.status_code in (403, 404)
        assert "SENSITIVE_SECRET_TOKEN" not in res.text
    finally:
        if dotfile.exists():
            dotfile.unlink()


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
    if (WEB_DIST / "favicon.svg").is_file():
        res = client.get("/favicon.svg")
        assert res.status_code == 200
        assert "<svg" in res.text or "svg" in res.headers.get("content-type", "")

    assets_dir = WEB_DIST / "assets"
    if assets_dir.is_dir():
        asset_files = [f for f in assets_dir.iterdir() if f.is_file()]
        if asset_files:
            sample_asset = asset_files[0]
            res = client.get(f"/assets/{sample_asset.name}")
            assert res.status_code == 200
            assert len(res.content) > 0


def test_api_endpoints_unaffected(client):
    """API endpoints remain fully operational and are not intercepted by SPA fallback."""
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json().get("ok") is True

    res = client.get("/api/settings")
    assert res.status_code == 200
    assert "db_path" in res.json() or "auth_required" in res.json()
