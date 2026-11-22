from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from agent_fleet.settings import Settings, get_settings


def test_settings_default_values(monkeypatch):
    """Verify default Settings values and configuration metadata."""
    for key in list(os.environ):
        if key.upper().startswith("AGENT_FLEET_"):
            monkeypatch.delenv(key, raising=False)

    settings = Settings(_env_file=None)
    assert settings.host == "127.0.0.1"
    assert settings.port == 8000
    assert settings.auto_scan_enabled is False
    assert settings.auto_scan_interval_minutes == 60
    assert settings.ssh_connect_timeout == 10
    assert settings.allow_anonymous_readonly is True
    assert settings.api_secret is None
    assert settings.is_auth_enabled is False

    # Check SettingsConfigDict options
    config = settings.model_config
    assert config.get("env_prefix") == "AGENT_FLEET_"
    assert config.get("env_file") == ".env"
    assert config.get("env_file_encoding") == "utf-8"
    assert config.get("case_sensitive") is False
    assert config.get("extra") == "ignore"


def test_settings_load_from_env_file(tmp_path):
    """Verify that Settings loads values from a specified .env file."""
    env_content = """
# Test configuration file
AGENT_FLEET_HOST=0.0.0.0
AGENT_FLEET_PORT=9090
AGENT_FLEET_DATA_DIR={data_dir}
AGENT_FLEET_INVENTORY_PATHS={data_dir}/inv1.yaml,inv2.yaml
AGENT_FLEET_API_SECRET=super-secure-token-123
AGENT_FLEET_ALLOW_ANONYMOUS_READONLY=false
AGENT_FLEET_AUTO_SCAN_ENABLED=true
AGENT_FLEET_AUTO_SCAN_INTERVAL_MINUTES=45
AGENT_FLEET_SSH_CONNECT_TIMEOUT=5
# Extra unknown variable should be ignored without error
AGENT_FLEET_UNKNOWN_CUSTOM_FIELD=ignore_me
""".format(data_dir=str(tmp_path / "custom_data"))

    env_file = tmp_path / ".env"
    env_file.write_text(env_content, encoding="utf-8")

    settings = Settings(_env_file=env_file)

    assert settings.host == "0.0.0.0"
    assert settings.port == 9090
    assert settings.data_dir == tmp_path / "custom_data"
    assert settings.inventory_paths == [
        tmp_path / "custom_data" / "inv1.yaml",
        Path("inv2.yaml"),
    ]
    assert settings.api_secret == "super-secure-token-123"
    assert settings.is_auth_enabled is True
    assert settings.allow_anonymous_readonly is False
    assert settings.auto_scan_enabled is True
    assert settings.auto_scan_interval_minutes == 45
    assert settings.ssh_connect_timeout == 5


def test_settings_inventory_paths_json_format(tmp_path):
    """Verify that inventory_paths also accepts JSON array syntax from env/file."""
    env_file = tmp_path / ".env"
    paths_json = json.dumps(["data/foo.yaml", "bar.yml"])
    env_file.write_text(f'AGENT_FLEET_INVENTORY_PATHS={paths_json}\n', encoding="utf-8")

    settings = Settings(_env_file=env_file)
    assert settings.inventory_paths == [Path("data/foo.yaml"), Path("bar.yml")]


def test_settings_env_var_precedence_over_env_file(tmp_path, monkeypatch):
    """Verify system environment variables override values in .env."""
    env_file = tmp_path / ".env"
    env_file.write_text("AGENT_FLEET_PORT=8888\nAGENT_FLEET_HOST=10.0.0.1\n", encoding="utf-8")

    monkeypatch.setenv("AGENT_FLEET_PORT", "9999")

    settings = Settings(_env_file=env_file)
    # Environment variable should override .env
    assert settings.port == 9999
    # Unset env var should fall back to .env
    assert settings.host == "10.0.0.1"


def test_settings_case_insensitive_env(monkeypatch):
    """Verify case_sensitive=False allows lower or mixed case env variables."""
    monkeypatch.setenv("agent_fleet_port", "8123")
    monkeypatch.setenv("agent_fleet_ssh_connect_timeout", "15")

    settings = Settings(_env_file=None)
    assert settings.port == 8123
    assert settings.ssh_connect_timeout == 15


def test_settings_masking_and_safe_dict():
    """Verify repr/str masking and safe_dict secret stripping."""
    settings = Settings(_env_file=None, api_secret="very-secret-token")
    assert "very-secret-token" not in repr(settings)
    assert "'***'" in repr(settings)
    assert "very-secret-token" not in str(settings)

    safe = settings.safe_dict()
    assert "api_secret" not in safe
    assert safe["auth_required"] is True


def test_settings_webhook_url_masking():
    """Verify webhook_url tokens, credentials, and query secrets are masked in safe_dict and repr."""
    cases = [
        (
            "https://hooks.slack.com/services/T00000000/B00000000/superSecretSlackToken",
            "superSecretSlackToken",
        ),
        (
            "https://discord.com/api/webhooks/123456789/discordSecretToken999",
            "discordSecretToken999",
        ),
        (
            "https://user:s3cr3tpass@hooks.example.com/notify?token=querySecret123#fragSecret",
            "s3cr3tpass",
        ),
        (
            "https://hooks.example.com/notify?token=querySecret123",
            "querySecret123",
        ),
        (
            "https://hooks.example.com/bot/a1b2c3d4e5f6g7h8i9j0k1l2",
            "a1b2c3d4e5f6g7h8i9j0k1l2",
        ),
    ]
    for raw_url, secret_fragment in cases:
        s = Settings(_env_file=None, webhook_url=raw_url, webhook_secret="whsec_raw_secret")
        safe = s.safe_dict()
        assert safe["webhook_url"] is not None
        assert secret_fragment not in safe["webhook_url"]
        assert "whsec_raw_secret" not in repr(s)
        assert secret_fragment not in repr(s)

    empty = Settings(_env_file=None, webhook_url=None)
    assert empty.safe_dict()["webhook_url"] is None
    assert "webhook_url=None" in repr(empty)
