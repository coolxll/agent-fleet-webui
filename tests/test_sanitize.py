"""Sanitization rules: the security contract of the whole product."""

from __future__ import annotations

import json

from agent_fleet.adapters.sanitize import (
    is_secret_key,
    looks_like_secret,
    mask_url,
    redact_value,
    sanitize_object,
    scrub_env,
    strip_unknown,
)


def test_secret_keys_are_dropped():
    for key in (
        "apiKey",
        "api_key",
        "API_KEY",
        "token",
        "accessToken",
        "password",
        "Authorization",
        "X-Api-Key",
        "client_secret",
        "privateKey",
        "sessionId",
        "cookie",
    ):
        assert is_secret_key(key), key


def test_structural_keys_survive():
    for key in ("env_keys", "key_env", "keystore", "model", "baseURL", "command"):
        assert not is_secret_key(key), key


def test_secret_values_are_redacted():
    for value in (
        "sk-ant-api03-abcdefghijklmnopqrstuvwx",
        "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
        "github_pat_11ABCDEFG0123456789abcdefg",
        "xoxb-1234567890-1234567890-abcdefghijkl",
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9abc",
        "AKIAIOSFODNN7EXAMPLE",
    ):
        assert looks_like_secret(value), value


def test_innocent_values_survive():
    for value in (
        "gpt-5.1",
        "https://api.example.com/v1",
        "/usr/local/bin/codex",
        "workspace-write",
        "1.2.3",
        "npm (nvm)",
    ):
        assert not looks_like_secret(value), value


def test_bearer_word_alone_is_not_a_secret():
    assert not looks_like_secret("config contains a bearer token field")
    assert looks_like_secret("Bearer sk-abcdefghijklmnopqrst")


def test_url_masking():
    assert mask_url("https://api.example.com/v1?key=secret") == (
        "https://api.example.com/v1",
        True,
    )
    assert mask_url("https://user:pw@api.example.com/v1") == ("https://api.example.com/v1", True)
    assert mask_url("https://api.example.com") == ("https://api.example.com", False)
    assert mask_url("http://10.0.0.5:8317") == ("http://10.0.0.5:8317", False)
    assert mask_url("https://api.example.com/sk-abcdefghijklmnop/v1") == (
        "https://api.example.com",
        True,
    )
    assert mask_url(None) == (None, False)


def test_nested_secret_is_dropped_from_structure():
    data = {
        "model": "gpt-5",
        "provider": {
            "npm": "@ai-sdk/openai-compatible",
            "options": {"apiKey": "sk-fixture-abcdefghijklmnop", "baseURL": "https://x.example.com"},
        },
    }
    clean = sanitize_object(data)
    assert clean["model"] == "gpt-5"
    assert "apiKey" not in json.dumps(clean)
    assert clean["provider"]["options"]["baseURL"] == "https://x.example.com"
    assert clean["provider"]["npm"] == "@ai-sdk/openai-compatible"


def test_env_blocks_report_names_only():
    clean = scrub_env({"OPENAI_API_KEY": "sk-real-abcdefghijklmnop", "REGION": "eu"})
    assert clean == {"OPENAI_API_KEY": "***redacted***", "REGION": "***redacted***"}
    assert "sk-real" not in json.dumps(clean)


def test_values_are_redacted_even_under_innocent_keys():
    clean = sanitize_object({"note": "ghp_abcdefghijklmnopqrstuvwxyz012345"})
    assert clean["note"].startswith("***")


def test_long_strings_are_truncated():
    clean = sanitize_object({"x": "a" * 500})
    assert clean["x"].endswith("(+truncated)")
    assert len(clean["x"]) < 300


def test_unknown_keys_are_named_but_not_shown():
    clean, dropped = strip_unknown(
        {"model": "m", "apiKey": "sk-abcdefghijklmnop", "other": 1}, ["model"]
    )
    assert clean == {"model": "m"}
    assert set(dropped) == {"apiKey", "other"}
    assert "sk-" not in json.dumps(clean)


def test_deep_nesting_is_bounded():
    node = {"a": 1}
    for _ in range(20):
        node = {"n": node}
    assert "***" in json.dumps(sanitize_object(node))
