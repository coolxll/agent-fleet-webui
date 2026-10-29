#!/usr/bin/env bash
#
# manage_runner.sh: GitHub Actions Self-Hosted Runner 自动化运维工具 (声明式 Fleet 控制面)
#
# 架构特性：
# 1. 纯出站安全（Outbound Only）：各主机 runner 仅主动长轮询 GitHub，绝不向公网开放任何入站端口。
# 2. 声明式配置驱动（Desired State）：基于 SQLite (runner_max_concurrency) 或 config/runner_fleet.yaml (max_runners) 自动对齐多机、多仓库 runner 状态。
# 3. 槽位编排与优雅伸缩：按 slot 编号增量扩容与安全缩容，严格实施 busy/draining 保护，绝不破坏执行中的任务。
# 4. 自动注入差异化代理与主机 Profile，彻底消除配置遗忘与僵尸 runner 堆积。
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG_FILE="${RUNNER_FLEET_CONFIG:-$ROOT_DIR/config/runner_fleet.yaml}"
RUNNER_VERSION="2.322.0" # 统一稳定版 runner

# Python 解释器查找（优先虚拟环境，保证 yaml 模块可用）
PYTHON_BIN="python3"
if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
fi

# 解析 SQLite 数据库 (优先) 或 config/runner_fleet.yaml (回退) 辅助函数
py_query() {
  local code="$1"
  "$PYTHON_BIN" - <<PYEOF
import sys, sqlite3, json
from pathlib import Path

db_path_env = "${AGENT_FLEET_DB_PATH:-}"
runner_config_env = "${RUNNER_FLEET_CONFIG:-}"

if db_path_env:
    db_path = Path(db_path_env)
elif not runner_config_env:
    db_path = Path("$ROOT_DIR/data/fleet.db")
else:
    db_path = None

cfg = {"hosts": {}, "pools": {}}
sqlite_has_hosts = False

if db_path and db_path.is_file():
    try:
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM hosts")
        total_hosts = cur.fetchone()[0]
        if total_hosts > 0:
            sqlite_has_hosts = True

        cur.execute("SELECT name, ssh_user, proxy, runner_labels, target_repos, runner_enabled, runner_max_concurrency, description FROM hosts WHERE enabled=1")
        for row in cur.fetchall():
            h_name, user, proxy, labels_raw, repos_raw, r_enabled, max_r, desc = row
            try:
                labels = json.loads(labels_raw) if labels_raw else []
            except Exception:
                labels = []
            try:
                repos = json.loads(repos_raw) if repos_raw else []
            except Exception:
                repos = []

            max_val = 1
            if max_r is not None:
                try:
                    max_val = int(max_r)
                except (ValueError, TypeError):
                    max_val = 1

            cfg["hosts"][h_name] = {
                "user": user or "lynn",
                "proxy": proxy or "",
                "labels": labels or ["self-hosted", "Linux", "X64", h_name],
                "max_runners": max_val,
                "description": desc or ""
            }
            if r_enabled:
                for repo in repos:
                    if h_name not in cfg["pools"].setdefault(repo, []):
                        cfg["pools"][repo].append(h_name)
        conn.close()
    except Exception:
        pass

# Fallback: only if SQLite does not exist or has 0 hosts, fallback to reading CONFIG_FILE
if not sqlite_has_hosts and Path("$CONFIG_FILE").is_file():
    try:
        import yaml
        with open("$CONFIG_FILE", "r", encoding="utf-8") as f:
            y = yaml.safe_load(f) or {}
            cfg["hosts"] = y.get("hosts", {}) or {}
            cfg["pools"] = y.get("pools", {}) or {}
            for h_name, h_info in cfg["hosts"].items():
                if isinstance(h_info, dict):
                    max_r = h_info.get("max_runners", 1)
                    try:
                        h_info["max_runners"] = int(max_r)
                    except (ValueError, TypeError):
                        h_info["max_runners"] = 1
    except Exception:
        pass

$code
PYEOF
}

# 获取当前 git 仓库的 owner/repo
detect_current_repo() {
  if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    local url
    url=$(git remote get-url origin 2>/dev/null || true)
    if [[ "$url" =~ github\.com[:/]([^/]+)/([^/\.]+)(\.git)?$ ]]; then
      echo "${BASH_REMATCH[1]}/${BASH_REMATCH[2]}"
      return 0
    fi
  fi
  echo ""
}

safe_name() {
  local input="$1"
  echo "$input" | tr '/.:' '-' | tr '[:upper:]' '[:lower:]'
}

check_gh() {
  if ! command -v gh >/dev/null 2>&1; then
    echo "❌ 错误: 未找到 gh CLI 命令，请先安装 GitHub CLI" >&2
    exit 1
  fi
  if ! gh auth status >/dev/null 2>&1; then
    echo "❌ 错误: gh CLI 尚未登录，请先执行 'gh auth login'" >&2
    exit 1
  fi
}

# 获取主机的 Profile 配置 (proxy, user, labels, max_runners)
get_host_profile() {
  local host="$1"
  py_query "
h = cfg.get('hosts', {}).get('$host', {})
proxy = h.get('proxy', '')
user = h.get('user', 'lynn')
labels = ','.join(h.get('labels', ['self-hosted', 'Linux', 'X64', '$host']))
max_runners = int(h.get('max_runners', 1))
print(f'{proxy}|{user}|{labels}|{max_runners}')
"
}

get_host_max_runners() {
  local host="$1"
  py_query "
h = cfg.get('hosts', {}).get('$host', {})
print(int(h.get('max_runners', 1)))
"
}

# 槽位与实例命名规范辅助函数
slot_runner_name() {
  local host="$1"
  local safe_repo="$2"
  local slot="${3:-0}"
  local is_legacy="${4:-0}"
  if [ "$is_legacy" = "1" ] || [ "$is_legacy" = "true" ]; then
    echo "${host}-${safe_repo}"
  else
    echo "${host}-${safe_repo}-${slot}"
  fi
}

slot_session_name() {
  local safe_repo="$1"
  local slot="${2:-0}"
  local is_legacy="${3:-0}"
  if [ "$is_legacy" = "1" ] || [ "$is_legacy" = "true" ]; then
    echo "runner-${safe_repo}"
  else
    echo "runner-${safe_repo}-${slot}"
  fi
}

slot_target_dir() {
  local host_user="$1"
  local safe_repo="$2"
  local slot="${3:-0}"
  local is_legacy="${4:-0}"
  if [ "$is_legacy" = "1" ] || [ "$is_legacy" = "true" ]; then
    echo "/home/${host_user}/runners/${safe_repo}"
  else
    echo "/home/${host_user}/runners/${safe_repo}-${slot}"
  fi
}

# 声明式 Reconcile 决策引擎 (Python 内核解析槽位状态)
py_reconcile() {
  local host="$1"
  local safe_repo="$2"
  local max_runners="$3"
  local runners_json="$4"

  RUNNERS_RAW="$runners_json" "$PYTHON_BIN" - <<PYEOF
import os, json, re, sys

host = "$host"
safe_repo = "$safe_repo"
try:
    max_runners = int("$max_runners")
except Exception:
    max_runners = 1

prefix = f"{host}-{safe_repo}"
raw_json = os.environ.get("RUNNERS_RAW", "")

runners = []
if raw_json.strip():
    try:
        data = json.loads(raw_json)
        runners = data.get("runners", [])
    except Exception:
        runners = []

slot_pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)$")
legacy_pattern = re.compile(rf"^{re.escape(prefix)}$")

matched = []
for r in runners:
    r_name = r.get("name", "")
    ms = slot_pattern.match(r_name)
    ml = legacy_pattern.match(r_name)
    if ms:
        matched.append({
            "id": r.get("id"),
            "name": r_name,
            "slot": int(ms.group(1)),
            "status": r.get("status", "unknown"),
            "busy": bool(r.get("busy", False)),
            "is_legacy": False
        })
    elif ml:
        matched.append({
            "id": r.get("id"),
            "name": r_name,
            "slot": 0,
            "status": r.get("status", "unknown"),
            "busy": bool(r.get("busy", False)),
            "is_legacy": True
        })

# 排序：槽位号递增，优先在线且非遗留实例
matched.sort(key=lambda x: (x["slot"], 0 if x["status"] == "online" else 1, 1 if x["is_legacy"] else 0))

existing_by_slot = {}
duplicates = []
for r in matched:
    s = r["slot"]
    if s not in existing_by_slot:
        existing_by_slot[s] = r
    else:
        duplicates.append(r)

desired_slots = list(range(max_runners)) if max_runners > 0 else []

healthy = []
missing = []

for s in desired_slots:
    if s in existing_by_slot:
        r = existing_by_slot[s]
        if r["status"] == "online":
            healthy.append(r)
        else:
            missing.append({
                "slot": s,
                "existing_id": r["id"],
                "existing_name": r["name"],
                "is_legacy": r["is_legacy"],
                "reason": r["status"]
            })
    else:
        missing.append({
            "slot": s,
            "existing_id": None,
            "existing_name": None,
            "is_legacy": False,
            "reason": "missing"
        })

redundant = []
for s, r in existing_by_slot.items():
    if s >= max_runners:
        redundant.append(r)
redundant.extend(duplicates)

out = {
    "host": host,
    "safe_repo": safe_repo,
    "max_runners": max_runners,
    "healthy": healthy,
    "missing": missing,
    "redundant": redundant
}
print(json.dumps(out))
PYEOF
}

usage() {
  cat <<'EOF'
GitHub Actions Runner Fleet 自动化运维工具 (声明式控制面)

用法:
  ./scripts/manage_runner.sh <command> [options]

集群级 / 声明式命令:
  overview                         查看全网全局大盘（包含各主机槽位与 busy 状态矩阵）
  sync                             一键声明式对齐（根据 max_runners 增量扩缩容与自愈）
  prune                            一键垃圾回收（安全清理离线 offline 且非 busy 的僵尸 Runner）

单机 / 交互命令:
  status [owner/repo]              查看指定仓库已绑定的 Runner 状态
  start  [owner/repo] [host] [slot] 为指定仓库在目标主机部署并启动指定或全部槽位 Runner
  stop   [owner/repo] [host] [slot] 优雅停止指定槽位或全部 Runner (具有 busy/draining 保护)
  logs   [owner/repo] [host] [slot] 查看指定主机及槽位的实时运行日志
  token  [owner/repo]              快速获取指定仓库的动态 registration token

示例:
  ./scripts/manage_runner.sh overview
  ./scripts/manage_runner.sh sync
  ./scripts/manage_runner.sh prune
  ./scripts/manage_runner.sh start coolxll/agent-fleet-webui runner-node-01
  ./scripts/manage_runner.sh start coolxll/agent-fleet-webui runner-node-01 1
  ./scripts/manage_runner.sh stop  coolxll/agent-fleet-webui runner-node-01 0
EOF
  exit 1
}

cmd_token() {
  local repo="${1:-$(detect_current_repo)}"
  if [ -z "$repo" ]; then
    echo "❌ 请指定仓库名" >&2
    exit 1
  fi
  check_gh
  gh api -X POST "repos/$repo/actions/runners/registration-token" --jq .token
}

cmd_status() {
  local repo="${1:-$(detect_current_repo)}"
  if [ -z "$repo" ]; then
    echo "❌ 请指定仓库名 (例如: coolxll/agent-fleet-webui)" >&2
    exit 1
  fi

  check_gh
  echo "==> 检查仓库 [$repo] 的 Runner 列表..."
  local runners
  runners=$(gh api "repos/$repo/actions/runners" 2>/dev/null || true)

  if [ -z "$runners" ] || [ "$(echo "$runners" | jq '.total_count // 0')" -eq 0 ]; then
    echo "ℹ 仓库 [$repo] 暂无任何 Self-Hosted Runner"
    return 0
  fi

  echo "$runners" | jq -r '.runners[] | "• ID: \(.id) | 名称: \(.name) | 状态: \(.status) | 繁忙: \(.busy) | OS: \(.os) | 标签: [\(.labels | map(.name) | join(", "))]"'
}

# 启动单个槽位的 Runner 实例
cmd_start_slot() {
  local repo="$1"
  local host="$2"
  local slot="${3:-0}"
  local force="${4:-false}"

  if [ -z "$repo" ]; then
    echo "❌ 请指定仓库名" >&2
    return 1
  fi

  check_gh

  local profile
  profile=$(get_host_profile "$host")
  local host_proxy
  host_proxy=$(echo "$profile" | cut -d'|' -f1)
  local host_user
  host_user=$(echo "$profile" | cut -d'|' -f2)
  local host_labels
  host_labels=$(echo "$profile" | cut -d'|' -f3)

  echo "==> [1/4] 验证主机 [$host] SSH 连通性 (用户: $host_user)..."
  if ! ssh -o BatchMode=yes -o ConnectTimeout=5 "$host" "echo ok" >/dev/null 2>&1; then
    echo "❌ 错误: 无法通过 SSH 连接到主机 [$host]，请检查 ~/.ssh/config" >&2
    return 1
  fi

  local safe_repo
  safe_repo=$(safe_name "$repo")

  # 检查是否已有 legacy 名称 runner 存在（仅当 slot==0 且 legacy 存在时保持向下兼容）
  local is_legacy=0
  local gh_runners
  gh_runners=$(gh api "repos/$repo/actions/runners" 2>/dev/null || true)
  if [ "$slot" = "0" ] && [ -n "$gh_runners" ]; then
    local leg_id
    leg_id=$(echo "$gh_runners" | jq -r ".runners[] | select(.name == \"${host}-${safe_repo}\") | .id" 2>/dev/null || true)
    if [ -n "$leg_id" ] && [ "$leg_id" != "null" ]; then
      is_legacy=1
    fi
  fi

  local runner_name
  runner_name=$(slot_runner_name "$host" "$safe_repo" "$slot" "$is_legacy")
  local session_name
  session_name=$(slot_session_name "$safe_repo" "$slot" "$is_legacy")
  local target_dir
  target_dir=$(slot_target_dir "$host_user" "$safe_repo" "$slot" "$is_legacy")

  echo "==> [2/4] 检查 GitHub 上同名 Runner [$runner_name]..."
  if [ -n "$gh_runners" ]; then
    local existing_runner
    existing_runner=$(echo "$gh_runners" | jq -c ".runners[] | select(.name == \"$runner_name\")" 2>/dev/null || true)
    if [ -n "$existing_runner" ]; then
      local ex_status
      ex_status=$(echo "$existing_runner" | jq -r '.status // "unknown"')
      local ex_busy
      ex_busy=$(echo "$existing_runner" | jq -r '.busy // false')
      local existing_id
      existing_id=$(echo "$existing_runner" | jq -r '.id')

      if [ "$ex_status" = "online" ]; then
        if [ "$ex_busy" = "true" ]; then
          echo "⚠️ Runner [$runner_name] 正在执行任务 (busy=true)，跳过拉起以防破坏任务"
          return 0
        fi
        if [ "$force" != "true" ]; then
          echo "✓ Runner [$runner_name] 已经在线运行中 (无需重复拉起)"
          return 0
        fi
      fi

      echo "   - 发现旧记录 (ID: $existing_id, status: $ex_status)，清理释放旧槽位..."
      gh api -X DELETE "repos/$repo/actions/runners/$existing_id" >/dev/null 2>&1 || true
    fi
  fi

  echo "==> [3/4] 获取仓库 [$repo] 动态注册 Token..."
  local token
  token=$(gh api -X POST "repos/$repo/actions/runners/registration-token" --jq .token)
  if [ -z "$token" ]; then
    echo "❌ 获取 Token 失败" >&2
    return 1
  fi

  echo "==> [4/4] 在 [$host] 启动 Runner [$runner_name] (槽位: $slot, 代理: ${host_proxy:-直连}, 标签: [$host_labels])..."
  ssh "$host" "
    set -euo pipefail
    mkdir -p ~/.cache/actions-runner \"$target_dir\"

    TARBALL=\"\$HOME/.cache/actions-runner/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz\"
    if [ ! -f \"\$TARBALL\" ]; then
      echo '   - 首次下载 runner 安装包缓存...'
      if [ -n \"$host_proxy\" ]; then
        curl -sSL --proxy \"$host_proxy\" -o \"\$TARBALL\" \"https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz\"
      else
        curl -sSL -o \"\$TARBALL\" \"https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz\"
      fi
    fi

    # 清理并解压当前槽位目录与会话
    tmux kill-session -t \"$session_name\" 2>/dev/null || true
    pkill -f \"$target_dir\" 2>/dev/null || true
    rm -rf \"$target_dir\"/*
    tar -zxf \"\$TARBALL\" -C \"$target_dir\"

    # 自动写入代理配置（支持 HTTP 与 SOCKS5 出站代理共享）
    if [ -n \"$host_proxy\" ]; then
      cat << ENVEOF > \"$target_dir/.env\"
HTTP_PROXY=$host_proxy
HTTPS_PROXY=$host_proxy
http_proxy=$host_proxy
https_proxy=$host_proxy
ALL_PROXY=$host_proxy
all_proxy=$host_proxy
NO_PROXY=localhost,127.0.0.1,10.*,172.*,100.*
no_proxy=localhost,127.0.0.1,10.*,172.*,100.*
ENVEOF
    else
      touch \"$target_dir/.env\"
    fi

    if [ -f ~/.runner-env ]; then
      cat ~/.runner-env >> \"$target_dir/.env\"
    fi

    cd \"$target_dir\"
    ./config.sh --url \"https://github.com/$repo\" \
                --token \"$token\" \
                --name \"$runner_name\" \
                --labels \"$host_labels\" \
                --unattended \
                --replace

    tmux new-session -d -s \"$session_name\" \"cd '$target_dir' && ./run.sh\"
  "

  # 等待上线确认
  local wait_interval="${RUNNER_START_WAIT_INTERVAL:-2}"
  local max_retries="${RUNNER_START_MAX_RETRIES:-6}"
  if [ "$wait_interval" -gt 0 ] 2>/dev/null; then
    sleep "$wait_interval"
  fi
  local status=""
  for ((i=1; i<=max_retries; i++)); do
    status=$(gh api "repos/$repo/actions/runners" --jq ".runners[] | select(.name == \"$runner_name\") | .status" 2>/dev/null || true)
    if [ "$status" = "online" ]; then
      break
    fi
    if [ "$wait_interval" -gt 0 ] 2>/dev/null; then
      sleep "$wait_interval"
    fi
  done

  if [ "$status" = "online" ]; then
    echo "✓ 主机 [$host] (槽位 $slot) -> 仓库 [$repo] Runner [$runner_name] 已成功上线！"
  else
    echo "⚠️ Runner [$runner_name] 已拉起，GitHub 当前上报状态为: [$status]"
  fi
}

# 交互式 / 命令行入口：支持指定单个 slot 或按 max_runners 批量拉起
cmd_start() {
  local repo="${1:-$(detect_current_repo)}"
  local host="${2:-runner-node-01}"
  local slot="${3:-}"

  if [ -z "$repo" ]; then
    echo "❌ 请指定仓库名" >&2
    exit 1
  fi

  if [ -n "$slot" ]; then
    cmd_start_slot "$repo" "$host" "$slot"
  else
    local max_r
    max_r=$(get_host_max_runners "$host")
    if [ "$max_r" -le 0 ]; then
      echo "ℹ 主机 [$host] max_runners=$max_r，跳过启动"
      return 0
    fi
    echo "==> 批量启动主机 [$host] 的所有 Runner 实例 (目标并发数: $max_r)..."
    for ((s=0; s<max_r; s++)); do
      cmd_start_slot "$repo" "$host" "$s"
    done
  fi
}

# 停止单个槽位的 Runner 实例（含 busy / draining 保护）
cmd_stop_slot() {
  local repo="$1"
  local host="$2"
  local slot="${3:-0}"
  local expected_name="${4:-}"
  local expected_id="${5:-}"
  local force="${6:-false}"

  if [ -z "$repo" ]; then
    echo "❌ 请指定仓库名" >&2
    return 1
  fi

  check_gh
  local profile
  profile=$(get_host_profile "$host")
  local host_user
  host_user=$(echo "$profile" | cut -d'|' -f2)

  local safe_repo
  safe_repo=$(safe_name "$repo")

  local runner_name="$expected_name"
  local rid="$expected_id"

  # 若未传入具体 runner 名称，则查询 GitHub 获取其实时状态
  if [ -z "$runner_name" ] || [ -z "$rid" ]; then
    local gh_runners
    gh_runners=$(gh api "repos/$repo/actions/runners" 2>/dev/null || true)
    if [ -n "$gh_runners" ]; then
      local candidate
      candidate=$(echo "$gh_runners" | jq -c ".runners[] | select(.name == \"${host}-${safe_repo}-${slot}\" or .name == \"${host}-${safe_repo}\")" 2>/dev/null | head -n 1 || true)
      if [ -n "$candidate" ]; then
        runner_name=$(echo "$candidate" | jq -r '.name')
        rid=$(echo "$candidate" | jq -r '.id')
        local is_busy
        is_busy=$(echo "$candidate" | jq -r '.busy // false')
        if [ "$is_busy" = "true" ] && [ "$force" != "true" ]; then
          echo "⚠️ Runner [$runner_name] 处于 busy 状态（正在执行工作流任务），跳过销毁以防破坏任务；已标记为 draining"
          return 0
        fi
      fi
    fi
  fi

  if [ -z "$runner_name" ]; then
    runner_name="${host}-${safe_repo}-${slot}"
  fi

  # 判断是否为 legacy 槽位
  local is_legacy=0
  if [ "$runner_name" = "${host}-${safe_repo}" ]; then
    is_legacy=1
  fi
  local session_name
  session_name=$(slot_session_name "$safe_repo" "$slot" "$is_legacy")
  local target_dir
  target_dir=$(slot_target_dir "$host_user" "$safe_repo" "$slot" "$is_legacy")

  echo "==> 注销仓库 [$repo] 在主机 [$host] 上的 Runner [$runner_name] (槽位 $slot)..."
  if [ -n "$rid" ] && [ "$rid" != "null" ]; then
    gh api -X DELETE "repos/$repo/actions/runners/$rid" >/dev/null 2>&1 || true
    echo "   - GitHub 端反注册成功 (ID: $rid)"
  fi

  ssh "$host" "
    tmux kill-session -t \"$session_name\" 2>/dev/null || true
    pkill -f \"$target_dir\" 2>/dev/null || true
    rm -rf \"$target_dir\"
  " 2>/dev/null || true
  echo "✓ 远端主机 [$host] 槽位 [$slot] 会话与目录已清理"
}

# 交互式 / 命令行停止入口：支持指定 slot 或批量下线
cmd_stop() {
  local repo="${1:-$(detect_current_repo)}"
  local host="${2:-runner-node-01}"
  local slot="${3:-}"

  if [ -z "$repo" ]; then
    echo "❌ 请指定仓库名" >&2
    exit 1
  fi

  if [ -n "$slot" ] && [ "$slot" != "all" ]; then
    cmd_stop_slot "$repo" "$host" "$slot"
  else
    echo "==> 批量下线主机 [$host] 在仓库 [$repo] 上的所有 Runner 实例..."
    local gh_runners
    gh_runners=$(gh api "repos/$repo/actions/runners" 2>/dev/null || true)
    local safe_repo
    safe_repo=$(safe_name "$repo")
    local runners_to_stop
    runners_to_stop=$(RUNNERS_RAW="$gh_runners" "$PYTHON_BIN" - <<PYEOF
import os, json, re
host = "$host"
safe_repo = "$safe_repo"
prefix = f"{host}-{safe_repo}"
raw_json = os.environ.get("RUNNERS_RAW", "")
slot_pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)$")
legacy_pattern = re.compile(rf"^{re.escape(prefix)}$")
try:
    runners = json.loads(raw_json).get("runners", []) if raw_json else []
except Exception:
    runners = []
for r in runners:
    name = r.get("name", "")
    ms = slot_pattern.match(name)
    ml = legacy_pattern.match(name)
    if ms:
        print(f"{ms.group(1)}|{name}|{r.get('id')}|{str(r.get('busy', False)).lower()}")
    elif ml:
        print(f"0|{name}|{r.get('id')}|{str(r.get('busy', False)).lower()}")
PYEOF
)
    if [ -n "$runners_to_stop" ]; then
      while IFS='|' read -r s_idx r_name r_id r_busy; do
        [ -z "$s_idx" ] && continue
        if [ "$r_busy" = "true" ]; then
          echo "⚠️ Runner [$r_name] 处于 busy 状态，标记为 draining，跳过销毁"
        else
          cmd_stop_slot "$repo" "$host" "$s_idx" "$r_name" "$r_id"
        fi
      done <<< "$runners_to_stop"
    else
      cmd_stop_slot "$repo" "$host" 0
    fi
  fi
}

cmd_logs() {
  local repo="${1:-$(detect_current_repo)}"
  local host="${2:-runner-node-01}"
  local slot="${3:-}"

  local safe_repo
  safe_repo=$(safe_name "$repo")

  local session_name
  if [ -n "$slot" ]; then
    session_name="runner-${safe_repo}-${slot}"
  else
    session_name="runner-${safe_repo}-0"
  fi

  echo "==> 捕获 [$host] 上的 tmux 会话 [$session_name] 输出..."
  ssh "$host" "tmux capture-pane -pt \"$session_name\" -S -50 2>/dev/null || tmux capture-pane -pt \"runner-${safe_repo}\" -S -50 2>/dev/null || echo 'ℹ 会话 $session_name 未在运行'"
}

# --- 全网大盘总览 (Overview) ---
cmd_overview() {
  check_gh
  echo "================================================================================"
  echo "  GitHub Actions Runner Fleet 全网全局状态大盘"
  echo "  配置文件: config/runner_fleet.yaml"
  echo "================================================================================"

  local repos
  repos=$(py_query "for r in cfg.get('pools', {}).keys(): print(r)")
  if [ -z "$repos" ]; then
    echo "ℹ config/runner_fleet.yaml 的 pools 中尚未声明任何仓库"
    return 0
  fi

  printf "%-30s | %-16s | %-6s | %-12s | %-6s | %-8s\n" "仓库 (Repository)" "期望主机 (Host)" "槽位" "状态 (Status)" "繁忙" "Runner ID"
  echo "-------------------------------+------------------+--------+--------------+--------+---------"

  while IFS= read -r repo; do
    [ -z "$repo" ] && continue
    local expected_hosts
    expected_hosts=$(py_query "for h in cfg.get('pools', {}).get('$repo', []): print(h)")

    local gh_runners
    gh_runners=$(gh api "repos/$repo/actions/runners" 2>/dev/null || true)

    while IFS= read -r host; do
      [ -z "$host" ] && continue
      local safe_repo
      safe_repo=$(safe_name "$repo")
      local max_r
      max_r=$(get_host_max_runners "$host")

      local overview_lines
      overview_lines=$(RUNNERS_RAW="$gh_runners" "$PYTHON_BIN" - <<PYEOF
import json, os, re

raw = os.environ.get("RUNNERS_RAW", "")
host = "$host"
safe_repo = "$safe_repo"
try:
    max_r = int("$max_r")
except Exception:
    max_r = 1

try:
    data = json.loads(raw) if raw else {}
    runners = data.get("runners", [])
except Exception:
    runners = []

prefix = f"{host}-{safe_repo}"
slot_pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)$")
legacy_pattern = re.compile(rf"^{re.escape(prefix)}$")

slots_found = {}
for r in runners:
    r_name = r.get("name", "")
    ms = slot_pattern.match(r_name)
    ml = legacy_pattern.match(r_name)
    if ms:
        slots_found[int(ms.group(1))] = r
    elif ml:
        slots_found[0] = r

all_slots = sorted(set(list(range(max_r)) + list(slots_found.keys())))
if not all_slots:
    print(f"0|⚠️ 未部署|-|-(max_r=0)")
else:
    for s in all_slots:
        if s in slots_found:
            r = slots_found[s]
            status = r.get("status", "unknown")
            busy = str(r.get("busy", False)).lower()
            rid = f"#{r.get('id')}"
            if status == "online":
                d_status = "✅ 在线"
            elif status == "offline":
                d_status = "❌ 离线"
            else:
                d_status = f"⚠️ {status}"
        else:
            d_status = "⚠️ 未部署"
            busy = "-"
            rid = "-"
        print(f"{s}|{d_status}|{busy}|{rid}")
PYEOF
)
      while IFS='|' read -r s_idx s_status s_busy s_id; do
        [ -z "$s_idx" ] && continue
        printf "%-30s | %-16s | %-6s | %-12s | %-6s | %-8s\n" "$repo" "$host" "#$s_idx" "$s_status" "$s_busy" "$s_id"
      done <<< "$overview_lines"
    done <<< "$expected_hosts"
  done <<< "$repos"

  echo "================================================================================"
}

# --- 声明式对齐单个主机的 Runner 槽位 ---
reconcile_host_runners() {
  local repo="$1"
  local host="$2"
  local gh_runners="$3"

  local safe_repo
  safe_repo=$(safe_name "$repo")
  local max_r
  max_r=$(get_host_max_runners "$host")

  local plan_json
  plan_json=$(py_reconcile "$host" "$safe_repo" "$max_r" "$gh_runners")

  # 1. 打印正常在线状态 (幂等无损)
  local healthy_lines
  healthy_lines=$("$PYTHON_BIN" -c "
import json
data = json.loads('''$plan_json''')
for h in data.get('healthy', []):
    print(f\"{h['slot']}|{h['name']}\")
")
  if [ -n "$healthy_lines" ]; then
    while IFS='|' read -r s_idx r_name; do
      [ -z "$s_idx" ] && continue
      echo "✓ 主机 [$host] (槽位 $s_idx): Runner [$r_name] 处于正常在线状态 (无需改动)"
    done <<< "$healthy_lines"
  fi

  # 2. 扩容/自愈：补齐缺失或离线槽位
  local missing_lines
  missing_lines=$("$PYTHON_BIN" -c "
import json
data = json.loads('''$plan_json''')
for m in data.get('missing', []):
    print(f\"{m['slot']}|{m.get('existing_id') or ''}|{m.get('existing_name') or ''}|{m.get('reason')}\")
")
  if [ -n "$missing_lines" ]; then
    while IFS='|' read -r s_idx ex_id ex_name reason; do
      [ -z "$s_idx" ] && continue
      echo "⚡ 主机 [$host] (槽位 $s_idx): Runner 处于未上线或异常状态 [$reason]，执行拉起与自愈..."
      cmd_start_slot "$repo" "$host" "$s_idx"
    done <<< "$missing_lines"
  fi

  # 3. 缩容/回收：处理冗余槽位 (busy 保护)
  local redundant_lines
  redundant_lines=$("$PYTHON_BIN" -c "
import json
data = json.loads('''$plan_json''')
for r in data.get('redundant', []):
    print(f\"{r['slot']}|{r['id']}|{r['name']}|{str(r['busy']).lower()}\")
")
  if [ -n "$redundant_lines" ]; then
    while IFS='|' read -r s_idx r_id r_name r_busy; do
      [ -z "$s_idx" ] && continue
      if [ "$r_busy" = "true" ]; then
        echo "⚠️ 主机 [$host] (超额槽位 $s_idx): Runner [$r_name] 当前正处于繁忙执行中 (busy=true)，标记为 draining，安全跳过，严禁中断任务！"
      else
        echo "✂️ 主机 [$host] (超额槽位 $s_idx): 缩容注销空闲冗余 Runner [$r_name] (ID: $r_id)..."
        cmd_stop_slot "$repo" "$host" "$s_idx" "$r_name" "$r_id"
      fi
    done <<< "$redundant_lines"
  fi
}

# --- 一键声明式对齐自愈 (Sync) ---
cmd_sync() {
  check_gh
  echo "==> 开始声明式对齐 Runner Fleet (Desired State: config/runner_fleet.yaml)..."

  local repos
  repos=$(py_query "for r in cfg.get('pools', {}).keys(): print(r)")
  if [ -z "$repos" ]; then
    echo "ℹ 未在配置文件中找到任何仓库声明"
    return 0
  fi

  while IFS= read -r repo; do
    [ -z "$repo" ] && continue
    echo ""
    echo "------------------------------------------------------------"
    echo "📦 校验仓库: [$repo]"
    echo "------------------------------------------------------------"

    local expected_hosts
    expected_hosts=$(py_query "for h in cfg.get('pools', {}).get('$repo', []): print(h)")

    local gh_runners
    gh_runners=$(gh api "repos/$repo/actions/runners" 2>/dev/null || true)

    while IFS= read -r host; do
      [ -z "$host" ] && continue
      reconcile_host_runners "$repo" "$host" "$gh_runners"
    done <<< "$expected_hosts"
  done <<< "$repos"

  echo ""
  echo "✓ Fleet 声明式同步与自愈检查完成！"
}

# --- 一键清理所有关联仓库中失联且非 busy 的 offline 僵尸 Runner (Prune) ---
cmd_prune() {
  check_gh
  echo "==> 开始全网扫描并清理失联的 Offline 僵尸 Runner..."

  local repos
  repos=$(py_query "for r in cfg.get('pools', {}).keys(): print(r)")
  if [ -z "$repos" ]; then
    echo "ℹ 未在配置文件中找到任何仓库声明"
    return 0
  fi

  local pruned_count=0
  while IFS= read -r repo; do
    [ -z "$repo" ] && continue
    local gh_runners
    gh_runners=$(gh api "repos/$repo/actions/runners" 2>/dev/null || true)
    [ -z "$gh_runners" ] && continue

    local offline_runners
    offline_runners=$(RUNNERS_RAW="$gh_runners" "$PYTHON_BIN" - <<PYEOF
import os, json
raw = os.environ.get("RUNNERS_RAW", "")
try:
    data = json.loads(raw)
    runners = data.get("runners", [])
    for r in runners:
        if r.get("status") == "offline" and not r.get("busy", False):
            print(f"{r.get('id')}|{r.get('name')}")
except Exception:
    pass
PYEOF
)
    if [ -n "$offline_runners" ]; then
      while IFS='|' read -r r_id r_name; do
        [ -z "$r_id" ] && continue
        echo "   - [清理] 仓库 [$repo] 存在离线空闲 Runner [$r_name] (ID: $r_id)，执行注销..."
        gh api -X DELETE "repos/$repo/actions/runners/$r_id" >/dev/null 2>&1 || true
        pruned_count=$((pruned_count + 1))
      done <<< "$offline_runners"
    fi
  done <<< "$repos"

  if [ "$pruned_count" -eq 0 ]; then
    echo "✓ 很好！全网无任何 offline 僵尸 Runner 积压。"
  else
    echo "✓ 垃圾回收完毕：共成功清理并注销了 $pruned_count 个 offline 僵尸 Runner！"
  fi
}

main() {
  local action="${1:-}"
  shift || true

  case "$action" in
    overview|all|list)
      cmd_overview
      ;;
    sync|apply)
      cmd_sync
      ;;
    prune|gc)
      cmd_prune
      ;;
    status)
      cmd_status "$@"
      ;;
    token)
      cmd_token "$@"
      ;;
    start|deploy)
      cmd_start "$@"
      ;;
    stop|down|teardown)
      cmd_stop "$@"
      ;;
    logs)
      cmd_logs "$@"
      ;;
    *)
      usage
      ;;
  esac
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
