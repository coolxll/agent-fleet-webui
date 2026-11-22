#!/usr/bin/env bash
#
# teardown_runner.sh: 一键彻底下线并注销 self-hosted runner
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="${1:-coolxll/agent-fleet-webui}"
HOST="${2:-runner-node-01}"

# 调用通用的 manage_runner 工具
if [ -f "$SCRIPT_DIR/manage_runner.sh" ]; then
  bash "$SCRIPT_DIR/manage_runner.sh" stop "$REPO" "$HOST"
fi

# 兼顾清理旧命名规则的历史临时目录与会话
ssh -n -o BatchMode=yes -o ConnectTimeout=8 -o ServerAliveInterval=15 -o ServerAliveCountMax=2 "$HOST" '
  tmux kill-session -t actions-runner-fleet 2>/dev/null || true
  tmux kill-session -t actions-runner-fleet-2 2>/dev/null || true
  for pid in $(ps -eo pid=,args= | awk -v self="$$" -v parent="$PPID" '"'"'/[a]ctions-runner-fleet/ && (/[R]unner\.(Listener|Worker)/ || /[r]un-helper\.sh/ || /[r]un\.sh/) && $1 != self && $1 != parent {print $1}'"'"'); do
    kill "$pid" 2>/dev/null || true
  done
  rm -rf ~/actions-runner-fleet ~/actions-runner-fleet-2
' 2>/dev/null || true

echo "✓ 下线与清理已完成"
