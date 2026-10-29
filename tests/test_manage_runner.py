"""Tests for manage_runner.sh slot-based declarative reconciliation and busy protection."""

import json
import os
import stat
import subprocess
from pathlib import Path
import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent
SCRIPT_PATH = ROOT_DIR / "scripts" / "manage_runner.sh"


def run_bash_cmd(cmd: str, env: dict) -> subprocess.CompletedProcess:
    """Run bash command with isolated environment."""
    return subprocess.run(
        ["bash", "-c", cmd],
        env=env,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def mock_fleet_env(tmp_path):
    """Set up an isolated execution environment with mock gh, mock ssh, and fleet config."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    gh_runners_file = data_dir / "gh_runners.json"
    gh_runners_file.write_text(json.dumps({"total_count": 0, "runners": []}), encoding="utf-8")

    deleted_log = data_dir / "deleted_runners.log"
    deleted_log.write_text("", encoding="utf-8")

    ssh_log = data_dir / "ssh_commands.log"
    ssh_log.write_text("", encoding="utf-8")

    # Mock gh CLI
    gh_script = bin_dir / "gh"
    gh_script.write_text(
        f"""#!/usr/bin/env python3
import sys, json, os, re
from pathlib import Path

runners_file = Path("{gh_runners_file}")
del_log = Path("{deleted_log}")

args = sys.argv[1:]
if not args:
    sys.exit(0)

if args[0] == "auth" and args[1] == "status":
    sys.exit(0)

if args[0] == "api":
    # 检查是否为 DELETE
    if "-X" in args and "DELETE" in args:
        endpoint = [a for a in args if "actions/runners" in a][0]
        # endpoint: repos/<repo>/actions/runners/<id>
        m = re.search(r'/actions/runners/(\\d+)', endpoint)
        if m:
            rid = int(m.group(1))
            with open(del_log, "a", encoding="utf-8") as f:
                f.write(f"DELETE {{rid}}\\n")
            if runners_file.is_file():
                data = json.loads(runners_file.read_text(encoding="utf-8"))
                data["runners"] = [r for r in data.get("runners", []) if r.get("id") != rid]
                data["total_count"] = len(data["runners"])
                runners_file.write_text(json.dumps(data), encoding="utf-8")
        sys.exit(0)

    # 检查是否为 registration-token
    if "registration-token" in " ".join(args):
        print(json.dumps({{"token": "mock-reg-token-abc123xyz"}}))
        sys.exit(0)

    # 默认 GET runners
    if runners_file.is_file():
        content = runners_file.read_text(encoding="utf-8")
        # 模拟 --jq 参数
        if "--jq" in args:
            jq_idx = args.index("--jq")
            jq_expr = args[jq_idx + 1]
            data = json.loads(content)
            # 常见简单 jq 解析
            if ".runners[] | select(.name ==" in jq_expr:
                m_name = re.search(r'select\\(\\.name == "([^"]+)"\\)', jq_expr)
                if m_name:
                    target_name = m_name.group(1)
                    matched = [r for r in data.get("runners", []) if r.get("name") == target_name]
                    if ".id" in jq_expr:
                        for r in matched:
                            print(r.get("id"))
                    elif ".status" in jq_expr:
                        for r in matched:
                            print(r.get("status"))
                    else:
                        for r in matched:
                            print(json.dumps(r))
            elif ".total_count" in jq_expr:
                print(data.get("total_count", 0))
            elif '.runners[] | select(.status == "offline")' in jq_expr:
                for r in data.get("runners", []):
                    if r.get("status") == "offline":
                        print(r.get("id"))
            else:
                print(content)
        else:
            print(content)
        sys.exit(0)

sys.exit(0)
""",
        encoding="utf-8",
    )
    gh_script.chmod(gh_script.stat().st_mode | stat.S_IEXEC)

    # Mock ssh CLI
    ssh_script = bin_dir / "ssh"
    ssh_script.write_text(
        f"""#!/usr/bin/env python3
import sys, re, json
from pathlib import Path

runners_file = Path("{gh_runners_file}")
ssh_log = Path("{ssh_log}")

cmd_str = " ".join(sys.argv[1:])
with open(ssh_log, "a", encoding="utf-8") as f:
    f.write(cmd_str + "\\n")

# SSH 连通性测试
if "echo ok" in cmd_str:
    sys.exit(0)

# 模拟 runner config.sh 注册上线
m_name = re.search(r'--name\\s+"([^"]+)"', cmd_str)
if m_name:
    runner_name = m_name.group(1)
    if runners_file.is_file():
        data = json.loads(runners_file.read_text(encoding="utf-8"))
        next_id = max([r.get("id", 100) for r in data.get("runners", [])] + [100]) + 1
        data["runners"].append({{
            "id": next_id,
            "name": runner_name,
            "status": "online",
            "busy": False,
            "os": "Linux",
            "labels": [{{"id": 1, "name": "self-hosted"}}]
        }})
        data["total_count"] = len(data["runners"])
        runners_file.write_text(json.dumps(data), encoding="utf-8")

sys.exit(0)
""",
        encoding="utf-8",
    )
    ssh_script.chmod(ssh_script.stat().st_mode | stat.S_IEXEC)

    # 构造自定义 runner_fleet.yaml
    fleet_yaml = tmp_path / "custom_runner_fleet.yaml"
    fleet_yaml.write_text(
        """
hosts:
  runner-node-01:
    user: testuser
    proxy: http://127.0.0.1:8118
    labels: [self-hosted, Linux, X64, runner-node-01]
    max_runners: 2
pools:
  org/test-repo:
    - runner-node-01
""",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["RUNNER_FLEET_CONFIG"] = str(fleet_yaml)
    env["RUNNER_START_WAIT_INTERVAL"] = "0"
    env["RUNNER_START_MAX_RETRIES"] = "1"
    # 清空 SQLite 优先路径以使用测试 YAML
    env["AGENT_FLEET_DB_PATH"] = str(tmp_path / "nonexistent.db")

    return {
        "env": env,
        "fleet_yaml": fleet_yaml,
        "gh_runners_file": gh_runners_file,
        "deleted_log": deleted_log,
        "ssh_log": ssh_log,
    }


def test_slot_reconcile_logic_0_1_n_max_runners():
    """测试 0/1/N max_runners 配置下的槽位解析与命令构建。"""
    host = "runner-node-01"
    safe_repo = "org-test-repo"

    # 1. 测试 max_runners = 0
    cmd_0 = [
        "bash",
        "-c",
        f"source {SCRIPT_PATH} 2>/dev/null || true; py_reconcile '{host}' '{safe_repo}' 0 '{{\"runners\": [{{\"name\": \"{host}-{safe_repo}-0\", \"status\": \"online\", \"busy\": false, \"id\": 101}}]}}'",
    ]
    res_0 = subprocess.run(cmd_0, capture_output=True, text=True)
    assert res_0.returncode == 0
    plan_0 = json.loads(res_0.stdout.strip())
    assert plan_0["max_runners"] == 0
    assert len(plan_0["missing"]) == 0
    assert len(plan_0["redundant"]) == 1
    assert plan_0["redundant"][0]["name"] == f"{host}-{safe_repo}-0"

    # 2. 测试 max_runners = 1 且存在 legacy (无槽位后缀) Runner
    cmd_1_legacy = [
        "bash",
        "-c",
        f"source {SCRIPT_PATH} 2>/dev/null || true; py_reconcile '{host}' '{safe_repo}' 1 '{{\"runners\": [{{\"name\": \"{host}-{safe_repo}\", \"status\": \"online\", \"busy\": false, \"id\": 101}}]}}'",
    ]
    res_1_leg = subprocess.run(cmd_1_legacy, capture_output=True, text=True)
    assert res_1_leg.returncode == 0
    plan_1_leg = json.loads(res_1_leg.stdout.strip())
    assert plan_1_leg["max_runners"] == 1
    assert len(plan_1_leg["healthy"]) == 1
    assert plan_1_leg["healthy"][0]["is_legacy"] is True
    assert len(plan_1_leg["missing"]) == 0
    assert len(plan_1_leg["redundant"]) == 0

    # 3. 测试 max_runners = 3 (N)，当前仅有 slot 0，期望缺失 slot 1 和 slot 2
    cmd_3 = [
        "bash",
        "-c",
        f"source {SCRIPT_PATH} 2>/dev/null || true; py_reconcile '{host}' '{safe_repo}' 3 '{{\"runners\": [{{\"name\": \"{host}-{safe_repo}-0\", \"status\": \"online\", \"busy\": false, \"id\": 101}}]}}'",
    ]
    res_3 = subprocess.run(cmd_3, capture_output=True, text=True)
    assert res_3.returncode == 0
    plan_3 = json.loads(res_3.stdout.strip())
    assert plan_3["max_runners"] == 3
    assert len(plan_3["healthy"]) == 1
    assert len(plan_3["missing"]) == 2
    missing_slots = [m["slot"] for m in plan_3["missing"]]
    assert missing_slots == [1, 2]
    assert len(plan_3["redundant"]) == 0


def test_scale_up_from_1_to_3_runners(mock_fleet_env):
    """模拟扩容：从 1 个扩到 3 个，只启动缺失的 2 个槽位，已有槽位不被破坏。"""
    env = mock_fleet_env["env"]
    fleet_yaml = mock_fleet_env["fleet_yaml"]
    gh_runners_file = mock_fleet_env["gh_runners_file"]
    deleted_log = mock_fleet_env["deleted_log"]
    ssh_log = mock_fleet_env["ssh_log"]

    # 初始化配置：max_runners = 3
    fleet_yaml.write_text(
        """
hosts:
  runner-node-01:
    user: testuser
    proxy: http://127.0.0.1:8118
    labels: [self-hosted, Linux, X64, runner-node-01]
    max_runners: 3
pools:
  org/test-repo:
    - runner-node-01
""",
        encoding="utf-8",
    )

    # 初始 GitHub 状态：已存在 slot 0 (ID: 100)
    initial_runners = {
        "total_count": 1,
        "runners": [
            {
                "id": 100,
                "name": "runner-node-01-org-test-repo-0",
                "status": "online",
                "busy": False,
                "os": "Linux",
                "labels": [{"id": 1, "name": "self-hosted"}],
            }
        ],
    }
    gh_runners_file.write_text(json.dumps(initial_runners), encoding="utf-8")

    # 执行声明式对齐
    cmd = f"{SCRIPT_PATH} sync"
    res = run_bash_cmd(cmd, env)
    assert res.returncode == 0, f"sync failed: {res.stderr}"

    # 检查输出日志
    out = res.stdout
    assert "✓ 主机 [runner-node-01] (槽位 0): Runner [runner-node-01-org-test-repo-0] 处于正常在线状态 (无需改动)" in out
    assert "槽位 1" in out
    assert "槽位 2" in out

    # 验证已有槽位 (ID: 100) 绝未被删除
    del_content = deleted_log.read_text(encoding="utf-8")
    assert "DELETE 100" not in del_content, "已有 slot 0 不得被删除"

    # 验证 SSH 执行记录：只对 slot 1 和 slot 2 执行了 config.sh
    ssh_content = ssh_log.read_text(encoding="utf-8")
    assert "--name \"runner-node-01-org-test-repo-1\"" in ssh_content
    assert "--name \"runner-node-01-org-test-repo-2\"" in ssh_content
    assert "--name \"runner-node-01-org-test-repo-0\"" not in ssh_content

    # 验证最终 GitHub 上有 3 个 runner
    final_data = json.loads(gh_runners_file.read_text(encoding="utf-8"))
    final_names = [r["name"] for r in final_data["runners"]]
    assert "runner-node-01-org-test-repo-0" in final_names
    assert "runner-node-01-org-test-repo-1" in final_names
    assert "runner-node-01-org-test-repo-2" in final_names
    assert len(final_names) == 3


def test_scale_down_with_busy_protection(mock_fleet_env):
    """模拟缩容与 busy 保护：当缩容时，busy runner 不被 kill，idle runner 正常停止。"""
    env = mock_fleet_env["env"]
    fleet_yaml = mock_fleet_env["fleet_yaml"]
    gh_runners_file = mock_fleet_env["gh_runners_file"]
    deleted_log = mock_fleet_env["deleted_log"]
    ssh_log = mock_fleet_env["ssh_log"]

    # 缩容目标配置：max_runners = 1
    fleet_yaml.write_text(
        """
hosts:
  runner-node-01:
    user: testuser
    proxy: http://127.0.0.1:8118
    labels: [self-hosted, Linux, X64, runner-node-01]
    max_runners: 1
pools:
  org/test-repo:
    - runner-node-01
""",
        encoding="utf-8",
    )

    # 当前运行环境有 3 个 runner：
    # slot 0: ID 100 (idle, 期望保留)
    # slot 1: ID 101 (idle, 冗余需下线)
    # slot 2: ID 102 (busy=true, 冗余但正在跑任务，必须 draining 保护跳过！)
    current_runners = {
        "total_count": 3,
        "runners": [
            {
                "id": 100,
                "name": "runner-node-01-org-test-repo-0",
                "status": "online",
                "busy": False,
                "os": "Linux",
                "labels": [{"id": 1, "name": "self-hosted"}],
            },
            {
                "id": 101,
                "name": "runner-node-01-org-test-repo-1",
                "status": "online",
                "busy": False,
                "os": "Linux",
                "labels": [{"id": 1, "name": "self-hosted"}],
            },
            {
                "id": 102,
                "name": "runner-node-01-org-test-repo-2",
                "status": "online",
                "busy": True,
                "os": "Linux",
                "labels": [{"id": 1, "name": "self-hosted"}],
            },
        ],
    }
    gh_runners_file.write_text(json.dumps(current_runners), encoding="utf-8")

    # 执行声明式对齐
    cmd = f"{SCRIPT_PATH} sync"
    res = run_bash_cmd(cmd, env)
    assert res.returncode == 0

    out = res.stdout
    # 验证 slot 0 保持不变
    assert "✓ 主机 [runner-node-01] (槽位 0): Runner [runner-node-01-org-test-repo-0] 处于正常在线状态 (无需改动)" in out

    # 验证 slot 1 空闲冗余被注销
    assert "缩容注销空闲冗余 Runner [runner-node-01-org-test-repo-1] (ID: 101)" in out

    # 验证 slot 2 busy 被标记为 draining 并安全跳过
    assert "当前正处于繁忙执行中 (busy=true)，标记为 draining，安全跳过，严禁中断任务" in out

    # 验证 GitHub 记录：slot 1 被删除，slot 2 (ID: 102) 绝未被删除
    del_content = deleted_log.read_text(encoding="utf-8")
    assert "DELETE 101" in del_content
    assert "DELETE 102" not in del_content, "繁忙中的 slot 2 严禁被删除！"

    # 验证 SSH 执行记录：绝无针对 slot 2 的 tmux kill-session
    ssh_content = ssh_log.read_text(encoding="utf-8")
    assert "tmux kill-session -t \"runner-org-test-repo-1\"" in ssh_content
    assert "runner-org-test-repo-2" not in ssh_content, "繁忙中的 slot 2 严禁被 kill！"


def test_sync_idempotency_no_spurious_actions(mock_fleet_env):
    """调谐（sync）的幂等性：同一目标状态多次 sync，不会产生重复注册或无谓重启。"""
    env = mock_fleet_env["env"]
    fleet_yaml = mock_fleet_env["fleet_yaml"]
    gh_runners_file = mock_fleet_env["gh_runners_file"]
    deleted_log = mock_fleet_env["deleted_log"]
    ssh_log = mock_fleet_env["ssh_log"]

    # 目标配置：max_runners = 2
    fleet_yaml.write_text(
        """
hosts:
  runner-node-01:
    user: testuser
    proxy: http://127.0.0.1:8118
    labels: [self-hosted, Linux, X64, runner-node-01]
    max_runners: 2
pools:
  org/test-repo:
    - runner-node-01
""",
        encoding="utf-8",
    )

    # 两个槽位均已正常在线
    current_runners = {
        "total_count": 2,
        "runners": [
            {
                "id": 201,
                "name": "runner-node-01-org-test-repo-0",
                "status": "online",
                "busy": False,
                "os": "Linux",
                "labels": [{"id": 1, "name": "self-hosted"}],
            },
            {
                "id": 202,
                "name": "runner-node-01-org-test-repo-1",
                "status": "online",
                "busy": True,
                "os": "Linux",
                "labels": [{"id": 1, "name": "self-hosted"}],
            },
        ],
    }
    gh_runners_file.write_text(json.dumps(current_runners), encoding="utf-8")

    # 首次 sync
    res1 = run_bash_cmd(f"{SCRIPT_PATH} sync", env)
    assert res1.returncode == 0
    assert "✓ 主机 [runner-node-01] (槽位 0): Runner [runner-node-01-org-test-repo-0] 处于正常在线状态 (无需改动)" in res1.stdout
    assert "✓ 主机 [runner-node-01] (槽位 1): Runner [runner-node-01-org-test-repo-1] 处于正常在线状态 (无需改动)" in res1.stdout

    # 检查在此过程中没有任何删除操作或部署操作
    assert deleted_log.read_text(encoding="utf-8").strip() == ""
    ssh_before = ssh_log.read_text(encoding="utf-8")
    assert "config.sh" not in ssh_before

    # 第二次 sync (完全相同状态)
    res2 = run_bash_cmd(f"{SCRIPT_PATH} sync", env)
    assert res2.returncode == 0
    assert "✓ 主机 [runner-node-01] (槽位 0): Runner [runner-node-01-org-test-repo-0] 处于正常在线状态 (无需改动)" in res2.stdout
    assert "✓ 主机 [runner-node-01] (槽位 1): Runner [runner-node-01-org-test-repo-1] 处于正常在线状态 (无需改动)" in res2.stdout

    assert deleted_log.read_text(encoding="utf-8").strip() == ""
    ssh_after = ssh_log.read_text(encoding="utf-8")
    assert "config.sh" not in ssh_after


def test_cmd_start_and_stop_slot_semantics(mock_fleet_env):
    """测试单槽位 start 与 stop 操作语义及 busy 保护。"""
    env = mock_fleet_env["env"]
    gh_runners_file = mock_fleet_env["gh_runners_file"]
    deleted_log = mock_fleet_env["deleted_log"]
    ssh_log = mock_fleet_env["ssh_log"]

    # 1. 尝试单独停止一个处于 busy 状态的 runner
    gh_runners_file.write_text(
        json.dumps({
            "total_count": 1,
            "runners": [
                {
                    "id": 301,
                    "name": "runner-node-01-org-test-repo-1",
                    "status": "online",
                    "busy": True,
                    "os": "Linux",
                    "labels": [{"id": 1, "name": "self-hosted"}],
                }
            ],
        }),
        encoding="utf-8",
    )

    res_stop_busy = run_bash_cmd(f"{SCRIPT_PATH} stop org/test-repo runner-node-01 1", env)
    assert res_stop_busy.returncode == 0
    assert "处于 busy 状态（正在执行工作流任务），跳过销毁以防破坏任务；已标记为 draining" in res_stop_busy.stdout
    assert "DELETE 301" not in deleted_log.read_text(encoding="utf-8")

    # 2. 将状态改为空闲 (busy=false)，再次 stop 应正常清理
    gh_runners_file.write_text(
        json.dumps({
            "total_count": 1,
            "runners": [
                {
                    "id": 301,
                    "name": "runner-node-01-org-test-repo-1",
                    "status": "online",
                    "busy": False,
                    "os": "Linux",
                    "labels": [{"id": 1, "name": "self-hosted"}],
                }
            ],
        }),
        encoding="utf-8",
    )
    res_stop_idle = run_bash_cmd(f"{SCRIPT_PATH} stop org/test-repo runner-node-01 1", env)
    assert res_stop_idle.returncode == 0
    assert "GitHub 端反注册成功 (ID: 301)" in res_stop_idle.stdout
    assert "DELETE 301" in deleted_log.read_text(encoding="utf-8")


def test_stop_non_zero_slot_ignores_legacy_even_if_first(mock_fleet_env):
    """Issue #52: 停止非零槽位时必须精确匹配该槽位，即便 legacy 实例排在 API 返回列表首位。"""
    env = mock_fleet_env["env"]
    gh_runners_file = mock_fleet_env["gh_runners_file"]
    deleted_log = mock_fleet_env["deleted_log"]
    ssh_log = mock_fleet_env["ssh_log"]

    # legacy (ID: 100) 排在首位，slot 1 (ID: 101) 排在后面
    gh_runners_file.write_text(
        json.dumps({
            "total_count": 2,
            "runners": [
                {
                    "id": 100,
                    "name": "runner-node-01-org-test-repo",
                    "status": "online",
                    "busy": False,
                    "os": "Linux",
                    "labels": [{"id": 1, "name": "self-hosted"}],
                },
                {
                    "id": 101,
                    "name": "runner-node-01-org-test-repo-1",
                    "status": "online",
                    "busy": False,
                    "os": "Linux",
                    "labels": [{"id": 1, "name": "self-hosted"}],
                },
            ],
        }),
        encoding="utf-8",
    )

    # 停止槽位 1
    res = run_bash_cmd(f"{SCRIPT_PATH} stop org/test-repo runner-node-01 1", env)
    assert res.returncode == 0

    # 验证 GitHub 注销仅针对 ID 101，绝不误杀 legacy (ID: 100)
    del_content = deleted_log.read_text(encoding="utf-8")
    assert "DELETE 101" in del_content
    assert "DELETE 100" not in del_content, "停止 slot 1 严禁误删 legacy runner"

    # 验证清理的远端会话与路径仅为 slot 1
    ssh_content = ssh_log.read_text(encoding="utf-8")
    assert 'tmux kill-session -t "runner-org-test-repo-1"' in ssh_content
    assert 'pkill -f "/home/testuser/runners/org-test-repo-1/"' in ssh_content
    assert 'rm -rf "/home/testuser/runners/org-test-repo-1"' in ssh_content


def test_stop_non_zero_slot_when_absent_never_falls_back_to_legacy(mock_fleet_env):
    """Issue #52: 停止未在线的非零槽位时，严禁回退并注销 legacy 实例。"""
    env = mock_fleet_env["env"]
    gh_runners_file = mock_fleet_env["gh_runners_file"]
    deleted_log = mock_fleet_env["deleted_log"]
    ssh_log = mock_fleet_env["ssh_log"]

    # 仅存在 legacy runner (ID: 100)，无 slot 2
    gh_runners_file.write_text(
        json.dumps({
            "total_count": 1,
            "runners": [
                {
                    "id": 100,
                    "name": "runner-node-01-org-test-repo",
                    "status": "online",
                    "busy": False,
                    "os": "Linux",
                    "labels": [{"id": 1, "name": "self-hosted"}],
                }
            ],
        }),
        encoding="utf-8",
    )

    # 停止槽位 2
    res = run_bash_cmd(f"{SCRIPT_PATH} stop org/test-repo runner-node-01 2", env)
    assert res.returncode == 0

    # 绝不执行针对 ID 100 的 DELETE
    del_content = deleted_log.read_text(encoding="utf-8")
    assert "DELETE 100" not in del_content

    # 远端清理操作只作用于 slot 2
    ssh_content = ssh_log.read_text(encoding="utf-8")
    assert 'tmux kill-session -t "runner-org-test-repo-2"' in ssh_content
    assert 'pkill -f "/home/testuser/runners/org-test-repo-2/"' in ssh_content


def test_stop_slot_0_prefers_exact_over_legacy(mock_fleet_env):
    """Issue #52: 当 slot 0 与 legacy 同时存在时，停止槽位 0 优先精确注销 slot-0 实例。"""
    env = mock_fleet_env["env"]
    gh_runners_file = mock_fleet_env["gh_runners_file"]
    deleted_log = mock_fleet_env["deleted_log"]
    ssh_log = mock_fleet_env["ssh_log"]

    gh_runners_file.write_text(
        json.dumps({
            "total_count": 2,
            "runners": [
                {
                    "id": 100,
                    "name": "runner-node-01-org-test-repo",
                    "status": "online",
                    "busy": False,
                    "os": "Linux",
                    "labels": [{"id": 1, "name": "self-hosted"}],
                },
                {
                    "id": 200,
                    "name": "runner-node-01-org-test-repo-0",
                    "status": "online",
                    "busy": False,
                    "os": "Linux",
                    "labels": [{"id": 1, "name": "self-hosted"}],
                },
            ],
        }),
        encoding="utf-8",
    )

    res = run_bash_cmd(f"{SCRIPT_PATH} stop org/test-repo runner-node-01 0", env)
    assert res.returncode == 0

    # 精确匹配 slot-0 (ID: 200)，不误删 legacy (ID: 100)
    del_content = deleted_log.read_text(encoding="utf-8")
    assert "DELETE 200" in del_content
    assert "DELETE 100" not in del_content

    ssh_content = ssh_log.read_text(encoding="utf-8")
    assert 'tmux kill-session -t "runner-org-test-repo-0"' in ssh_content
    assert 'pkill -f "/home/testuser/runners/org-test-repo-0/"' in ssh_content


def test_stop_slot_0_falls_back_to_legacy_when_slot_0_absent(mock_fleet_env):
    """Issue #52: 仅当 slot-0 不存在时，停止槽位 0 允许回退选中 legacy 实例。"""
    env = mock_fleet_env["env"]
    gh_runners_file = mock_fleet_env["gh_runners_file"]
    deleted_log = mock_fleet_env["deleted_log"]
    ssh_log = mock_fleet_env["ssh_log"]

    gh_runners_file.write_text(
        json.dumps({
            "total_count": 1,
            "runners": [
                {
                    "id": 100,
                    "name": "runner-node-01-org-test-repo",
                    "status": "online",
                    "busy": False,
                    "os": "Linux",
                    "labels": [{"id": 1, "name": "self-hosted"}],
                }
            ],
        }),
        encoding="utf-8",
    )

    res = run_bash_cmd(f"{SCRIPT_PATH} stop org/test-repo runner-node-01 0", env)
    assert res.returncode == 0

    # 成功回退注销 legacy
    del_content = deleted_log.read_text(encoding="utf-8")
    assert "DELETE 100" in del_content

    ssh_content = ssh_log.read_text(encoding="utf-8")
    assert 'tmux kill-session -t "runner-org-test-repo"' in ssh_content
    assert 'pkill -f "/home/testuser/runners/org-test-repo/"' in ssh_content


def test_directory_path_termination_boundary_isolation(mock_fleet_env):
    """Issue #53: 进程终止按完整目录路径边界隔离，避免 slot-1 杀死 slot-10/11，或 legacy 杀死带后缀槽位。"""
    env = mock_fleet_env["env"]
    ssh_log = mock_fleet_env["ssh_log"]

    # 1. 停止 slot 1
    res1 = run_bash_cmd(f"{SCRIPT_PATH} stop org/test-repo runner-node-01 1", env)
    assert res1.returncode == 0
    ssh_content1 = ssh_log.read_text(encoding="utf-8")

    # 提取 pkill 模式
    import re
    m1 = re.search(r'pkill -f "([^"]+)"', ssh_content1)
    assert m1 is not None, "必须包含 pkill 命令"
    pattern1 = m1.group(1)
    assert pattern1 == "/home/testuser/runners/org-test-repo-1/"

    # 验证 regex 隔离边界
    regex1 = re.compile(pattern1)
    # 应匹配 slot 1 的进程
    assert regex1.search("/home/testuser/runners/org-test-repo-1/run.sh")
    assert regex1.search("/home/testuser/runners/org-test-repo-1/bin/Runner.Listener run")
    # 绝对不得误伤 slot 10, slot 11
    assert not regex1.search("/home/testuser/runners/org-test-repo-10/run.sh")
    assert not regex1.search("/home/testuser/runners/org-test-repo-10/bin/Runner.Listener run")
    assert not regex1.search("/home/testuser/runners/org-test-repo-11/run.sh")
    assert not regex1.search("/home/testuser/runners/org-test-repo-11/bin/Runner.Listener run")

    # 2. 停止 legacy (slot 0 回退)
    gh_runners_file = mock_fleet_env["gh_runners_file"]
    gh_runners_file.write_text(
        json.dumps({
            "total_count": 1,
            "runners": [
                {
                    "id": 100,
                    "name": "runner-node-01-org-test-repo",
                    "status": "online",
                    "busy": False,
                    "os": "Linux",
                    "labels": [{"id": 1, "name": "self-hosted"}],
                }
            ],
        }),
        encoding="utf-8",
    )
    ssh_log.write_text("", encoding="utf-8")
    res0 = run_bash_cmd(f"{SCRIPT_PATH} stop org/test-repo runner-node-01 0", env)
    assert res0.returncode == 0
    ssh_content0 = ssh_log.read_text(encoding="utf-8")

    m0 = re.search(r'pkill -f "([^"]+)"', ssh_content0)
    assert m0 is not None, "必须包含 pkill 命令"
    pattern0 = m0.group(1)
    assert pattern0 == "/home/testuser/runners/org-test-repo/"

    regex0 = re.compile(pattern0)
    # 应匹配 legacy 的进程
    assert regex0.search("/home/testuser/runners/org-test-repo/run.sh")
    assert regex0.search("/home/testuser/runners/org-test-repo/bin/Runner.Listener run")
    # 绝对不得误伤带后缀的槽位: slot 0, slot 1, slot 10
    assert not regex0.search("/home/testuser/runners/org-test-repo-0/run.sh")
    assert not regex0.search("/home/testuser/runners/org-test-repo-0/bin/Runner.Listener run")
    assert not regex0.search("/home/testuser/runners/org-test-repo-1/run.sh")
    assert not regex0.search("/home/testuser/runners/org-test-repo-10/run.sh")
