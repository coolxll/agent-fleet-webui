# Progress

Last updated: 2026-10-18

Requirements source: `requirements.md` (IDs such as `CFG-3` referenced below).

## Stages

### 1. Skeleton + inventory (done)

* FastAPI + SQLite + SQLAlchemy backend, React/TS/Vite frontend.
* Inventory loaders for Ansible YAML, Ansible INI, `~/.ssh/config` and the
  project YAML format, merged by host name.
* Verified against the real workspace: 20 hosts / 28 runtimes imported from
  this machine's SSH config and the homelab Ansible inventory.

### 2. Remote collection (done)

* One read-only shell script per runtime, streamed to `sh -s` over one
  `ssh` connection, replying with an `AF<TAB>...` record stream.
* Local runner (controller machine), SSH runner, Windows runner, WSL runner.
* Verified on real hosts: macOS (local), 6 Linux servers, 3 Windows hosts and
  their WSL distros.

### 3. Adapters (done)

* **Codex**, **Claude Code**, **OpenCode**, **Pi**, **Hermes**: detection,
  version, install method, and configuration parsing for all five.
* Fixture-verified: installed, absent, config-only, missing file, empty file,
  unreadable file, malformed TOML/YAML, JSONC comments, secrets present.

### 4. Sanitization (done)

* Whitelist + recursive key dropping + value redaction + URL masking.
* A real leak was found and fixed during development: an OpenCode
  `provider.<name>.options.apiKey` nested under a harmlessly named key.
  The structural `env_keys` field was being dropped by its own sanitizer;
  both issues are now covered by tests.
* Post-fix verification across all 20 real hosts: no credential-shaped string
  appears in any API response.

### 5. Scanning, snapshots, failures (done)

* Bounded concurrency (4 per run, 8 concurrent SSH sessions overall),
  per-runtime timeouts, per-run progress.
* Failed scans keep the last known good data and mark the runtime stale.
* Historical snapshots per runtime.

### 6. Web UI (done)

* Overview (with per-machine scan + enable/disable), Matrix, Inspector with
  filters; scan controls and live progress. Stage 8 added the Compare tab and
  the Export JSON button.

### 7. Tests + docs (done)

* 92 passing tests.

### 8. Comparison, scopes, scheduling, export (done)

* **Snapshot diff + two-machine comparison**: `services/diff.py` produces
  field-level change records for two installations (same agent on different
  machines) or two snapshots (history of one runtime). UI "Compare" tab.
* **Scope discovery + precedence**: every adapter declares a
  system -> user -> project chain. Claude derives project roots from
  `~/.claude.json`; Codex from its `[projects]` table; OpenCode from its
  declared workspace roots. A second probe pass reads project-scope files
  (`.claude/settings.json`, `.mcp.json`, `opencode.json`, ...), capped at 5
  roots per agent and 30 files per runtime.
  Precedence is applied per agent: Claude merges system < user < local <
  project settings plus project `.mcp.json`; OpenCode parses the single
  highest-precedence readable document; Codex, Pi and Hermes normalize the
  user-scope file and report the system policy as a scope without merging it.
  `resolution` is "effective" only when every scope in the chain was readable
  or definitively absent; otherwise "declared" plus a warning naming the
  unreadable file (CFG-3). The stored values are always declared file values:
  environment variables and CLI flags are outside the probe.
* **Scheduled scanning**: one background loop in the API process, state
  persisted in `app_state` so a restart resumes. A due run is skipped, never
  queued, when a manual run is active.
* **JSON export**: `/api/export` (optionally filtered by runtime, agent, or
  detected-only) returns the whole fleet as one sanitized document; the UI has
  an Export JSON button.

## Verified on real machines

| Machine | Runtime | Result |
| --- | --- | --- |
| local-controller (macOS 26.5 arm64) | macos | all 5 agents, config parsed |
| rn-direct (Ubuntu 24.04) | linux | codex 0.117.0, claude 2.1.187, opencode 1.19.0, hermes 0.21.7; pi absent |
| x13 (Ubuntu) | linux | codex 0.162.1, claude 2.1.302, opencode 1.2.0 |
| k14 | wsl | codex 0.153.4, opencode 1.18.20, pi 1.3.0, hermes 0.21.7 |
| corp172-dev | linux | codex 0.154.0, claude 2.1.226, opencode 1.18.18, pi 1.2.0 |
| la-tri | linux | reachable, no agents installed |
| r9kp-win | windows | Windows 11; codex 0.162.1, claude 2.1.226, opencode 1.18.4, pi 1.1.0, hermes config-only |
| r9kp-win | wsl:Ubuntu-22.04 | probed through `wsl.exe` |
| wujie-win | windows | 4 agents + WSL distro |
| k14-win | windows | reachable, no agents installed; WSL archlinux probed |
| r9u | - | unreachable: connection timed out (reported, not crashed) |

Two-host end-to-end: **local-controller + rn-direct**, plus a full 27-runtime
fleet scan.

## WSL policy (changed 2026-10-18, after user feedback)

WSL distros are reached through their own SSH endpoints (wujie, r9kp-wsl, ...),
which the SSH config already provides. Auto-discovery through the Windows host
(wsl.exe -l -q) duplicated those, so it is now off by default
(AGENT_FLEET_DISCOVER_WSL=false). The duplicated rows were removed and the two
distros without a direct connection (docker-desktop, k14-win archlinux) were
disabled with a reason rather than deleted.

Also fixed in the same pass: a kind hint in inventory.yaml was overwriting the
kind observed by a probe on every restart, so wujie showed "linux" although it
is a WSL distro. Hints now only apply before the first scan.

## Fleet scope (2026-10-18)

The fleet now contains exactly the machines the user asked for. Everything
else that used to leak in from ~/.ssh/config (corp172, ddrk, jph2, la-tri, pve,
r9u, racknerd) is excluded at the host level rather than deleted, so its
history is still there and the Overview has a "show excluded hosts" checkbox.

k14 turned out to be the WSL side of k14-win (kernel
6.6.114.1-microsoft-standard-WSL2, /mnt/c present), the same relationship as
wujie/wujie-win and r9kp-wsl/r9kp-win. The two auto-discovered distros with no
direct SSH endpoint (k14-win/wsl:archlinux, r9kp-win/wsl:docker-desktop) were
removed with the new DELETE /api/runtimes/{id} endpoint.

## Known limitations / not done

**Not implemented (phase 1 is read-only by design)**

* Config modification, provider switching, API key sync, remote install or
  upgrade, remote task execution, session orchestration.

**Known gaps**

* Windows config parsing uses the same adapters, so a few Windows-specific
  layout variants will show up as `partial` rather than `parsed`.
* Merging is not uniform across agents: Claude merges system/user/local/project
  settings; OpenCode takes the highest-precedence document; Codex, Pi and
  Hermes normalize only the user-scope file (the system policy is reported as
  a scope but not applied). Claude's per-project MCP servers recorded in
  `~/.claude.json` are listed separately and a warning says so.
* "Effective" only ever covers the config files in the chain. Environment
  variables and CLI flags are never observed, so no value is presented as
  the definitive runtime setting.
* If the project-scope pass fails, project values are missing while the scan
  still succeeds; scopes show `not_probed` and `resolution` remains
  `effective` because the missing files never entered the chain check.
* Skills are counted and listed by directory/`SKILL.md` presence; frontmatter
  is not parsed.
* Stale runtime rows are not garbage collected: renaming a WSL distro in the
  inventory leaves the old row behind until it is removed by hand.
* Snapshot history is not pruned.
* The API has no authentication; bind it to loopback.

**Done in stage 8 (previously listed as gaps)**

* Snapshot history has a diff view, and two installations of the same agent
  can be compared side by side.

**Partially verified**

* Windows: 3 hosts probed natively. Encoding of non-ASCII (Chinese) OS captions
  required forcing UTF-8 output in the PowerShell probe.
* WSL: distro discovery and POSIX probing through `wsl.exe` work on the hosts
  that were reachable during development.
