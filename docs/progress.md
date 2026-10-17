# Progress

Last updated: 2026-10-18

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
  filters; scan controls and live progress.

### 7. Tests + docs (done)

* 62 passing tests.

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

## Known limitations / not done

**Not implemented (phase 1 is read-only by design)**

* Config modification, provider switching, API key sync, remote install or
  upgrade, remote task execution, session orchestration.

**Known gaps**

* Windows config parsing uses the same adapters, so a few Windows-specific
  layout variants will show up as `partial` rather than `parsed`.
* Project-scope and system-scope configurations are not merged into the
  user-scope view. Claude's per-project MCP servers are listed separately and
  a warning says so.
* "Effective configuration" is never claimed - only declared values are shown.
* Skills are counted and listed by directory/`SKILL.md` presence; frontmatter
  is not parsed.
* Snapshot history is stored but there is no diff view yet (P1).
* Scheduled scanning is not implemented; scans are manual.
* Stale runtime rows are not garbage collected: renaming a WSL distro in the
  inventory leaves the old row behind until it is removed by hand.

**Partially verified**

* Windows: 3 hosts probed natively. Encoding of non-ASCII (Chinese) OS captions
  required forcing UTF-8 output in the PowerShell probe.
* WSL: distro discovery and POSIX probing through `wsl.exe` work on the hosts
  that were reachable during development.
