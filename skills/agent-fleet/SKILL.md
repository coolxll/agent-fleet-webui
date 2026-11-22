---
name: agent-fleet
description: Query and coordinate multi-machine coding agents via the agent-fleet CLI (matrix, config compare, quotas, tasks, daemons). Use before dispatching remote agent work (e.g. via herdr) or when auditing agent versions, providers, models, and quotas across machines.
---

# Agent Fleet CLI Skill

Use `agent-fleet` to inspect the cached fleet state across machines (macOS, Linux, Windows, WSL) in compact, sanitized JSON without touching remote hosts or leaking secrets.

## Invocation

If `agent-fleet` is on `PATH` (via symlink to `scripts/agent-fleet`):

```bash
agent-fleet <subcommand> [options]
```

Otherwise, invoke directly from the repository:

```bash
<repo>/scripts/agent-fleet <subcommand> [options]
```

## Read-Only Commands (Fast, Offline, Side-Effect-Free)

All commands below read only from the local SQLite database and sanitized local quota stores. They never run SSH probes, never make outbound network calls, and never print raw tokens or credentials.

### 1. Fleet Matrix (`matrix`)

Inspect which agents are installed on which machines, with versions, providers, models, masked `base_url`, and warnings:

```bash
agent-fleet matrix
agent-fleet matrix --agent codex
agent-fleet matrix --host corp172-dev
agent-fleet matrix --agent claude --host wujie
```

### 2. Cross-Machine Config Diff (`compare`)

Diff the same agent across two machines (`<host>` or `<host>/<runtime_key>` or `<runtime_id>`):

```bash
agent-fleet compare local-controller corp172-dev --agent claude
agent-fleet compare wujie r9kp/wsl --agent codex
```

Returns field-level `changes` (`added` / `removed` / `changed`) and `identical: true|false`.

### 3. Quota Summary (`quotas`)

Inspect sanitized quota readings across runtime snapshots, imported accounts, and Magpie source status:

```bash
agent-fleet quotas
agent-fleet quotas --agent codex
```

Check `min_remaining_fraction` and account `status` to avoid dispatching work to exhausted or errored accounts.

### 4. Cached Daemon Status (`daemons`)

Inspect cached Herdr and Paseo daemon health, versions, workspace/session counts, and staleness:

```bash
agent-fleet daemons
agent-fleet daemons --host corp172-dev
```

### 5. Task Board (`tasks`)

List or create task board items:

```bash
agent-fleet tasks list
agent-fleet tasks list --status todo
agent-fleet tasks create \
  --title "Fix auth refresh race" \
  --description "Reproduce and patch token refresh lock" \
  --target-host corp172-dev \
  --agent codex \
  --project-path /workspace/my-repo
```

Valid statuses: `todo`, `in_progress`, `waiting`, `review`, `done`.

## Recommended Dispatch Workflow

Before dispatching remote coding work (e.g. via `herdr --machine <host>`):

1. **Check availability & config**: run `agent-fleet matrix --agent <agent>` to confirm the target host is reachable (`reachable: true`), has the agent `installed`, and uses the expected `provider`/`model`.
2. **Check quota headroom**: run `agent-fleet quotas --agent <agent>` to verify remaining quota (`min_remaining_fraction`) and account health.
3. **Check daemon readiness**: run `agent-fleet daemons --host <host>` to confirm `herdr` (or `paseo`) is `running` and not stale.
4. **Optionally log work**: run `agent-fleet tasks create --title "..." --target-host <host> --agent <agent>` to record the task on the fleet board.
5. **Dispatch**: execute via `herdr --machine <host> ...` (or your configured remote runner).

## Mutating / Live Commands (Use Only When Needed)

- `agent-fleet sync`: sync inventory from `inventory.yaml` into SQLite.
- `agent-fleet scan [--host <host>] [--runtime <id>]`: run live SSH/local probes and persist fresh snapshots.
- `agent-fleet probe <host>`: run a one-off probe without persisting.

Only run `scan` or `sync` when cached data is stale (`stale: true`) and the user expects live remote checks.
