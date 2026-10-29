"""SSH key inspection and diagnostic service.

Provides capabilities to inspect the controller machine's SSH public key
and diagnose SSH connectivity failures.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Search candidates in priority order (ed25519 is preferred modern standard)
SUPPORTED_PUBLIC_KEYS = ("id_ed25519.pub", "id_rsa.pub", "id_ecdsa.pub")


def is_safe_pub_path(target_path: Path | str, base_dir: Path | str) -> bool:
    """Validate that target_path is strictly inside base_dir and ends with .pub.

    Prevents directory traversal attacks and accidental private key exposure.
    """
    target = Path(target_path)
    base = Path(base_dir)

    # 1. Must explicitly have .pub suffix
    if target.suffix != ".pub":
        return False

    # 2. Strict directory containment check
    try:
        resolved_base = base.resolve()
        resolved_target = target.resolve()
        resolved_target.relative_to(resolved_base)
        return True
    except (ValueError, RuntimeError):
        return False


def read_public_key_file(pub_path: Path | str, base_dir: Path | str) -> str:
    """Read a public key file safely.

    Raises ValueError if path traversal is attempted or file does not have .pub suffix.
    """
    target = Path(pub_path)
    base = Path(base_dir)

    if not is_safe_pub_path(target, base):
        raise ValueError(
            f"Security restriction: Only .pub files within {base} may be accessed. "
            f"Attempted: {pub_path}"
        )

    resolved = target.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Public key file not found: {pub_path}")

    return resolved.read_text(encoding="utf-8").strip()


def parse_key_type(content: str, filename: str) -> str:
    """Extract key type from public key content or filename."""
    parts = content.split()
    if parts and parts[0].startswith(("ssh-", "ecdsa-")):
        return parts[0]
    # Fallback to key type from filename (e.g. id_ed25519.pub -> ed25519)
    name = Path(filename).name
    if name.endswith(".pub"):
        name = name[:-4]
    if name.startswith("id_"):
        name = name[3:]
    return name


def get_controller_public_key(ssh_dir: Path | str | None = None) -> dict[str, Any]:
    """Retrieve the controller machine's public SSH key.

    Searches for supported public keys in ~/.ssh (or the specified ssh_dir).
    Security guarantees:
    - Only files ending with '.pub' are accessed.
    - Strict path traversal validation is enforced.
    - Private keys are never touched.
    """
    if ssh_dir is None:
        base_dir = Path.home() / ".ssh"
    else:
        base_dir = Path(ssh_dir)

    if not base_dir.exists() or not base_dir.is_dir():
        return {
            "found": False,
            "instructions": "Run ssh-keygen -t ed25519 to generate a key pair.",
        }

    for filename in SUPPORTED_PUBLIC_KEYS:
        candidate_path = base_dir / filename
        if not is_safe_pub_path(candidate_path, base_dir):
            continue

        try:
            if candidate_path.resolve().is_file():
                content = read_public_key_file(candidate_path, base_dir)
                if not content:
                    continue
                key_type = parse_key_type(content, filename)
                return {
                    "found": True,
                    "key_type": key_type,
                    "public_key": content,
                    "filename": filename,
                }
        except (ValueError, OSError, UnicodeDecodeError) as exc:
            logger.debug("Skipping candidate key %s: %s", candidate_path, exc)
            continue

    return {
        "found": False,
        "instructions": "Run ssh-keygen -t ed25519 to generate a key pair.",
    }


def diagnose_ssh_failure(error_msg: str | None) -> dict[str, str]:
    """Parse and categorize common SSH connection failure reasons into suggestions.

    Categorizes:
    - Permission denied (publickey)
    - Connection refused
    - Operation timed out
    - Host key verification failed
    """
    if not error_msg:
        return {
            "category": "unknown",
            "suggestion": "SSH connection failed. Check host connectivity and SSH daemon status.",
        }

    err_lower = error_msg.lower()

    if "permission denied" in err_lower or "publickey" in err_lower:
        return {
            "category": "permission_denied",
            "suggestion": (
                "Permission denied (publickey): Please add the controller's public SSH key "
                "to ~/.ssh/authorized_keys on the remote host."
            ),
        }

    if "connection refused" in err_lower:
        return {
            "category": "connection_refused",
            "suggestion": (
                "Connection refused: Please verify that the SSH service (sshd) is running on the remote host "
                "and listening on the specified port."
            ),
        }

    if (
        "operation timed out" in err_lower
        or "timed out" in err_lower
        or "connection timed out" in err_lower
        or "timeout" in err_lower
    ):
        return {
            "category": "timeout",
            "suggestion": (
                "Operation timed out: Please check the remote host IP/hostname, SSH port, and verify that "
                "firewalls or security groups permit inbound traffic."
            ),
        }

    if "host key verification failed" in err_lower:
        return {
            "category": "host_key_verification_failed",
            "suggestion": (
                "Host key verification failed: Remote host identification has changed. "
                "Please check or update ~/.ssh/known_hosts on the controller machine."
            ),
        }

    return {
        "category": "unknown",
        "suggestion": f"SSH connection failed: {error_msg}. Check host settings and SSH server logs.",
    }
