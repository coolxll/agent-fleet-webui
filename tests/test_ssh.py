"""Unit tests for OpenSSH connection semantics, destination parsing, and command construction.

Verifies Issue #50: OpenSSH alias connection semantics are preserved when both alias
and explicit network address are configured (-o HostName=<address> and alias operand).
"""

from __future__ import annotations

import pytest

from agent_fleet.remotes.ssh import (
    build_ssh_command,
    parse_ssh_destination,
    resolve_ssh_connection,
    resolve_ssh_target,
)


def test_parse_ssh_destination():
    # Empty or None
    assert parse_ssh_destination(None) == ("", None, None)
    assert parse_ssh_destination("") == ("", None, None)

    # Clean host
    assert parse_ssh_destination("192.168.1.10") == ("192.168.1.10", None, None)
    assert parse_ssh_destination("my-alias") == ("my-alias", None, None)

    # user@host
    assert parse_ssh_destination("deploy@192.168.1.10") == ("192.168.1.10", "deploy", None)
    assert parse_ssh_destination("admin@bastion") == ("bastion", "admin", None)

    # host:port
    assert parse_ssh_destination("192.168.1.10:2222") == ("192.168.1.10", None, 2222)
    assert parse_ssh_destination("bastion:8022") == ("bastion", None, 8022)

    # user@host:port
    assert parse_ssh_destination("deploy@192.168.1.10:2222") == ("192.168.1.10", "deploy", 2222)

    # Defaults
    assert parse_ssh_destination("host", default_user="fallback", default_port=22) == (
        "host",
        "fallback",
        22,
    )


def test_resolve_ssh_target_alias_precedence():
    """When both alias and address are present, resolve_ssh_target selects the alias."""
    # Both alias and IP
    assert (
        resolve_ssh_target(ssh_host="172.20.0.10", ssh_alias="corp172", ssh_user="devuser")
        == "devuser@corp172"
    )
    assert resolve_ssh_target(ssh_host="172.20.0.10", ssh_alias="corp172") == "corp172"

    # Only alias
    assert resolve_ssh_target(ssh_alias="corp172", ssh_user="devuser") == "devuser@corp172"
    assert resolve_ssh_target(ssh_alias="corp172") == "corp172"

    # Only address
    assert resolve_ssh_target(ssh_host="172.20.0.10", ssh_user="devuser") == "devuser@172.20.0.10"
    assert resolve_ssh_target(ssh_host="172.20.0.10") == "172.20.0.10"

    # Fallback to host_name
    assert resolve_ssh_target(host_name="fallback-box") == "fallback-box"
    assert resolve_ssh_target() == ""


def test_resolve_ssh_connection():
    """Verify resolve_ssh_connection yields destination operand and -o HostName option."""
    # Both alias and address
    operand, opts = resolve_ssh_connection(
        ssh_alias="corp-bastion",
        ssh_host="10.200.0.5",
        ssh_user="sysadmin",
    )
    assert operand == "sysadmin@corp-bastion"
    assert opts == ["HostName=10.200.0.5"]

    # Only alias -> no HostName option
    operand, opts = resolve_ssh_connection(ssh_alias="corp-bastion")
    assert operand == "corp-bastion"
    assert opts == []

    # Only address -> no HostName option
    operand, opts = resolve_ssh_connection(ssh_host="10.200.0.5")
    assert operand == "10.200.0.5"
    assert opts == []


def test_build_ssh_command_both_alias_and_address():
    """When both alias and address are configured, command uses alias operand and -o HostName=<address>."""
    cmd = build_ssh_command(
        target="corp-bastion",
        host_address="10.200.0.5",
        user="sysadmin",
        port=22,
    )
    assert cmd[0] == "ssh"
    assert "HostName=10.200.0.5" in cmd
    hn_idx = cmd.index("HostName=10.200.0.5")
    assert cmd[hn_idx - 1] == "-o"
    # Destination operand is the alias, preserving OpenSSH Host block configuration
    assert cmd[-1] == "sysadmin@corp-bastion"
    # Port 22 should not append -p
    assert "-p" not in cmd


def test_build_ssh_command_explicit_ssh_alias_and_ssh_host():
    """Passing ssh_alias and ssh_host parameters produces alias operand and HostName option."""
    cmd = build_ssh_command(
        ssh_alias="gpu-node",
        ssh_host="192.168.10.88",
        port=8022,
    )
    assert "HostName=192.168.10.88" in cmd
    assert "-p" in cmd
    p_idx = cmd.index("-p")
    assert cmd[p_idx + 1] == "8022"
    assert cmd[-1] == "gpu-node"


def test_build_ssh_command_only_alias():
    """When only an alias is provided, connects to alias without HostName option."""
    cmd = build_ssh_command(target="workstation-alias")
    assert cmd[-1] == "workstation-alias"
    assert not any(arg.startswith("HostName=") for arg in cmd)


def test_build_ssh_command_only_address():
    """When only an address is provided, connects to address without HostName option."""
    cmd = build_ssh_command(target="192.168.1.50")
    assert cmd[-1] == "192.168.1.50"
    assert not any(arg.startswith("HostName=") for arg in cmd)


def test_build_ssh_command_alias_equals_address():
    """When alias and address are identical strings, no redundant HostName option is injected."""
    cmd = build_ssh_command(target="10.0.0.1", host_address="10.0.0.1")
    assert cmd[-1] == "10.0.0.1"
    assert not any(arg.startswith("HostName=") for arg in cmd)


def test_build_ssh_command_user_precedence():
    """Verify precedence between explicit user argument, embedded user, and alias config."""
    # Explicit user overrides operand without user
    cmd1 = build_ssh_command(target="my-alias", user="override_user")
    assert cmd1[-1] == "override_user@my-alias"

    # Embedded user in target string
    cmd2 = build_ssh_command(target="embedded_user@my-alias")
    assert cmd2[-1] == "embedded_user@my-alias"

    # No user specified: operand remains clean to inherit ~/.ssh/config User
    cmd3 = build_ssh_command(target="my-alias")
    assert cmd3[-1] == "my-alias"
    assert "@" not in cmd3[-1]


def test_build_ssh_command_port_precedence():
    """Verify non-standard ports emit -p <port> while port 22 or None omits -p."""
    # Custom port
    cmd1 = build_ssh_command(target="my-alias", port=2222)
    assert "-p" in cmd1
    p_idx = cmd1.index("-p")
    assert cmd1[p_idx + 1] == "2222"

    # Embedded port in target
    cmd2 = build_ssh_command(target="my-alias:3333")
    assert "-p" in cmd2
    p_idx = cmd2.index("-p")
    assert cmd2[p_idx + 1] == "3333"

    # Port 22: -p omitted so ~/.ssh/config Port is not overridden
    cmd3 = build_ssh_command(target="my-alias", port=22)
    assert "-p" not in cmd3

    # Port None: -p omitted
    cmd4 = build_ssh_command(target="my-alias", port=None)
    assert "-p" not in cmd4


def test_build_ssh_command_batch_mode_timeout_and_remote_command():
    """Verify batch mode, timeout, extra options, and remote commands."""
    cmd = build_ssh_command(
        target="my-server",
        connect_timeout=10,
        batch_mode=False,
        extra_options=["StrictHostKeyChecking=no", "ServerAliveInterval=30"],
        remote_command=["uname", "-a"],
    )
    assert "BatchMode=yes" not in cmd
    assert "ConnectTimeout=10" in cmd
    assert "StrictHostKeyChecking=no" in cmd
    assert "ServerAliveInterval=30" in cmd
    assert cmd[-3:] == ["my-server", "uname", "-a"]

    # String remote command
    cmd_str = build_ssh_command(target="my-server", remote_command="echo hello")
    assert cmd_str[-2:] == ["my-server", "echo hello"]
