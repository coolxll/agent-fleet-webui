"""Sanitization: nothing leaves this module unless it was explicitly allowed.

Rules implemented here:
  1. Secret-looking KEYS are dropped entirely (name kept only if it is a
     whitelisted structural key).
  2. Secret-looking VALUES are redacted even when the key looks innocent.
  3. URLs are reduced to scheme + host + port; paths and query strings are
     dropped because they routinely carry tokens.
  4. Unknown fields are never rendered by default: adapters record their
     names in unsupported_fields and drop their values.
"""

from __future__ import annotations

from urllib.parse import urlsplit

REDACTED = "***redacted***"
MASKED = "***masked***"

SECRET_KEY_MARKERS = (
    "token",
    "secret",
    "api_key",
    "apikey",
    "api-key",
    "api key",
    "password",
    "passwd",
    "pwd",
    "credential",
    "private_key",
    "privatekey",
    "access_key",
    "accesskey",
    "client_secret",
    "auth",
    "authorization",
    "bearer",
    "cookie",
    "session",
    "otp",
    "passphrase",
    "x-api-key",
    "sk-",
    "key",
)

# Keys that contain a marker but are safe to show structurally.
KEY_ALLOWLIST = (
    # Structural fields we emit ourselves; they hold names, never values.
    "env_keys",
    "secret_keys_redacted",
    "key_env",          # hermes: name of the env var holding the key, not the key
    "keystore",
)

SECRET_VALUE_PREFIXES = (
    "sk-",
    "sk_",
    "sk-ant-",
    "ghp_",
    "gho_",
    "ghu_",
    "ghs_",
    "github_pat_",
    "xoxb-",
    "xapp-",
    "aki",
    "aia",
    "glpat-",
    "shpat_",
    "sk_live_",
    "sk_test_",
    "rk_live_",
    "eyj",     # JWT header segment
)

SECRET_VALUE_CONTAINS = (
    "api-key: ",
    "x-api-key: ",
)

MAX_STRING_LEN = 240
MAX_ITEMS = 200


def is_secret_key(key: str) -> bool:
    low = str(key).lower()
    if any(a in low for a in KEY_ALLOWLIST):
        return False
    return any(m in low for m in SECRET_KEY_MARKERS)


def looks_like_secret(value) -> bool:
    if not isinstance(value, str):
        return False
    v = value.strip()
    low = v.lower()
    if not v:
        return False
    for prefix in SECRET_VALUE_PREFIXES:
        if low.startswith(prefix):
            # short values like "sk-" alone or "key" prefixes inside words
            if len(v) >= 8:
                return True
    for marker in SECRET_VALUE_CONTAINS:
        if marker in low:
            return True
    # "Bearer <token>" is a credential; the word bearer alone is not.
    if "bearer " in low:
        tail = low.split("bearer ", 1)[1].strip().split(" ")[0]
        if len(tail) >= 16 and all(c.isalnum() or c in "._-+" for c in tail):
            return True
    # long high-entropy hex/base64 blobs
    if len(v) >= 32 and all(c.isalnum() or c in "+/=-_" for c in v):
        digits = sum(1 for c in v if c.isdigit())
        if digits >= 4:
            return True
    return False


def mask_url(url) -> tuple[str, bool]:
    """Return (masked_url, changed).

    Keeps scheme://host[:port]/path and drops credentials, query strings and
    fragments, which is where tokens hide. A path segment that itself looks
    like a credential is dropped as well.
    """
    if not isinstance(url, str) or not url:
        return (None, False)
    raw = url.strip()
    if "://" not in raw:
        return (raw[: MAX_STRING_LEN], False)
    try:
        parts = urlsplit(raw)
    except ValueError:
        return (MASKED, True)
    changed = False
    netloc = parts.netloc
    if parts.username or parts.password:
        host = parts.hostname or ""
        netloc = f"{host}:{parts.port}" if parts.port else host
        changed = True
    path = "" if parts.path in ("", "/") else parts.path
    if path and any(looks_like_secret(s) for s in path.split("/") if s):
        path = ""
        changed = True
    if parts.query or parts.fragment:
        changed = True
    return (f"{parts.scheme}://{netloc}{path}", changed)


def redact_value(value):
    if isinstance(value, str):
        if looks_like_secret(value):
            return REDACTED
        if len(value) > MAX_STRING_LEN:
            return value[:MAX_STRING_LEN] + "...(+truncated)"
        return value
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return value
    return MASKED


def sanitize_object(obj, depth: int = 0):
    """Recursively scrub a structure. Secret keys vanish; values get redacted."""
    if depth > 6:
        return MASKED
    if isinstance(obj, dict):
        out = {}
        for i, (k, v) in enumerate(obj.items()):
            if i >= MAX_ITEMS:
                out["..."] = f"{len(obj) - MAX_ITEMS} more keys omitted"
                break
            if is_secret_key(k):
                continue
            out[str(k)[:80]] = sanitize_object(v, depth + 1)
        return out
    if isinstance(obj, list):
        return [sanitize_object(v, depth + 1) for v in obj[:MAX_ITEMS]]
    return redact_value(obj)


def scrub_env(env):
    """Env blocks are reported as key names only, never values."""
    if not isinstance(env, dict):
        return None
    out = {}
    for i, k in enumerate(env):
        if i >= MAX_ITEMS:
            out["..."] = "more env vars omitted"
            break
        out[str(k)[:80]] = REDACTED
    return out


def strip_unknown(obj, allowed_keys):
    """Keep only whitelisted keys; report the rest by name.

    Returns (clean_dict, dropped_keys).
    """
    if not isinstance(obj, dict):
        return {}, []
    clean, dropped = {}, []
    for k, v in obj.items():
        if k in allowed_keys:
            clean[k] = v
        else:
            dropped.append(str(k)[:60])
    return clean, sorted(set(dropped))
