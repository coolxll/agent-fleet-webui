#!/usr/bin/env bash
#
# manage_runner.sh: GitHub Actions Self-Hosted Runner 自动化运维工具
#
# 依托 gh CLI 与 SSH，实现多仓库、多机器之间 runner 的秒级自动注册、代理注入与彻底注销。
#
set -euo pipefail

# 默认主机与配置
DEFAULT_HOST="corp172-dev"
PROXY_URL="http://127.0.0.1:8118"
RUNNER_VERSION="2.322.0" # 当前成熟稳定版 actions-runner

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

# 格式化安全的目录/会话名称
safe_name() {
  local input="$1"
  echo "$input" | tr '/.:' '-' | tr '[:upper:]' '[:lower:]'
}

usage() {
  cat <<'EOF'
GitHub Actions Runner 自动化运维工具

用法:
  ./scripts/manage_runner.sh <command> [options]

命令:
  status [owner/repo]              查看仓库已绑定的 Runner 状态
  start  [owner/repo] [host]       自动为指定仓库在目标主机部署并启动 Runner（带代理与标签）
  stop   [owner/repo] [host]       停止并彻底注销指定仓库在该主机上的 Runner
  logs   [owner/repo] [host]       查看远端 Runner 的实时运行日志
  token  [owner/repo]              直接打印该仓库的动态 registration token

示例:
  # 查看当前项目的 Runner 状态
  ./scripts/manage_runner.sh status

  # 为当前项目在 corp172-dev 上启动 Runner
  ./scripts/manage_runner.sh start

  # 为另一个项目在 x13 上启动 Runner
  ./scripts/manage_runner.sh start coolxll/EmbyMetaInfo x13

  # 停止并彻底下线注销
  ./scripts/manage_runner.sh stop coolxll/EmbyMetaInfo x13
EOF
  exit 1
}

# 检查 gh 命令与登录
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

cmd_token() {
  local repo="${1:-$(detect_current_repo)}"
  if [ -z "$repo" ]; then
    echo "❌ 请指定仓库名" >&2
    exit 1
  fi
  check_gh
  gh api -X POST "repos/$repo/actions/runners/registration-token" --jq .token
}

cmd_start() {
  local repo="${1:-$(detect_current_repo)}"
  local host="${2:-$DEFAULT_HOST}"

  if [ -z "$repo" ]; then
    echo "❌ 请指定仓库名 (例如: coolxll/agent-fleet-webui)" >&2
    exit 1
  fi

  check_gh
  echo "==> 1. 验证目标主机 [$host] SSH 连通性..."
  if ! ssh -o BatchMode=yes -o ConnectTimeout=5 "$host" "echo ok" >/dev/null 2>&1; then
    echo "❌ 错误: 无法通过 SSH 连接到主机 [$host]，请检查 ~/.ssh/config" >&2
    exit 1
  fi

  local safe_repo
  safe_repo=$(safe_name "$repo")
  local runner_name="${host}-${safe_repo}"
  local session_name="runner-${safe_repo}"
  local target_dir="/home/lynn/runners/${safe_repo}"

  echo "==> 2. 检查 GitHub 上是否已存在同名 Runner [$runner_name]..."
  local existing_id
  existing_id=$(gh api "repos/$repo/actions/runners" --jq ".runners[] | select(.name == \"$runner_name\") | .id" 2>/dev/null || true)
  if [ -n "$existing_id" ]; then
    echo "ℹ 检测到已存在同名 Runner (ID: $existing_id)，先安全替换清理..."
    gh api -X DELETE "repos/$repo/actions/runners/$existing_id" >/dev/null 2>&1 || true
  fi

  echo "==> 3. 获取仓库 [$repo] 动态注册 Token..."
  local token
  token=$(gh api -X POST "repos/$repo/actions/runners/registration-token" --jq .token)
  if [ -z "$token" ]; then
    echo "❌ 无法获取 Runner 注册 Token，请检查权限" >&2
    exit 1
  fi

  echo "==> 4. 在 [$host] 准备运行环境与代理..."
  ssh "$host" "
    set -euo pipefail
    mkdir -p ~/.cache/actions-runner \"$target_dir\"

    # 缓存 actions-runner 安装包，避免重复下载
    TARBALL=\"\$HOME/.cache/actions-runner/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz\"
    if [ ! -f \"\$TARBALL\" ]; then
      echo '   - 首次下载 actions-runner 安装包到主机缓存...'
      curl -sSL -x $PROXY_URL -o \"\$TARBALL\" \"https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz\"
    fi

    # 清理并解包到独立工作区
    tmux kill-session -t \"$session_name\" 2>/dev/null || true
    pkill -f \"$target_dir\" 2>/dev/null || true
    rm -rf \"$target_dir\"/*
    tar -zxf \"\$TARBALL\" -C \"$target_dir\"

    # 自动配置代理环境变量，确保永不遗忘
    cat << 'ENVEOF' > \"$target_dir/.env\"
HTTP_PROXY=$PROXY_URL
HTTPS_PROXY=$PROXY_URL
http_proxy=$PROXY_URL
https_proxy=$PROXY_URL
NO_PROXY=localhost,127.0.0.1,10.*,172.*,100.*
ENVEOF

    # 若存在通用环境模板 ~/.runner-env，追加合并
    if [ -f ~/.runner-env ]; then
      cat ~/.runner-env >> \"$target_dir/.env\"
    fi

    # 注册 Runner
    cd \"$target_dir\"
    ./config.sh --url \"https://github.com/$repo\" \
                --token \"$token\" \
                --name \"$runner_name\" \
                --labels \"self-hosted,Linux,X64,$host\" \
                --unattended \
                --replace

    # 在独立的 tmux 后台会话中启动
    tmux new-session -d -s \"$session_name\" \"cd '$target_dir' && ./run.sh\"
  "

  echo "==> 5. 校验 GitHub 在线状态..."
  sleep 3
  local status=""
  for i in {1..10}; do
    status=$(gh api "repos/$repo/actions/runners" --jq ".runners[] | select(.name == \"$runner_name\") | .status" 2>/dev/null || true)
    if [ "$status" = "online" ]; then
      break
    fi
    sleep 2
  done

  if [ "$status" = "online" ]; then
    echo "✓ 成功！Runner [$runner_name] 已在 [$host] 启动并上线！"
    echo "  - 仓库: $repo"
    echo "  - 标签: [self-hosted, Linux, X64, $host]"
    echo "  - 工作目录: $target_dir"
    echo "  - 查看日志: ./scripts/manage_runner.sh logs $repo $host"
  else
    echo "⚠️ Runner 已启动，但 GitHub 报告状态为 [$status]，可通过 logs 指令查看远端输出。"
  fi
}

cmd_stop() {
  local repo="${1:-$(detect_current_repo)}"
  local host="${2:-$DEFAULT_HOST}"

  if [ -z "$repo" ]; then
    echo "❌ 请指定仓库名 (例如: coolxll/agent-fleet-webui)" >&2
    exit 1
  fi

  check_gh
  local safe_repo
  safe_repo=$(safe_name "$repo")
  local runner_name="${host}-${safe_repo}"
  local session_name="runner-${safe_repo}"
  local target_dir="/home/lynn/runners/${safe_repo}"

  echo "==> 1. 从 GitHub 仓库 [$repo] 反注册 Runner [$runner_name]..."
  local rid
  rid=$(gh api "repos/$repo/actions/runners" --jq ".runners[] | select(.name == \"$runner_name\") | .id" 2>/dev/null || true)
  if [ -n "$rid" ]; then
    gh api -X DELETE "repos/$repo/actions/runners/$rid" >/dev/null 2>&1 || true
    echo "✓ 已从 GitHub 注销 Runner (ID: $rid)"
  else
    echo "ℹ GitHub 上未找到 Runner [$runner_name]，跳过 API 注销"
  fi

  echo "==> 2. 清理 [$host] 上的运行会话与目录..."
  ssh "$host" "
    tmux kill-session -t \"$session_name\" 2>/dev/null || true
    pkill -f \"$target_dir\" 2>/dev/null || true
    rm -rf \"$target_dir\"
  "
  echo "✓ 远端主机 [$host] 会话与目录清理完毕"
}

cmd_logs() {
  local repo="${1:-$(detect_current_repo)}"
  local host="${2:-$DEFAULT_HOST}"

  local safe_repo
  safe_repo=$(safe_name "$repo")
  local session_name="runner-${safe_repo}"

  echo "==> 捕获 [$host] 上的 tmux 会话 [$session_name] 输出..."
  ssh "$host" "tmux capture-pane -pt \"$session_name\" -S -50 2>/dev/null || echo 'ℹ 会话 $session_name 未在运行'"
}

main() {
  local action="${1:-}"
  shift || true

  case "$action" in
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

main "$@"
