"""SSH target resolution and command building utilities.

Provides a unified contract for resolving Host and Runtime connection targets,
ports, and user parameters, maintaining full backwards compatibility with OpenSSH
aliases and ~/.ssh/config declarations.
"""

from __future__ import annotations

from typing import Any


def parse_ssh_destination(
    raw_host: str | None,
    default_user: str | None = None,
    default_port: int | None = None,
) -> tuple[str, str | None, int | None]:
    """Parse an SSH destination string like [user@]host[:port].

    Returns (clean_host, user, port).
    """
    if not raw_host:
        return "", default_user, default_port

    target = raw_host.strip()
    user = default_user
    port = default_port

    # Handle user@ prefix if present
    if "@" in target:
        user_part, target = target.split("@", 1)
        if user_part:
            user = user_part

    # Handle :port suffix if present (avoid IPv6 [::1]:port confusion)
    if ":" in target and not target.endswith("]"):
        parts = target.rsplit(":", 1)
        if parts[1].isdigit():
            target = parts[0]
            port = int(parts[1])

    return target, user, port


def resolve_ssh_target(
    ssh_host: str | None = None,
    ssh_alias: str | None = None,
    host_name: str | None = None,
    ssh_user: str | None = None,
) -> str:
    """Resolve the SSH destination target (e.g., 'user@192.168.1.100' or 'alias').

    Resolution priority for host address:
    1. ssh_host (explicit IP or hostname)
    2. ssh_alias (SSH config alias)
    3. host_name (fleet Host name)

    If ssh_user is provided and not already present in the host string,
    it is prepended as 'user@host'.
    """
    raw = (ssh_host or ssh_alias or host_name or "").strip()
    clean_host, parsed_user, _ = parse_ssh_destination(raw)
    effective_user = (ssh_user or parsed_user or "").strip()

    if effective_user and clean_host:
        return f"{effective_user}@{clean_host}"
    return clean_host


def build_ssh_command(
    target: str,
    port: int | None = None,
    user: str | None = None,
    connect_timeout: int = 5,
    remote_command: list[str] | str | None = None,
    batch_mode: bool = True,
    extra_options: list[str] | None = None,
) -> list[str]:
    """Build a robust OpenSSH CLI command argv list.

    Non-standard ports (!= 22) are passed via -p.
    Users are prepended to target as user@host if not already present.
    """
    cmd = ["ssh"]
    if batch_mode:
        cmd.extend(["-o", "BatchMode=yes"])
    if connect_timeout:
        cmd.extend(["-o", f"ConnectTimeout={connect_timeout}"])
    if extra_options:
        for opt in extra_options:
            cmd.extend(["-o", opt])

    clean_host, parsed_user, parsed_port = parse_ssh_destination(target)
    effective_port = port or parsed_port
    effective_user = user or parsed_user

    if effective_port and int(effective_port) != 22:
        cmd.extend(["-p", str(effective_port)])

    if effective_user and clean_host and "@" not in clean_host:
        final_target = f"{effective_user}@{clean_host}"
    else:
        final_target = clean_host or target

    cmd.append(final_target)

    if remote_command:
        if isinstance(remote_command, list):
            cmd.extend(remote_command)
        else:
            cmd.append(remote_command)

    return cmd
