import asyncio
import base64
import json
from datetime import datetime, timezone

import httpx
import pytest
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from agent_fleet.services import qoder_quota as qoder
from agent_fleet.services.quota_providers import fetch_account


def _encrypt_payload(payload: dict, machine_id: str) -> str:
    key = machine_id[:16].encode("utf-8")
    raw = json.dumps(payload).encode("utf-8")
    padder = padding.PKCS7(128).padder()
    padded_data = padder.update(raw) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.CBC(key))
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(padded_data) + encryptor.finalize()
    return base64.b64encode(ciphertext).decode("utf-8")


def test_decrypt_user_payload():
    mid = "1234567890abcdef-machine-id"
    payload = {
        "uid": "user-uuid-123",
        "name": "张三",
        "access_token": "qoder-token-xyz",
        "organization_name": "TestOrg",
    }
    b64 = _encrypt_payload(payload, mid)
    decrypted = qoder._decrypt_user_payload(b64, mid)
    assert decrypted["uid"] == "user-uuid-123"
    assert decrypted["name"] == "张三"
    assert decrypted["access_token"] == "qoder-token-xyz"


def test_parse_credential_plain():
    res = qoder.parse_credential("my-token-123")
    assert res["access_token"] == "my-token-123"


def test_parse_credential_json():
    data = {
        "access_token": "qoder-at-456",
        "security_oauth_token": "sec-token-789",
        "name": "李四",
        "organization_name": "MyOrg",
    }
    res = qoder.parse_credential(json.dumps(data))
    assert res["access_token"] == "qoder-at-456"
    assert res["security_oauth_token"] == "sec-token-789"
    assert res["name"] == "李四"


def test_parse_credential_encrypted_envelope():
    mid = "fedcba0987654321-mac-id"
    payload = {
        "access_token": "token-from-file",
        "name": "王五",
        "organization_name": "CompanyCorp",
    }
    b64 = _encrypt_payload(payload, mid)
    envelope = {"machine_id": mid, "user": b64}
    res = qoder.parse_credential(json.dumps(envelope))
    assert res["access_token"] == "token-from-file"
    assert res["name"] == "王五"
    assert res["organization_name"] == "CompanyCorp"


def test_fetch_qoder_success(monkeypatch):
    def handler(request: httpx.Request):
        assert request.url.host == "openapi.qoder.com.cn"
        assert request.headers["Authorization"] == "Bearer mock-qoder-token"
        assert request.headers["X-Security-Oauth-Token"] == "mock-sec-token"

        if request.url.path == "/sash/api/v2/me/usage":
            return httpx.Response(
                200,
                json={
                    "displayMode": "qoder",
                    "qoderUsage": {
                        "userId": "qoder-user-1",
                        "userType": "teams",
                        "usageType": "credits",
                        "expiresAt": 1795017600000,
                        "userQuota": {
                            "total": 3000,
                            "used": 150,
                            "remaining": 2850,
                            "percentage": 0.05,
                            "unit": "credits",
                        },
                        "orgResourcePackage": {
                            "used": 0,
                            "remaining": 0,
                            "percentage": 0,
                            "cap": 3000,
                            "available": False,
                        },
                    },
                },
            )
        if request.url.path == "/api/v1/userinfo":
            return httpx.Response(
                200,
                json={
                    "id": "qoder-user-1",
                    "name": "徐凌龙",
                    "organization_name": "dongfu-qoder-cn-teams",
                },
            )
        if request.url.path == "/api/v2/user/plan":
            return httpx.Response(
                200,
                json={
                    "plan_tier_name": "Teams",
                },
            )
        return httpx.Response(404)

    orig_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: orig_client(transport=httpx.MockTransport(handler), **kw),
    )

    cred = {
        "access_token": "mock-qoder-token",
        "security_oauth_token": "mock-sec-token",
    }
    result = asyncio.run(fetch_account("qodercn", json.dumps(cred)))

    assert result["identity"]["name"] == "徐凌龙 (dongfu-qoder-cn-teams)"
    assert result["identity"]["plan"] == "Qoder Teams"
    account = result["quota"]["accounts"][0]
    assert account["min_remaining_fraction"] == 0.95
    group = account["groups"][0]
    bucket = group["buckets"][0]
    assert bucket["remaining"] == 2850
    assert bucket["used"] == 150
    assert bucket["total"] == 3000
    assert len(group["packages"]) == 1
    assert len(group["daily_consumption"]) == 1
