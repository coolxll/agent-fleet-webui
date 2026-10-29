import subprocess
import pytest

from agent_fleet import models
from agent_fleet.db import session_scope
from agent_fleet.services import sqlite_inventory
from agent_fleet.services.scanner import RuntimeRef


def test_seed_from_legacy_files(fleet_db):
    # 验证自动无损入库
    result = sqlite_inventory.seed_from_legacy_files_if_needed(force=True)
    assert isinstance(result, dict)

    hosts = sqlite_inventory.list_hosts()
    assert len(hosts) > 0

    host_names = [h["name"] for h in hosts]
    assert "runner-node-01" in host_names

    corp = sqlite_inventory.get_host("runner-node-01")
    assert corp is not None
    assert corp["ssh_user"] == "devuser"
    assert "8118" in corp.get("proxy", "")
    assert corp["runner_enabled"] is True


def test_sqlite_inventory_crud(fleet_db):
    test_host_name = "test-node-sqlite-crud"

    # Clean up before
    sqlite_inventory.delete_host(test_host_name)

    # 1. Create
    created = sqlite_inventory.create_host(
        {
            "name": test_host_name,
            "hostname": "192.168.1.100",
            "description": "Test SQLite Host",
            "ssh_user": "testadmin",
            "ssh_port": 2222,
            "proxy": "socks5://10.0.0.1:1080",
            "runner_enabled": True,
            "runner_max_concurrency": 4,
            "runner_labels": ["self-hosted", "test-label"],
            "target_repos": ["coolxll/demo"],
        }
    )
    assert created["name"] == test_host_name
    assert created["ssh_user"] == "testadmin"
    assert created["ssh_port"] == 2222
    assert created["proxy"] == "socks5://10.0.0.1:1080"
    assert created["runner_max_concurrency"] == 4
    assert created["runtimes_count"] == 1

    # 2. Get
    fetched = sqlite_inventory.get_host(test_host_name)
    assert fetched is not None
    assert fetched["id"] == created["id"]
    assert fetched["target_repos"] == ["coolxll/demo"]

    # 3. Update
    updated = sqlite_inventory.update_host(
        test_host_name,
        {
            "proxy": "http://127.0.0.1:8118",
            "runner_max_concurrency": 8,
            "target_repos": ["coolxll/demo", "coolxll/another"],
        },
    )
    assert updated["proxy"] == "http://127.0.0.1:8118"
    assert updated["runner_max_concurrency"] == 8
    assert len(updated["target_repos"]) == 2

    # 4. Export YAML
    yaml_text = sqlite_inventory.export_as_yaml()
    assert test_host_name in yaml_text
    assert "testadmin" in yaml_text

    # 5. Delete
    deleted = sqlite_inventory.delete_host(test_host_name)
    assert deleted is True
    assert sqlite_inventory.get_host(test_host_name) is None


def test_inventory_hosts_api(client):
    test_api_host = "test-node-api-host"
    sqlite_inventory.delete_host(test_api_host)

    # 1. List
    res = client.get("/api/inventory/hosts")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert "hosts" in data

    # 2. Create via API
    res = client.post(
        "/api/inventory/hosts",
        json={
            "name": test_api_host,
            "hostname": "test-api.local",
            "description": "API Test",
            "ssh_user": "apiuser",
            "proxy": "http://127.0.0.1:8118",
            "runner_enabled": True,
            "target_repos": ["coolxll/agent-fleet-webui"],
        },
    )
    assert res.status_code == 200
    created = res.json()["host"]
    assert created["name"] == test_api_host
    assert created["ssh_user"] == "apiuser"

    # 3. Get single
    res = client.get(f"/api/inventory/hosts/{test_api_host}")
    assert res.status_code == 200
    assert res.json()["host"]["name"] == test_api_host

    # 4. Update via API
    res = client.put(
        f"/api/inventory/hosts/{test_api_host}",
        json={"proxy": "socks5://100.64.0.1:1080", "runner_max_concurrency": 6},
    )
    assert res.status_code == 200
    updated = res.json()["host"]
    assert updated["proxy"] == "socks5://100.64.0.1:1080"
    assert updated["runner_max_concurrency"] == 6

    # 5. Export via API
    res = client.get("/api/inventory/export-yaml")
    assert res.status_code == 200
    assert test_api_host in res.json()["content"]

    # 6. Delete via API
    res = client.delete(f"/api/inventory/hosts/{test_api_host}")
    assert res.status_code == 200
    assert res.json()["deleted"] is True

    # 7. Check 404
    res = client.get(f"/api/inventory/hosts/{test_api_host}")
    assert res.status_code == 404


def test_sqlite_ssot_not_overwritten_by_inventory_sync(fleet_db, monkeypatch):
    """验证 SQLite 中修改的主机属性在普通同步或再次启动时不被旧 YAML 覆盖回滚 (SSOT)。"""
    from agent_fleet.inventory.loader import DiscoveredHost
    from agent_fleet.services.inventory_sync import sync_inventory
    import agent_fleet.services.inventory_sync as inv_sync_mod

    settings = fleet_db
    host_name = "ssot-node-test"

    # 1. 在 SQLite 中创建自定义主机
    sqlite_inventory.create_host(
        {
            "name": host_name,
            "hostname": "user-custom-hostname",
            "group": "user-custom-group",
            "tags": {"role": "worker", "user_tag": "preserve_me"},
            "ssh_user": "custom-user",
        }
    )

    # 2. 模拟外部旧 YAML 包含同名主机，但具有旧属性
    mock_yaml_host = DiscoveredHost(
        name=host_name,
        hostname="old-yaml-hostname",
        group="old-yaml-group",
        tags={"role": "outdated-role", "yaml_tag": "new_key"},
        user="old-user",
        source="project-yaml",
    )
    monkeypatch.setattr(inv_sync_mod, "load_all", lambda s: [mock_yaml_host])

    # 3. 运行非 force 的普通同步 (sync_inventory 默认 force=False)
    sync_res = sync_inventory(settings, force=False)
    assert sync_res["updated"] == 1

    # 4. 验证 SQLite 中的核心属性未被旧 YAML 冲刷
    h = sqlite_inventory.get_host(host_name)
    assert h["hostname"] == "user-custom-hostname", "用户在 SQLite 中设置的 hostname 不应被 YAML 覆盖"
    assert h["group"] == "user-custom-group", "用户在 SQLite 中设置的 group 不应被 YAML 覆盖"
    assert h["tags"]["role"] == "worker", "用户在 SQLite 中设置的 tag 冲突项不应被 YAML 覆盖"
    assert h["tags"]["user_tag"] == "preserve_me"
    assert h["tags"]["yaml_tag"] == "new_key", "新出现的 non-conflicting 标签允许补入"

    # 5. 再次调用 seed_from_legacy_files_if_needed(force=False) 模拟重启
    migrated = sqlite_inventory.seed_from_legacy_files_if_needed(force=False)
    assert migrated == {"created": 0, "updated": 0}, "已有主机时不应发生二次迁移覆盖"
    h_after = sqlite_inventory.get_host(host_name)
    assert h_after["hostname"] == "user-custom-hostname"

    # 6. 如果显式 force=True，则允许覆盖
    sync_res_force = sync_inventory(settings, force=True)
    assert sync_res_force["updated"] == 1
    h_forced = sqlite_inventory.get_host(host_name)
    assert h_forced["hostname"] == "old-yaml-hostname"
    assert h_forced["group"] == "old-yaml-group"
    assert h_forced["tags"]["role"] == "outdated-role"


def test_runner_query_does_not_resurrect_deleted_or_disabled_hosts(tmp_path):
    """验证 SQLite 中删除或禁用的主机/仓库关联，不会被 manage_runner.sh 脚本重新复活。"""
    import os
    import sqlite3
    import subprocess
    import json
    from pathlib import Path

    root_dir = Path(__file__).resolve().parent.parent
    script_path = root_dir / "scripts" / "manage_runner.sh"

    # 1. 创建独立的临时 SQLite DB
    test_db = tmp_path / "test_runner.db"
    conn = sqlite3.connect(str(test_db))
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE hosts (
            id TEXT PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            hostname TEXT,
            description TEXT,
            inventory_source TEXT,
            group_name TEXT,
            tags TEXT,
            ssh_host TEXT,
            ssh_port INTEGER,
            ssh_user TEXT,
            proxy TEXT,
            runner_enabled BOOLEAN DEFAULT 0,
            runner_max_concurrency INTEGER DEFAULT 2,
            runner_labels TEXT,
            target_repos TEXT,
            enabled BOOLEAN DEFAULT 1,
            created_at TIMESTAMP,
            updated_at TIMESTAMP
        )
    """)
    # 插入一个活跃主机 (enabled=1, runner_enabled=1, target_repos=['org/active-repo'])
    cur.execute(
        """
        INSERT INTO hosts (id, name, ssh_user, proxy, runner_labels, target_repos, runner_enabled, runner_max_concurrency, enabled)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "h1",
            "node-active",
            "activeuser",
            "http://127.0.0.1:8118",
            json.dumps(["self-hosted", "node-active"]),
            json.dumps(["org/active-repo"]),
            1,
            4,
            1,
        ),
    )
    # 插入一个被禁用的主机 (enabled=0)
    cur.execute(
        """
        INSERT INTO hosts (id, name, ssh_user, proxy, runner_labels, target_repos, runner_enabled, runner_max_concurrency, enabled)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "h2",
            "node-disabled",
            "disableduser",
            "",
            json.dumps(["self-hosted", "node-disabled"]),
            json.dumps(["org/revive-repo"]),
            1,
            2,
            0,
        ),
    )
    conn.commit()
    conn.close()

    # 2. 创建临时 YAML，包含已被删除的 node-deleted，已被禁用的 node-disabled，以及已被移除的仓库
    custom_yaml = tmp_path / "legacy_runner_fleet.yaml"
    custom_yaml.write_text(
        """
hosts:
  node-active:
    user: yaml-user
    proxy: http://yaml-proxy:8080
    labels: [self-hosted, yaml]
  node-disabled:
    user: yaml-disabled-user
    proxy: http://yaml-disabled:8080
    labels: [self-hosted, disabled]
  node-deleted:
    user: yaml-deleted-user
    proxy: http://yaml-deleted:8080
    labels: [self-hosted, deleted]
pools:
  org/active-repo:
    - node-active
    - node-deleted
  org/revive-repo:
    - node-disabled
  org/deleted-repo:
    - node-deleted
""",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["AGENT_FLEET_DB_PATH"] = str(test_db)
    env["RUNNER_FLEET_CONFIG"] = str(custom_yaml)

    # 3. 运行 manage_runner.sh 查询 hosts 和 pools
    # 验证 node-deleted 和 node-disabled 不在 hosts 中
    cmd = [
        "bash",
        "-c",
        f"source {script_path} 2>/dev/null || true; py_query \"import json; print(json.dumps(cfg))\"",
    ]
    res = subprocess.run(cmd, env=env, capture_output=True, text=True)
    assert res.returncode == 0, f"py_query failed: {res.stderr}"
    data = json.loads(res.stdout.strip())

    # a) 验证 hosts: 只有 node-active，没有 node-disabled，更没有 node-deleted
    assert "node-active" in data["hosts"]
    assert "node-disabled" not in data["hosts"], "禁用主机不应被 YAML 复活"
    assert "node-deleted" not in data["hosts"], "已删除主机不应被 YAML 复活"
    assert data["hosts"]["node-active"]["user"] == "activeuser", "活跃主机配置应来自 SQLite SSOT"

    # b) 验证 pools: 只有 org/active-repo: ['node-active']
    assert "org/active-repo" in data["pools"]
    assert data["pools"]["org/active-repo"] == ["node-active"], "已删除的主机不应出现在 pools 中"
    assert "org/revive-repo" not in data["pools"], "禁用主机的 repo 关联不应被复活"
    assert "org/deleted-repo" not in data["pools"], "已删除主机的 repo 关联不应被复活"

    # c) 验证回退场景：若 SQLite 数据库中主机数为 0，则应回退读取 YAML
    empty_db = tmp_path / "empty.db"
    conn_empty = sqlite3.connect(str(empty_db))
    conn_empty.execute("CREATE TABLE hosts (id TEXT PRIMARY KEY, enabled BOOLEAN)")
    conn_empty.commit()
    conn_empty.close()

    env_empty = os.environ.copy()
    env_empty["AGENT_FLEET_DB_PATH"] = str(empty_db)
    env_empty["RUNNER_FLEET_CONFIG"] = str(custom_yaml)

    res_fallback = subprocess.run(cmd, env=env_empty, capture_output=True, text=True)
    assert res_fallback.returncode == 0
    fallback_data = json.loads(res_fallback.stdout.strip())
    assert "node-deleted" in fallback_data["hosts"], "SQLite 为空时应正常回退至 YAML"
    assert "org/deleted-repo" in fallback_data["pools"]


def test_update_host_ssh_fields_sync_to_runtime(fleet_db):
    """Test updating host ssh_host, ssh_port, ssh_user synchronizes to primary runtime."""
    host_name = "test-ssh-sync-node"
    sqlite_inventory.delete_host(host_name)

    # 1. Create host with initial SSH connection fields
    created = sqlite_inventory.create_host(
        {
            "name": host_name,
            "hostname": "old.local",
            "ssh_host": "old.local",
            "ssh_port": 22,
            "ssh_user": "initialuser",
        }
    )
    assert created["runtimes_count"] == 1
    rt = created["runtimes"][0]
    assert rt["ssh_host"] == "old.local"
    assert rt["ssh_port"] == 22
    assert rt["collect_user"] == "initialuser"

    # 2. Update SSH fields on Host
    updated = sqlite_inventory.update_host(
        host_name,
        {
            "ssh_host": "192.168.10.55",
            "ssh_port": 22022,
            "ssh_user": "syncedadmin",
        },
    )
    assert updated["ssh_host"] == "192.168.10.55"
    assert updated["ssh_port"] == 22022
    assert updated["ssh_user"] == "syncedadmin"

    # Verify native runtime in returned dict is synchronized
    rt_updated = updated["runtimes"][0]
    assert rt_updated["ssh_host"] == "192.168.10.55"
    assert rt_updated["ssh_port"] == 22022
    assert rt_updated["collect_user"] == "syncedadmin"

    # 3. Verify persistence from fresh DB read
    refetched = sqlite_inventory.get_host(host_name)
    assert refetched is not None
    db_rt = refetched["runtimes"][0]
    assert db_rt["ssh_host"] == "192.168.10.55"
    assert db_rt["ssh_port"] == 22022
    assert db_rt["collect_user"] == "syncedadmin"

    sqlite_inventory.delete_host(host_name)


def test_host_connectivity_with_custom_ssh_params(fleet_db, monkeypatch):
    """Test test_host_connectivity correctly builds ssh argv with custom host, port, user."""
    host_name = "test-custom-connect-host"
    sqlite_inventory.delete_host(host_name)

    created = sqlite_inventory.create_host(
        {
            "name": host_name,
            "hostname": "bastion-alias",
            "ssh_host": "10.200.1.5",
            "ssh_port": 8022,
            "ssh_user": "fleetdeployer",
        }
    )

    captured_commands = []

    def mock_subprocess_run(cmd, *args, **kwargs):
        captured_commands.append(cmd)
        class CompletedProcess:
            returncode = 0
            stdout = "Linux 6.6.0-test x86_64\n"
            stderr = ""
        return CompletedProcess()

    monkeypatch.setattr(subprocess, "run", mock_subprocess_run)

    # Test connectivity with custom SSH parameters
    res = sqlite_inventory.test_host_connectivity(host_name)
    assert res["ok"] is True
    assert res["host"] == "fleetdeployer@bastion-alias"
    assert res["uname"] == "Linux 6.6.0-test x86_64"

    assert len(captured_commands) == 1
    cmd = captured_commands[0]
    assert cmd[0] == "ssh"
    assert "-p" in cmd
    p_idx = cmd.index("-p")
    assert cmd[p_idx + 1] == "8022"
    assert "-o" in cmd
    hn_idx = cmd.index("HostName=10.200.1.5")
    assert cmd[hn_idx - 1] == "-o"
    assert "fleetdeployer@bastion-alias" in cmd
    assert "uname -srm" in cmd

    # Also test alias compatibility (no custom port, no custom user)
    alias_host_name = "test-alias-connect-host"
    sqlite_inventory.delete_host(alias_host_name)
    sqlite_inventory.create_host(
        {
            "name": alias_host_name,
            "hostname": alias_host_name,
            "ssh_host": alias_host_name,
            "ssh_port": 22,
            "ssh_user": "",
        }
    )
    res_alias = sqlite_inventory.test_host_connectivity(alias_host_name)
    assert res_alias["ok"] is True
    cmd_alias = captured_commands[-1]
    assert "-p" not in cmd_alias
    assert alias_host_name in cmd_alias

    sqlite_inventory.delete_host(host_name)
    sqlite_inventory.delete_host(alias_host_name)


def test_runtime_ref_target_and_runner_ssh_resolution():
    """Test RuntimeRef target resolution and runner parameter construction."""
    ref_custom = RuntimeRef(
        id="rt-custom",
        host_name="node-1",
        ssh_alias="node-1",
        ssh_host="172.20.0.10",
        ssh_port=3022,
        collect_user="deploy",
    )
    assert ref_custom.target == "deploy@node-1"

    ref_alias = RuntimeRef(
        id="rt-alias",
        host_name="openssh-alias",
        ssh_alias="openssh-alias",
        ssh_host="",
        ssh_port=22,
        collect_user="",
    )
    assert ref_alias.target == "openssh-alias"


def test_seed_windows_host_and_wsl_runtimes(fleet_db, monkeypatch):
    """Issue #51: Verify Windows transport is not hardcoded to ssh and declared WSL runtimes are imported."""
    from agent_fleet.inventory.loader import DiscoveredHost, WslTarget
    import agent_fleet.services.sqlite_inventory as sq_mod

    win_host = DiscoveredHost(
        name="win-builder",
        hostname="192.168.1.120",
        user="Administrator",
        port=22,
        kind_hint="windows",
        wsl=[
            WslTarget(distro="Ubuntu-22.04", user="wsluser"),
            WslTarget(distro="Debian"),
        ],
        source="project-yaml",
    )
    monkeypatch.setattr("agent_fleet.inventory.load_all", lambda s: [win_host])

    res = sq_mod.seed_from_legacy_files_if_needed(force=True)
    assert res["created"] >= 1

    h = sq_mod.get_host("win-builder")
    assert h is not None
    assert h["name"] == "win-builder"

    # Verify native runtime has windows transport (not ssh!)
    runtimes = {r["key"]: r for r in h["runtimes"]}
    native_rt = runtimes.get("native")
    assert native_rt is not None
    assert native_rt["kind"] == "windows"
    assert native_rt["transport"] == "windows"
    assert native_rt["ssh_alias"] == "win-builder"
    assert native_rt["ssh_host"] == "192.168.1.120"

    # Verify child WSL runtimes
    wsl_ubuntu = runtimes.get("wsl:Ubuntu-22.04")
    assert wsl_ubuntu is not None
    assert wsl_ubuntu["kind"] == "wsl"
    assert wsl_ubuntu["transport"] == "wsl"
    assert wsl_ubuntu["wsl_distro"] == "Ubuntu-22.04"
    assert wsl_ubuntu["wsl_user"] == "wsluser"

    wsl_debian = runtimes.get("wsl:Debian")
    assert wsl_debian is not None
    assert wsl_debian["kind"] == "wsl"
    assert wsl_debian["transport"] == "wsl"
    assert wsl_debian["wsl_distro"] == "Debian"
    assert wsl_debian["wsl_user"] == "Administrator"

    sq_mod.delete_host("win-builder")


def test_seed_subsequent_runs_do_not_resurrect_deleted_or_duplicate(fleet_db, monkeypatch):
    """Issue #51: Verify subsequent runs do not resurrect deleted entries or create duplicate runtimes."""
    from agent_fleet.inventory.loader import DiscoveredHost
    import agent_fleet.services.sqlite_inventory as sq_mod

    test_node = "legacy-del-node"
    mock_host = DiscoveredHost(
        name=test_node,
        hostname="192.168.1.99",
        user="dev",
        source="project-yaml",
    )
    monkeypatch.setattr("agent_fleet.inventory.load_all", lambda s: [mock_host])

    # First seed
    res1 = sq_mod.seed_from_legacy_files_if_needed(force=True)
    assert res1["created"] >= 1
    assert sq_mod.get_host(test_node) is not None

    # User deletes the host
    deleted = sq_mod.delete_host(test_node)
    assert deleted is True
    assert sq_mod.get_host(test_node) is None

    # Subsequent run without force=True must NOT resurrect the deleted host
    res2 = sq_mod.seed_from_legacy_files_if_needed(force=False)
    assert res2 == {"created": 0, "updated": 0}
    assert sq_mod.get_host(test_node) is None


def test_no_hardcoded_ssh_user_default(client):
    """Verify that omitting ssh_user does not default to 'lynn' in API or models."""
    test_node = "test-no-default-user"
    sqlite_inventory.delete_host(test_node)

    # 1. API creation without ssh_user
    res = client.post(
        "/api/inventory/hosts",
        json={
            "name": test_node,
            "hostname": "192.168.1.50",
        },
    )
    assert res.status_code == 200
    host_data = res.json()["host"]
    assert host_data["ssh_user"] in ("", None)
    assert host_data["ssh_user"] != "lynn"

    # 2. Get host check
    fetched = sqlite_inventory.get_host(test_node)
    assert fetched["ssh_user"] in ("", None)
    assert fetched["ssh_user"] != "lynn"

    # 3. Model check in database
    with session_scope() as s:
        db_host = s.get(models.Host, host_data["id"])
        assert db_host.ssh_user is None

    sqlite_inventory.delete_host(test_node)


