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
    """Resolve the SSH destination target operand (e.g., 'alias', 'user@alias', or '192.168.1.100').

    Resolution priority for destination operand:
    1. ssh_alias (OpenSSH alias from ~/.ssh/config, to preserve config block semantics)
    2. ssh_host (explicit IP or hostname)
    3. host_name (fleet Host name fallback)

    When both an alias and an explicit IP/hostname are present, the alias is used
    as the OpenSSH destination operand so that OpenSSH matches the `Host <alias>`
    block in ~/.ssh/config (inheriting IdentityFile, ProxyJump, etc.), while the
    network address is passed via -o HostName=<address> in build_ssh_command().

    If ssh_user is provided and not already present in the target string,
    it is prepended as 'user@target'.
    """
    raw = (ssh_alias or ssh_host or host_name or "").strip()
    clean_host, parsed_user, _ = parse_ssh_destination(raw)
    effective_user = (ssh_user or parsed_user or "").strip()

    if effective_user and clean_host:
        return f"{effective_user}@{clean_host}"
    return clean_host


def resolve_ssh_connection(
    ssh_host: str | None = None,
    ssh_alias: str | None = None,
    host_name: str | None = None,
    ssh_user: str | None = None,
    ssh_port: int | None = None,
) -> tuple[str, list[str]]:
    """Resolve the OpenSSH destination operand and required command-line options.

    Returns (destination_operand, extra_options).

    Semantics:
    - If BOTH an alias and an explicit network address are provided (and different):
      destination operand is the alias (inheriting Host configuration from ~/.ssh/config),
      and '-o HostName=<address>' is added to options.
    - If only an alias is present: destination operand is alias.
    - If only an address is present: destination operand is address.
    - If ssh_user is present: 'user@operand' is returned.
    """
    clean_alias, alias_user, alias_port = parse_ssh_destination(ssh_alias)
    clean_host, host_user, host_port = parse_ssh_destination(ssh_host)
    clean_hostname, hname_user, hname_port = parse_ssh_destination(host_name)

    effective_user = (ssh_user or alias_user or host_user or hname_user or "").strip()

    alias = clean_alias or (clean_hostname if clean_host and clean_hostname != clean_host else None)
    address = clean_host or (clean_hostname if not alias else None)

    options: list[str] = []

    if alias and address and alias != address:
        operand = alias
        options.append(f"HostName={address}")
    elif alias:
        operand = alias
    elif address:
        operand = address
    else:
        operand = clean_hostname or ""

    if effective_user and operand and "@" not in operand:
        operand = f"{effective_user}@{operand}"

    return operand, options


def build_ssh_command(
    target: str = "",
    port: int | None = None,
    user: str | None = None,
    connect_timeout: int = 5,
    remote_command: list[str] | str | None = None,
    batch_mode: bool = True,
    extra_options: list[str] | None = None,
    host_address: str | None = None,
    ssh_host: str | None = None,
    ssh_alias: str | None = None,
) -> list[str]:
    """Build a robust OpenSSH CLI command argv list.

    Connection & Precedence Semantics:
    1. Alias vs Address:
       - When BOTH an SSH alias and an explicit IP/hostname address are provided,
         the alias is passed as the destination operand (so OpenSSH matches the
         `Host <alias>` block in ~/.ssh/config, inheriting IdentityFile, ProxyJump, etc.),
         and '-o HostName=<address>' is added so OpenSSH connects to the explicit IP/address.
       - If only an alias is present, connects to alias.
       - If only an address is present, connects to address.
    2. User:
       - Explicit user is prepended to operand as 'user@operand'.
       - If omitted, operand has no user prefix, allowing ~/.ssh/config `User` to apply.
    3. Port:
       - Non-standard ports (!= 22) are passed via -p <port>.
       - Standard port 22 or None omits -p, allowing ~/.ssh/config `Port` to apply.
    """
    cmd = ["ssh"]
    if batch_mode:
        cmd.extend(["-o", "BatchMode=yes"])
    if connect_timeout:
        cmd.extend(["-o", f"ConnectTimeout={connect_timeout}"])

    opts = list(extra_options or [])

    # Resolve operand and address
    clean_target, target_user, target_port = parse_ssh_destination(target)
    clean_addr, addr_user, addr_port = parse_ssh_destination(host_address or ssh_host)
    clean_alias, alias_user, alias_port = parse_ssh_destination(ssh_alias)

    effective_port = port or target_port or addr_port or alias_port
    effective_user = user or target_user or alias_user or addr_user

    # Determine alias and address
    alias = clean_alias or (clean_target if clean_addr and clean_target != clean_addr else None)
    address = clean_addr or (clean_target if not alias else None)

    if alias and address and alias != address:
        operand = alias
        hostname_opt = f"HostName={address}"
        if not any(o.startswith("HostName=") for o in opts):
            opts.insert(0, hostname_opt)
    elif alias:
        operand = alias
    elif address:
        operand = address
    else:
        operand = clean_target or target

    for opt in opts:
        cmd.extend(["-o", opt])

    if effective_port and int(effective_port) != 22:
        cmd.extend(["-p", str(effective_port)])

    if effective_user and operand and "@" not in operand:
        final_target = f"{effective_user}@{operand}"
    else:
        final_target = operand or target

    cmd.append(final_target)

    if remote_command:
        if isinstance(remote_command, list):
            cmd.extend(remote_command)
        else:
            cmd.append(remote_command)

    return cmd
