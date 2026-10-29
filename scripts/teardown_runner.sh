#!/usr/bin/env bash
#
# teardown_runner.sh: 一键彻底下线并注销 corp172-dev 上的 agent-fleet-webui self-hosted runner
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 调用通用的 manage_runner 工具
if [ -f "$SCRIPT_DIR/manage_runner.sh" ]; then
  bash "$SCRIPT_DIR/manage_runner.sh" stop "coolxll/agent-fleet-webui" "corp172-dev"
fi

# 兼顾清理旧命名规则的历史临时目录与会话
ssh -o BatchMode=yes "corp172-dev" "
  tmux kill-session -t actions-runner-fleet 2>/dev/null || true
  tmux kill-session -t actions-runner-fleet-2 2>/dev/null || true
  pkill -f 'actions-runner-fleet' 2>/dev/null || true
  rm -rf /home/lynn/actions-runner-fleet /home/lynn/actions-runner-fleet-2
" 2>/dev/null || true

echo "✓ 下线与清理已完成"
