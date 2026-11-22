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
    assert cmd_str[-3:] == ["--", "my-server", "echo hello"]


@pytest.mark.parametrize(
    "bad_value",
    [
        "-oProxyCommand=id",
        "-Jevil",
        "user@-oProxyCommand=id",
        "host with spaces",
        "host\nProxyCommand=id",
        "host\rname",
        "host\x00name",
        "host\tname",
    ],
)
def test_ssh_option_injection_rejected(bad_value):
    """Reject leading '-', whitespace, and control characters in SSH targets/options."""
    with pytest.raises(ValueError):
        build_ssh_command(target=bad_value)
    with pytest.raises(ValueError):
        resolve_ssh_target(ssh_host=bad_value)
    with pytest.raises(ValueError):
        resolve_ssh_connection(ssh_alias=bad_value)


def test_build_ssh_command_inserts_end_of_options_marker():
    """Verify '--' always precedes the SSH destination operand."""
    cmd = build_ssh_command(target="corp-bastion", user="sysadmin", port=2222)
    assert cmd[-2:] == ["--", "sysadmin@corp-bastion"]


@pytest.mark.asyncio
async def test_ssh_runner_end_of_options_and_validation(monkeypatch):
    """Verify _SshBase rejects malicious targets and inserts '--' before destination."""
    from agent_fleet.remotes import executor

    with pytest.raises(ValueError):
        executor.SSHRunner("-oProxyCommand=id")

    captured: list[list[str]] = []

    async def fake_run(argv, stdin_data, timeout):
        captured.append(argv)
        return executor.ExecResult(stdout="ok", exit_code=0)

    monkeypatch.setattr(executor, "_run", fake_run)

    runner = executor.SSHRunner("dev@box.local", port=22022)
    await runner.run_command(["uname", "-a"], timeout=5)
    await runner.run_stdin(["sh", "-s"], "echo hi", timeout=5)

    assert len(captured) == 2
    for argv in captured:
        idx = argv.index("--")
        assert argv[idx + 1] == "dev@box.local"


def test_wsl_runner_rejects_shell_metacharacters():
    """Verify WslRunner._wrap rejects shell metacharacters and accepts valid identifiers."""
    from agent_fleet.remotes.executor import LocalRunner, WslRunner

    parent = LocalRunner()
    valid = WslRunner(parent, "Ubuntu-22.04", "dev_user.1")
    assert valid._wrap(["sh", "-s"]) == ["wsl.exe", "-d", "Ubuntu-22.04", "-u", "dev_user.1", "--", "sh", "-s"]

    for bad_distro in ("Ubuntu; whoami", "Ubuntu$(id)", "Ubuntu|cat", "Ubuntu`id`", "-Ubuntu", ""):
        with pytest.raises(ValueError):
            WslRunner(parent, bad_distro)._wrap(["sh", "-s"])

    for bad_user in ("root;id", "user name", "$(whoami)", "-u"):
        with pytest.raises(ValueError):
            WslRunner(parent, "Ubuntu-22.04", bad_user)._wrap(["sh", "-s"])


def test_sqlite_inventory_rejects_ssh_and_wsl_injection(fleet_db):
    """Verify create_host and update_host reject SSH option injection and WSL metacharacters."""
    from agent_fleet.services import sqlite_inventory

    for bad_payload in (
        {"name": "-oProxyCommand=id"},
        {"name": "valid-host", "hostname": "-Jevil"},
        {"name": "valid-host", "ssh_host": "10.0.0.1\nProxyCommand=id"},
        {"name": "valid-host", "ssh_alias": "bad alias"},
        {"name": "valid-host", "ssh_user": "-root"},
        {"name": "valid-host", "wsl": [{"distro": "Ubuntu;id"}]},
        {"name": "valid-host", "wsl": [{"distro": "Ubuntu-22.04", "user": "root&whoami"}]},
    ):
        with pytest.raises(ValueError):
            sqlite_inventory.create_host(bad_payload)

    created = sqlite_inventory.create_host({"name": "safe-validation-host", "hostname": "10.0.0.5"})
    try:
        for bad_update in (
            {"hostname": "-oProxyCommand=id"},
            {"ssh_host": "host\x00evil"},
            {"ssh_user": "user with space"},
            {"wsl_distro": "Ubuntu;rm"},
            {"wsl_user": "root|id"},
        ):
            with pytest.raises(ValueError):
                sqlite_inventory.update_host(created["id"], bad_update)
    finally:
        sqlite_inventory.delete_host(created["id"])


@pytest.mark.asyncio
async def test_executor_post_kill_timeout_closes_transports(monkeypatch):
    """Verify _run does not hang if post-kill communicate() times out (e.g. grandchild holding pipes)."""
    import asyncio
    from agent_fleet.remotes import executor

    closed_transports: list[str] = []

    class FakeTransport:
        def __init__(self, label: str):
            self.label = label

        def close(self):
            closed_transports.append(self.label)

    class FakeStream:
        def __init__(self, label: str, use_private: bool = False):
            if use_private:
                self._transport = FakeTransport(label)
            else:
                self.transport = FakeTransport(label)
            self.closed = False

        def close(self):
            self.closed = True

    class HangingProc:
        def __init__(self):
            self.stdin = FakeStream("stdin", use_private=False)
            self.stdout = FakeStream("stdout", use_private=True)
            self.stderr = FakeStream("stderr", use_private=True)
            self.killed = False
            self.returncode = -9

        def kill(self):
            self.killed = True

        async def communicate(self, input=None):
            await asyncio.sleep(60)
            return b"", b""

        async def wait(self):
            return -9

    proc = HangingProc()

    async def fake_create_subprocess_exec(*argv, **kwargs):
        return proc

    real_wait_for = asyncio.wait_for

    async def fast_wait_for(coro, timeout):
        # Clamp timeouts so test runs instantly while still exercising real TimeoutError paths
        return await real_wait_for(coro, timeout=min(timeout, 0.05))

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(executor.asyncio, "wait_for", fast_wait_for)

    res = await executor._run(["sleep", "60"], "input", timeout=1)
    assert res.timed_out is True
    assert res.exit_code == -1
    assert proc.killed is True
    assert proc.stdin.closed is True
    assert set(closed_transports) == {"stdin", "stdout", "stderr"}
