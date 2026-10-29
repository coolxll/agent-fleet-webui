#!/usr/bin/env bash
#
# manage_runner.sh: GitHub Actions Self-Hosted Runner 自动化运维工具 (声明式 Fleet 控制面)
#
# 架构特性：
# 1. 纯出站安全（Outbound Only）：各主机 runner 仅主动长轮询 GitHub，绝不向公网开放任何入站端口。
# 2. 声明式配置驱动（Desired State）：基于 config/runner_fleet.yaml 自动对齐多机、多仓库 runner 状态。
# 3. 自动注入差异化代理与主机 Profile，彻底消除配置遗忘与僵尸 runner 堆积。
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

db_path = Path("${AGENT_FLEET_DB_PATH:-$ROOT_DIR/data/fleet.db}")
cfg = {"hosts": {}, "pools": {}}

if db_path.is_file():
    try:
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
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

            cfg["hosts"][h_name] = {
                "user": user or "lynn",
                "proxy": proxy or "",
                "labels": labels or ["self-hosted", "Linux", "X64", h_name],
                "max_runners": max_r or 2,
                "description": desc or ""
            }
            if r_enabled:
                for repo in repos:
                    if h_name not in cfg["pools"].setdefault(repo, []):
                        cfg["pools"][repo].append(h_name)
        conn.close()
    except Exception:
        pass

# Fallback & Supplement: if CONFIG_FILE exists, merge any missing entries
if Path("$CONFIG_FILE").is_file():
    try:
        import yaml
        with open("$CONFIG_FILE", "r", encoding="utf-8") as f:
            y = yaml.safe_load(f) or {}
            for k, v in (y.get("hosts", {}) or {}).items():
                if k not in cfg["hosts"]:
                    cfg["hosts"][k] = v
            for k, v in (y.get("pools", {}) or {}).items():
                if k not in cfg["pools"]:
                    cfg["pools"][k] = v
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

# 获取主机的 Profile 配置 (proxy, user, labels)
get_host_profile() {
  local host="$1"
  py_query "
h = cfg.get('hosts', {}).get('$host', {})
proxy = h.get('proxy', '')
user = h.get('user', 'lynn')
labels = ','.join(h.get('labels', ['self-hosted', 'Linux', 'X64', '$host']))
print(f'{proxy}|{user}|{labels}')
"
}

usage() {
  cat <<'EOF'
GitHub Actions Runner Fleet 自动化运维工具 (声明式控制面)

用法:
  ./scripts/manage_runner.sh <command> [options]

集群级 / 声明式命令:
  overview                         查看全网全局大盘（所有在册仓库与各主机矩阵状态）
  sync                             一键声明式对齐（根据 config/runner_fleet.yaml 自动补齐与唤醒）
  prune                            一键垃圾回收（清理所有关联仓库中已失联的 offline 僵尸 Runner）

单机 / 交互命令:
  status [owner/repo]              查看指定仓库已绑定的 Runner 状态
  start  [owner/repo] [host]       为指定仓库在目标主机部署并启动 Runner（自动注入 Profile 代理与标签）
  stop   [owner/repo] [host]       停止并彻底注销指定仓库在该主机上的 Runner
  logs   [owner/repo] [host]       查看指定主机上该 Runner 的实时运行日志
  token  [owner/repo]              快速获取指定仓库的动态 registration token

示例:
  ./scripts/manage_runner.sh overview
  ./scripts/manage_runner.sh sync
  ./scripts/manage_runner.sh prune
  ./scripts/manage_runner.sh start coolxll/EmbyMetaInfo x13
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

cmd_start() {
  local repo="${1:-$(detect_current_repo)}"
  local host="${2:-corp172-dev}"

  if [ -z "$repo" ]; then
    echo "❌ 请指定仓库名" >&2
    exit 1
  fi

  check_gh

  # 读取 Host Profile
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
  local runner_name="${host}-${safe_repo}"
  local session_name="runner-${safe_repo}"
  local target_dir="/home/${host_user}/runners/${safe_repo}"

  echo "==> [2/4] 检查 GitHub 上同名 Runner [$runner_name]..."
  local existing_id
  existing_id=$(gh api "repos/$repo/actions/runners" --jq ".runners[] | select(.name == \"$runner_name\") | .id" 2>/dev/null || true)
  if [ -n "$existing_id" ]; then
    echo "   - 发现已存在的旧记录 (ID: $existing_id)，清理释放旧槽位..."
    gh api -X DELETE "repos/$repo/actions/runners/$existing_id" >/dev/null 2>&1 || true
  fi

  echo "==> [3/4] 获取仓库 [$repo] 动态注册 Token..."
  local token
  token=$(gh api -X POST "repos/$repo/actions/runners/registration-token" --jq .token)
  if [ -z "$token" ]; then
    echo "❌ 获取 Token 失败" >&2
    return 1
  fi

  echo "==> [4/4] 在 [$host] 启动 Runner (代理: ${host_proxy:-直连}, 标签: [$host_labels])..."
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

    # 清理并解压
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
  sleep 2
  local status=""
  for i in {1..6}; do
    status=$(gh api "repos/$repo/actions/runners" --jq ".runners[] | select(.name == \"$runner_name\") | .status" 2>/dev/null || true)
    if [ "$status" = "online" ]; then
      break
    fi
    sleep 2
  done

  if [ "$status" = "online" ]; then
    echo "✓ 主机 [$host] -> 仓库 [$repo] Runner 已成功上线！"
  else
    echo "⚠️ Runner 已拉起，GitHub 当前上报状态为: [$status]"
  fi
}

cmd_stop() {
  local repo="${1:-$(detect_current_repo)}"
  local host="${2:-corp172-dev}"

  if [ -z "$repo" ]; then
    echo "❌ 请指定仓库名" >&2
    exit 1
  fi

  check_gh
  local profile
  profile=$(get_host_profile "$host")
  local host_user
  host_user=$(echo "$profile" | cut -d'|' -f2)

  local safe_repo
  safe_repo=$(safe_name "$repo")
  local runner_name="${host}-${safe_repo}"
  local session_name="runner-${safe_repo}"
  local target_dir="/home/${host_user}/runners/${safe_repo}"

  echo "==> 注销仓库 [$repo] 在主机 [$host] 上的 Runner..."
  local rid
  rid=$(gh api "repos/$repo/actions/runners" --jq ".runners[] | select(.name == \"$runner_name\") | .id" 2>/dev/null || true)
  if [ -n "$rid" ]; then
    gh api -X DELETE "repos/$repo/actions/runners/$rid" >/dev/null 2>&1 || true
    echo "   - GitHub 端反注册成功 (ID: $rid)"
  fi

  ssh "$host" "
    tmux kill-session -t \"$session_name\" 2>/dev/null || true
    pkill -f \"$target_dir\" 2>/dev/null || true
    rm -rf \"$target_dir\"
  " 2>/dev/null || true
  echo "✓ 远端主机 [$host] 会话与目录已清理"
}

cmd_logs() {
  local repo="${1:-$(detect_current_repo)}"
  local host="${2:-corp172-dev}"

  local safe_repo
  safe_repo=$(safe_name "$repo")
  local session_name="runner-${safe_repo}"

  echo "==> 捕获 [$host] 上的 tmux 会话 [$session_name] 输出..."
  ssh "$host" "tmux capture-pane -pt \"$session_name\" -S -50 2>/dev/null || echo 'ℹ 会话 $session_name 未在运行'"
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

  printf "%-30s | %-16s | %-12s | %-6s | %-8s\n" "仓库 (Repository)" "期望主机 (Host)" "状态 (Status)" "繁忙" "Runner ID"
  echo "-------------------------------+------------------+--------------+--------+---------"

  while IFS= read -r repo; do
    [ -z "$repo" ] && continue
    local expected_hosts
    expected_hosts=$(py_query "for h in cfg.get('pools', {}).get('$repo', []): print(h)")

    # 读取该 repo 在 GitHub 上的实时 runners 数据
    local gh_runners
    gh_runners=$(gh api "repos/$repo/actions/runners" 2>/dev/null || true)

    while IFS= read -r host; do
      [ -z "$host" ] && continue
      local runner_name="${host}-$(safe_name "$repo")"

      local r_status="未就绪"
      local r_busy="-"
      local r_id="-"

      if [ -n "$gh_runners" ]; then
        local match
        match=$(echo "$gh_runners" | jq -c ".runners[] | select(.name == \"$runner_name\")" 2>/dev/null || true)
        if [ -n "$match" ]; then
          r_status=$(echo "$match" | jq -r '.status')
          r_busy=$(echo "$match" | jq -r '.busy')
          r_id="#$(echo "$match" | jq -r '.id')"
        fi
      fi

      local display_status="$r_status"
      if [ "$r_status" = "online" ]; then
        display_status="✅ 在线"
      elif [ "$r_status" = "offline" ]; then
        display_status="❌ 离线(待自愈)"
      elif [ "$r_status" = "未就绪" ]; then
        display_status="⚠️ 未部署"
      fi

      printf "%-30s | %-16s | %-12s | %-6s | %-8s\n" "$repo" "$host" "$display_status" "$r_busy" "$r_id"
    done <<< "$expected_hosts"
  done <<< "$repos"

  echo "================================================================================"
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
      local runner_name="${host}-$(safe_name "$repo")"
      local status="missing"

      if [ -n "$gh_runners" ]; then
        status=$(echo "$gh_runners" | jq -r ".runners[] | select(.name == \"$runner_name\") | .status" 2>/dev/null || true)
      fi

      if [ "$status" = "online" ]; then
        echo "✓ 主机 [$host]: Runner [$runner_name] 处于正常在线状态 (无需改动)"
      else
        echo "⚡ 主机 [$host]: Runner 处于未上线或异常状态 [$status]，执行自动自愈与启动..."
        cmd_start "$repo" "$host"
      fi
    done <<< "$expected_hosts"
  done <<< "$repos"

  echo ""
  echo "✓ Fleet 声明式同步与自愈检查完成！"
}

# --- 一键清理所有关联仓库中失联的 offline 僵尸 Runner (Prune) ---
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
    local offline_ids
    offline_ids=$(gh api "repos/$repo/actions/runners" --jq '.runners[] | select(.status == "offline") | .id' 2>/dev/null || true)

    if [ -n "$offline_ids" ]; then
      for id in $offline_ids; do
        echo "   - [清理] 仓库 [$repo] 存在离线 Runner (ID: $id)，执行注销..."
        gh api -X DELETE "repos/$repo/actions/runners/$id" >/dev/null 2>&1 || true
        pruned_count=$((pruned_count + 1))
      done
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
