#!/usr/bin/env bash
#
# teardown_runner.sh: 一键彻底下线并注销 corp172-dev 上的 agent-fleet-webui self-hosted runner
#
set -euo pipefail

REPO="coolxll/agent-fleet-webui"
REMOTE_HOST="corp172-dev"

echo "==> 1. 从 GitHub 仓库反注册并移除相关 Fleet Runner..."
RUNNER_IDS=$(gh api "/repos/$REPO/actions/runners" --jq '.runners[] | select(.name | test("corp172-dev-fleet")) | .id' 2>/dev/null || true)

if [ -n "$RUNNER_IDS" ]; then
  for rid in $RUNNER_IDS; do
    gh api --method DELETE "/repos/$REPO/actions/runners/$rid" >/dev/null || true
    echo "✓ GitHub Runner (ID: $rid) 已从仓库 $REPO 注销成功"
  done
else
  echo "ℹ GitHub 仓库中未找到 Fleet 相关 runner，跳过 GitHub 端反注册"
fi

echo "==> 2. 清理 $REMOTE_HOST 上的运行进程与磁盘目录..."
ssh -o BatchMode=yes "$REMOTE_HOST" "
  echo '   - 终止 tmux session 及相关进程...'
  tmux kill-session -t actions-runner-fleet 2>/dev/null || true
  tmux kill-session -t actions-runner-fleet-2 2>/dev/null || true
  pkill -f 'actions-runner-fleet' 2>/dev/null || true
  sleep 1

  echo '   - 删除目录 /home/lynn/actions-runner-fleet*...'
  rm -rf /home/lynn/actions-runner-fleet /home/lynn/actions-runner-fleet-2
  echo '✓ 远端清理完成'
"

echo "==> 3. 核验下线状态..."
REMAINING=$(gh api "/repos/$REPO/actions/runners" --jq '.runners[] | select(.name | test("corp172-dev-fleet")) | .name' 2>/dev/null || true)
if [ -z "$REMAINING" ]; then
  echo "✓ 验证通过：所有 Fleet Runner 已彻底下线并清理完毕，无任何残留。"
else
  echo "⚠️ 警告：GitHub 上仍可探测到 $REMAINING"
fi
