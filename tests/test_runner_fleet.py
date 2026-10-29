import os
import subprocess
from pathlib import Path
import yaml
import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT_DIR / "config" / "runner_fleet.yaml"
SCRIPT_PATH = ROOT_DIR / "scripts" / "manage_runner.sh"


def test_runner_fleet_yaml_structure():
    assert CONFIG_PATH.is_file(), f"{CONFIG_PATH} 不存在"
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    assert isinstance(data, dict), "配置必须是字典"
    assert "hosts" in data, "缺少 hosts 定义"
    assert "pools" in data, "缺少 pools 运行矩阵定义"

    hosts = data["hosts"]
    assert "corp172-dev" in hosts, "缺少 corp172-dev 主机"
    assert "x13" in hosts, "缺少 x13 主机"

    corp = hosts["corp172-dev"]
    assert corp["user"] == "lynn"
    assert "8118" in corp.get("proxy", "")
    assert isinstance(corp.get("labels"), list)

    x13 = hosts["x13"]
    assert x13["user"] == "lynns"
    assert "1080" in x13.get("proxy", "")

    pools = data["pools"]
    assert "coolxll/agent-fleet-webui" in pools
    assert isinstance(pools["coolxll/agent-fleet-webui"], list)


def test_manage_runner_script_syntax():
    assert SCRIPT_PATH.is_file(), f"{SCRIPT_PATH} 不存在"
    res = subprocess.run(["bash", "-n", str(SCRIPT_PATH)], capture_output=True, text=True)
    assert res.returncode == 0, f"manage_runner.sh 存在语法错误: {res.stderr}"


def test_host_profile_query_logic(tmp_path):
    custom_yaml = tmp_path / "custom_fleet.yaml"
    custom_yaml.write_text(
        """
hosts:
  node-a:
    user: testuser
    proxy: http://10.0.0.1:8080
    labels: [self-hosted, Linux, test]
  node-b:
    user: dev
    proxy: socks5://100.1.2.3:1080
    labels: [self-hosted, arm64]
pools:
  org/demo:
    - node-a
    - node-b
""",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["RUNNER_FLEET_CONFIG"] = str(custom_yaml)

    # 验证 bash 脚本读取自定义配置的 Profile
    cmd = [
        "bash",
        "-c",
        f"source {SCRIPT_PATH} 2>/dev/null || true; get_host_profile node-b",
    ]
    res = subprocess.run(cmd, env=env, capture_output=True, text=True)
    out = res.stdout.strip()
    assert "socks5://100.1.2.3:1080" in out
    assert "dev" in out
    assert "self-hosted,arm64" in out


def test_safe_name_conversion():
    cmd = [
        "bash",
        "-c",
        f"source {SCRIPT_PATH} 2>/dev/null || true; safe_name 'coolxll/agent-fleet-webui.git'",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    out = res.stdout.strip()
    assert out == "coolxll-agent-fleet-webui-git"
