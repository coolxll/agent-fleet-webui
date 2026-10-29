#!/usr/bin/env python3
"""Mirror trae2api-web's Trae CN access token into agent-fleet.

Why this exists: agent-fleet's `trae` quota account stores a *manual, static*
access token and never renews it (see services/trae_quota.py — it only reads
quota). Trae CN access tokens live ~14 days, so the row silently goes red with
"Trae login expired or was rejected" a fortnight after import.

trae2api-web on a remote host keeps renewing the account via refresh_token
and writes the fresh token back to its auths file. This script copies that token over.

Cron-friendly contract:
  * prints nothing and exits 0 when there is nothing to do
  * prints one line when it updates the credential
  * exit 1 = upstream (trae2api) token missing/expiring, 2 = read/transport
    failure, 3 = agent-fleet rejected the credential

Env overrides: TRAE_AUTH_SSH, TRAE_AUTH_PATH, AGENT_FLEET_API,
TRAE_SYNC_STATE, TRAE_SYNC_MIN_HOURS.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any, NoReturn

HOST = os.environ.get("TRAE_AUTH_SSH", "trae-host")
AUTH_PATH = os.environ.get(
    "TRAE_AUTH_PATH",
    "/data/trae2api-web/data/auths/trae-default.json",
)
API = os.environ.get("AGENT_FLEET_API", "http://127.0.0.1:8000").rstrip("/")
STATE = pathlib.Path(
    os.environ.get("TRAE_SYNC_STATE", os.path.expanduser("~/.agent-fleet/trae-sync.json"))
)
MIN_REMAINING_H = float(os.environ.get("TRAE_SYNC_MIN_HOURS", "12"))


def fail(code: int, message: str) -> NoReturn:
    print(f"trae-credential-sync: {message}", file=sys.stderr)
    raise SystemExit(code)


def read_remote_credential() -> dict:
    cmd = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
        HOST,
        f"sudo cat {AUTH_PATH}",
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
    except subprocess.TimeoutExpired:
        fail(2, f"ssh {HOST} timed out")
    if out.returncode != 0:
        fail(2, f"ssh {HOST} failed: {out.stderr.strip()[:200]}")
    try:
        data = json.loads(out.stdout)
        auth = data["auth"]
        token = auth["accessToken"]
        uid = str(data.get("account", {}).get("uid") or "")
    except (ValueError, KeyError, TypeError) as exc:
        fail(2, f"auths file unparsable on {HOST}: {exc}")
    if not isinstance(token, str) or token.count(".") != 2:
        fail(2, "trae2api accessToken is not a JWT")
    return {"token": token, "uid": uid, "expires_at": auth.get("expiresAt")}


def jwt_exp(token: str) -> int | None:
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        claims = json.loads(base64.urlsafe_b64decode(part))
    except Exception:
        return None
    exp = claims.get("exp")
    return int(exp) if isinstance(exp, (int, float)) else None


def http_json(path: str, payload: dict | None = None, timeout: int = 60) -> tuple[int, Any]:
    req = urllib.request.Request(
        API + path,
        data=None if payload is None else json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="GET" if payload is None else "POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        body = exc.read()[:400].decode("utf-8", "ignore")
        return exc.code, {"error": body}
    except (urllib.error.URLError, OSError) as exc:
        fail(2, f"agent-fleet unreachable at {API}: {exc}")


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def save_state(data: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(STATE)


def main() -> int:
    cred = read_remote_credential()
    token = cred["token"]
    fingerprint = hashlib.sha256(token.encode()).hexdigest()
    exp = jwt_exp(token)
    now = time.time()

    if exp is not None:
        remaining_h = (exp - now) / 3600
        if remaining_h <= 0:
            fail(1, f"trae2api token already expired {abs(remaining_h):.1f}h ago; "
                    "trae2api's refresh_token renewal is not working")
        if remaining_h < MIN_REMAINING_H:
            print(f"trae-credential-sync: warning — trae2api token expires in "
                  f"{remaining_h:.1f}h (trae2api renewal looks stalled)", file=sys.stderr)

    status, listed = http_json("/api/quota-accounts")
    if status != 200:
        fail(2, f"agent-fleet /api/quota-accounts -> {status} {listed}")
    trae_rows = [a for a in listed.get("accounts", []) if a.get("provider") == "trae"]
    state = load_state()
    row = trae_rows[0] if trae_rows else None
    healthy = bool(row) and row.get("status") == "ok"
    already = state.get("fingerprint") == fingerprint

    if healthy and already:
        return 0  # nothing to do

    if not row:
        # No trae row yet: create one (agent-fleet verifies before saving).
        label = None
    else:
        label = row.get("label")

    status, body = http_json(
        "/api/quota-accounts",
        {"provider": "trae", "label": label, "credential": token},
    )
    if status != 200:
        fail(3, f"agent-fleet rejected the credential (HTTP {status}): {body.get('error', body)}")

    state.update({
        "fingerprint": fingerprint,
        "uid": cred["uid"],
        "token_exp": exp,
        "account_id": body.get("id"),
        "synced_at": int(now),
    })
    save_state(state)

    quota = ((body.get("quota") or {}).get("accounts") or [{}])[0]
    bucket = (((quota.get("groups") or [{}])[0]).get("buckets") or [{}])[0]
    remaining = bucket.get("remaining")
    total = bucket.get("total")
    exp_txt = time.strftime("%Y-%m-%d %H:%M", time.localtime(exp)) if exp else "?"
    print(
        f"trae-credential-sync: updated agent-fleet trae credential "
        f"(uid {cred['uid']}, token exp {exp_txt}, credits {remaining}/{total})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
