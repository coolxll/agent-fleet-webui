# Requirements

What Agent Fleet Explorer must do, why, and how each requirement is verified.
This is the requirements source the other documents refer to:

* `research.md` - how the collection mechanisms were chosen and validated.
* `architecture.md` - how the requirements are implemented.
* `progress.md` - which requirements are reached, and the known gaps.

Last updated: 2026-10-29

## 1. Problem

AI coding agents (Codex CLI, Claude Code, OpenCode, Pi, Hermes, AGY, CodeBuddy,
Qoder) are installed ad hoc across workstations and servers running macOS,
Linux, Windows and WSL. Each one keeps its configuration in its own format
and location, per user and per machine. There is no single place to answer:

* which agents are installed on each machine, at which version;
* which model, provider, endpoint, MCP servers, skills and quota pools each one is
  configured to use;
* how two machines - or the same machine before and after an upgrade - differ;
* how to coordinate remote coding tasks and maintain agent execution lifecycles.

The existing tools surveyed in `research.md` §1 either forcibly mutate configuration
(CC-Switch, Magpie) or require a heavy agent/daemon installed on every endpoint
(FleetDM).

**Goal:** a dual-mode control plane over the fleet:
1. **Read-only Discovery & Observability:** zero-side-effect, non-invasive fleet
   probing, configuration inspection, version tracking, and quota monitoring
   reached through existing SSH credentials with nothing permanently installed
   on remote endpoints.
2. **Controlled Remote Operational Actions:** explicit, user-initiated write
   operations (one-click agent upgrades, runner fleet lifecycles, and task board
   handoff) executed over authorized SSH with strict safety whitelists and
   parameterless command synthesis.

## 2. Users and scenarios

The primary user is a developer/operator who already has SSH access to a fleet
of development machines.

| Scenario | Expected outcome |
| --- | --- |
| Fleet audit | Every host and runtime with the agents installed and their config summary |
| Drift detection | The same agent compared across two machines, field by field |
| Upgrade history | Two snapshots of one runtime compared, field by field |
| One-click upgrade | Controlled upgrade of outdated agents across runtimes via whitelist templates |
| Runner fleet ops | Declarative synchronization and lifecycle control of GitHub Actions runners |
| Task handoff | Task board dispatch relaying coding goals to remote agent sessions |
| Review / export | One sanitized JSON export of the whole fleet |
| Recurring check | Scheduled fleet scans with visible progress and stale markers |

Phase 1 assumed a single operator. The platform has since introduced API token
authentication (`AGENT_FLEET_API_SECRET`) supporting both anonymous read-only
and strict authenticated modes.

## 3. Hard constraints and trust boundary

These constraints are non-negotiable and every feature must preserve them.

| ID | Constraint | Reason |
| --- | --- | --- |
| HC-1 | **Non-invasive discovery; controlled operations.** Routine fleet scans, inventory probes and config discovery must remain strictly read-only and non-invasive. Remote write operations (one-click agent upgrades, runner fleet lifecycles, and task board execution) are strictly opt-in, parameterless or whitelisted, and must only execute upon explicit user action. | Production/shared hosts must be safe to monitor continuously; operations must never have accidental side effects. |
| HC-2 | **Nothing permanently installed on remotes.** Probes and operational commands rely exclusively on standard shells and binaries already present (e.g. bash, sh, powershell, npm, git). | No package manager, agent daemon or Python runtime can be mandated on remote targets. |
| HC-3 | **Reuse existing SSH configuration and credentials.** No keys or passwords are collected, copied or stored. | `~/.ssh/config` (ProxyJump, ports, Tailscale names, agents) already works; duplicating it creates a dangerous second credential path. |
| HC-4 | **No credential ever reaches storage or an API response.** | Remote configs and memory dumps routinely contain live API keys and tokens. |
| HC-5 | **Raw remote config is never persisted.** Raw bytes exist in memory during one scan only. | Removing the raw copy removes the main blast radius of a database leak. |
| HC-6 | **Single local process, no external services.** SQLite plus the FastAPI process. | Deployable on a laptop or container; no message queues or external database clusters to operate. |

### Trust Boundary & Privilege Model

* **Controller OpenSSH Access:** The controller machine requires authorized OpenSSH credentials (`~/.ssh/config` identity files, SSH agent, or keypairs) to reach remote fleet nodes.
* **Target Execution Privileges:** Remote operations execute strictly under the permissions of the configured SSH user on the target machine. Root / sudo is never requested or assumed.
* **Zero Command Injection (Action Whitelist):** Remote write operations (upgrades, runner actions) never execute arbitrary client-supplied strings. Commands are synthesized from strict server-side whitelists (`UPGRADE_RULES`) and parameterless templates run through `PATH_BOOTSTRAP` with per-runtime concurrency locks.

## 4. Functional requirements

IDs are stable references for issues and commits. "Verified by" points at the
automated test or the evidence in `progress.md`.

### Inventory

| ID | Requirement | Acceptance criteria | Verified by |
| --- | --- | --- | --- |
| INV-1 | Load Ansible YAML/INI inventories and the project YAML. `~/.ssh/config` is not auto-imported. | All three formats parse; a host appearing in several sources is stored once, later sources only fill gaps per field. An SSH alias alone never creates a host. | `tests/test_inventory.py` |
| INV-2 | The project YAML declares runtime kind hints, WSL distro lists, tags and disabled state. | These fields are applied before the first probe; a probe observation still wins for runtime kind. | `tests/test_inventory.py` |
| INV-3 | Inventory sync is idempotent and non-destructive. | Re-running sync updates existing hosts/runtimes instead of duplicating them; removed hosts are not deleted. | `tests/test_inventory.py`, manual runs |
| INV-4 | The controller itself is a runtime without SSH. | With `AGENT_FLEET_INCLUDE_LOCAL_RUNTIME=true`, the local machine is probed through a local shell. | manual (`progress.md`) |

### Collection

| ID | Requirement | Acceptance criteria | Verified by |
| --- | --- | --- | --- |
| COL-1 | Collect everything in as few round trips as possible. | The union of adapter `probe_spec()`s is rendered into one script per runtime; project-scope files, whose paths are only known after the user-scope pass, use one additional small pass (≤30 files). | `tests/test_probe.py`, `tests/test_scan.py` |
| COL-2 | Never write on the remote. | The generated script contains no output redirection, `rm`, `tee`, `curl`; only stderr redirection. | `test_generated_script_is_read_only_and_quoted` |
| COL-3 | Capture real command outcomes. | The real exit code is captured before output is piped; empty output is a scan error, not "no agents". | `test_generated_script_captures_real_exit_code`, `test_empty_output_is_an_error_not_absence` |
| COL-4 | Reach macOS, Linux, Windows and WSL. | POSIX: script streamed to `sh -s` over `ssh`. Windows: PowerShell `-EncodedCommand`, chunked below the ~8k limit. WSL: `wsl.exe -d <distro> -- sh -s`. | `tests/`, manual on real hosts (`progress.md`) |
| COL-5 | Bound cost per runtime. | Per-runtime timeout (`AGENT_FLEET_COMMAND_TIMEOUT`, 25s); files capped at 512 KB, directory listings at 400 entries; oversized/truncated files are reported as unreadable. | `tests/test_probe.py`, `tests/test_scan.py` |
| COL-6 | Bounded concurrency. | 4 runtimes per run by default, hard ceiling of 8 simultaneous SSH connections; one slow host does not block others. | `test_slow_host_does_not_block_others` |
| COL-7 | Discover WSL distros. | A Windows runtime lists its distros; each becomes its own runtime with its own home, user and agents. | manual (`progress.md`) |

### Adapters and detection

| ID | Requirement | Acceptance criteria | Verified by |
| --- | --- | --- | --- |
| AD-1 | One adapter per agent. | Codex, Claude Code, OpenCode, Pi and Hermes implement `probe_spec`, `detect`, `discover_config`, `parse_config`. | `tests/test_adapters.py` |
| AD-2 | Adapters only see `RemoteFacts`. | Adapters are pure functions of fixture facts and can be tested with no machine or network. | `tests/test_adapters.py` |
| AD-3 | Correct install states. | Distinguish `installed`, `not_installed`, `config_only` (binary gone, config present), `unknown` (not probed) and `not_scanned`. | `tests/test_adapters.py`, `tests/test_scan.py` |
| AD-4 | Report version, executable path and install method. | Version output is parsed; non-zero version commands are surfaced as errors without inventing a version. | `tests/test_adapters.py` |
| AD-5 | Normalize the config surface. | Model, provider, masked base URL, MCP servers, skills, declared settings, unsupported field names and warnings, per agent. | `tests/test_adapters.py`, `tests/fixtures/` |

### Scopes and precedence

| ID | Requirement | Acceptance criteria | Verified by |
| --- | --- | --- | --- |
| CFG-1 | Discover system, user, local and project scope files. | Every adapter declares a precedence chain and reports each file with a state (`ok`, `missing`, `unreadable`, `not_probed`, `no_projects`). | `tests/test_scopes.py` |
| CFG-2 | Apply precedence where the agent defines it. | Claude merges system < user < local < project settings and project `.mcp.json`; OpenCode parses the single highest-precedence readable document; Codex, Pi and Hermes normalize the user-scope file and report the system policy as a scope without applying it (documented limitation). | `test_project_settings_override_user_settings`, `tests/test_scopes.py` |
| CFG-3 | Never claim more than was read. | `resolution` is `effective` only when every file in the chain was readable or definitively absent; otherwise `declared`, plus a warning naming the unreadable file. A file the probe never visited (project pass failed) stays `not_probed` and does not lower the verdict. | `test_unreadable_scope_blocks_the_effective_claim` |
| CFG-4 | Report project roots honestly. | Project roots come from Claude's projects table, Codex's `[projects]` table and OpenCode's declared workspace roots; no roots is reported as `no_projects`, not guessed. | `test_no_projects_is_reported_honestly` |

### Sanitization and secrecy

| ID | Requirement | Acceptance criteria | Verified by |
| --- | --- | --- | --- |
| SEC-1 | Field whitelist. | Adapters emit known fields only; unknown keys are reported by name with values dropped. | `tests/test_sanitize.py` |
| SEC-1b | Bound output size. | Strings are capped at 240 chars, collections at 200 entries, nesting depth is bounded; oversized config files (>512 KB) and truncated probe streams are reported as unreadable rather than partially trusted. | `tests/test_sanitize.py`, `tests/test_probe.py` |
| SEC-2 | Drop credential-named keys recursively. | Keys such as `apiKey`, `token`, `password`, `Authorization` are removed at any nesting depth, including inside innocently named blocks. | `test_nested_secret_is_dropped_from_structure` |
| SEC-3 | Redact credential-looking values. | `sk-...`, `ghp_...`, JWTs and `Bearer <token>` are replaced even under an innocent key. | `tests/test_sanitize.py` |
| SEC-4 | Mask URLs. | Scheme + host + port + path are kept; embedded credentials, query strings, fragments and secret-looking path segments are stripped. | `test_url_masking` |
| SEC-5 | Environment blocks by name only. | Only key names are shown, never values, for user env blocks and MCP `env`/`headers`. | `test_env_blocks_report_names_only` |
| SEC-6 | Hard deny list. | The probe generator refuses `.ssh/`, private keys, `.aws/credentials`, `.npmrc`, `.netrc`, `.kube/config`, keychain and similar paths even if an adapter asks for them; Pi `auth.json` and Hermes `.env` are never requested. | `test_generated_script_is_read_only_and_quoted` |
| SEC-7 | Raw config is not persisted. | SQLite stores only the sanitized structure; raw content is dropped after parsing. | `tests/test_scan.py`, code review of `services/persist.py` |
| SEC-8 | One central scrub. | Every adapter result passes through `scrub_config()` before storage, so a new adapter cannot leak by forgetting to sanitize. | `tests/test_export.py`, `test_fixture_configs_never_leak` |

### Storage and failure handling

| ID | Requirement | Acceptance criteria | Verified by |
| --- | --- | --- | --- |
| DATA-1 | Identity is (host, runtime, user, agent). | Windows native hosts and each WSL distro are distinct runtimes and never merged. | `tests/test_scan.py` |
| DATA-2 | Keep last known good data. | A failed scan updates connectivity and the error only; agent facts and the last snapshot stay untouched. | `test_connection_failure_keeps_previous_data` |
| DATA-3 | Mark data stale. | A runtime is flagged `stale` once its last success is older than `AGENT_FLEET_SCAN_TTL_SECONDS` (6h) plus a 60s grace; a runtime that never succeeded has no stale flag and is reported as not scanned. | `api/routes.py` (`_stale`), API check |
| DATA-4 | Snapshot every success. | Each successful scan writes one snapshot (status, version, sanitized config) per runtime. | `test_scan_service_records_per_runtime_tasks` |
| DATA-5 | Config retention. | When a scan produces nothing usable (`empty`/`error`/`unsupported`), the previous config is kept and still pointed at. | `tests/test_scan.py`, code review of `services/persist.py` |

### Scanning and scheduling

| ID | Requirement | Acceptance criteria | Verified by |
| --- | --- | --- | --- |
| SCAN-1 | Manual scans. | "Scan all" scans every enabled runtime; a single runtime can be scanned from its row. A runtime disabled after the run was created is marked `skipped`, not counted as a failure. | `tests/test_api.py`, `tests/test_scan.py`, `services/scan_service.py` |
| SCAN-2 | Observable progress. | A run exposes total/done/ok/failed while in flight. | `tests/test_scan.py`, `tests/test_api.py` |
| SCAN-3 | Scheduling. | One background loop in the API process; state persisted in `app_state`; interval floored at 60s (UI offers 10 min - 24 h); a due run is skipped, never queued, while another run is active; next run counts from the last start. | `tests/test_scheduler.py` |
| SCAN-4 | Restart-safe schedule. | A backend restart resumes the persisted schedule (enabled flag and interval). | `tests/test_scheduler.py` |

### Comparison, export, interfaces

| ID | Requirement | Acceptance criteria | Verified by |
| --- | --- | --- | --- |
| CMP-1 | Compare two installations. | Field-level `added`/`removed`/`changed` records; a different agent on each side is flagged. | `tests/test_diff.py` |
| CMP-2 | Compare two snapshots. | Same change records over the snapshot payload (upgrade, install, removal). | `tests/test_diff.py` |
| EXP-1 | Export the fleet. | `GET /api/export` returns one `agent-fleet-export/1` document, filterable by runtime, agent and detected-only, containing only sanitized data. | `tests/test_export.py` |
| UI-1 | Four views. | Fleet overview (totals, runtime table, per-row scan/enable), matrix (installed / absent / config-only / scan-failed / not-scanned / unknown / error), inspector (full sanitized detail), compare (two machines or two snapshots). | `web/src/views/`, manual |
| API-1 | Read-only HTTP API plus scan triggers. | All routes under `/api`; reads never mutate remote state; the only writes are scan runs, schedule state, inventory sync and the runtime enable flag. | `tests/test_api.py` |
| CLI-1 | Command-line parity. | `sync`, `runtimes`, `probe <target>`, `scan [--host <name> | --runtime <id>]` work without the UI. | `python -m agent_fleet.cli --help`, manual |
| OPS-1 | Safe default deployment. | One process, SQLite only, no authentication; intended to bind to loopback and not be exposed to untrusted networks. | code review (`main.py`, CORS) |
| OPS-2 | Configuration by environment. | `AGENT_FLEET_*` variables cover storage, inventory, timeouts, concurrency, TTL, auth secret and local runtime. | `README.md`, `settings.py` |
| OPS-3 | API access control. | Optional `AGENT_FLEET_API_SECRET` enforces Bearer token authentication; supports `AGENT_FLEET_ALLOW_ANONYMOUS_READONLY=true` (default: read allowed, writes require auth) or `false` (strict auth required for all routes). | `tests/test_security_auth.py` |

### Remote operations and execution

| ID | Requirement | Acceptance criteria | Verified by |
| --- | --- | --- | --- |
| UPG-1 | One-click remote agent upgrade. | Whitelisted, parameterless script synthesis from `UPGRADE_RULES` across POSIX (`sh`) and Windows (`powershell`); zero arbitrary command execution; per-runtime lock; background tasks with observable progress; automatic post-upgrade re-scan. | `tests/test_action_service.py` |
| RUN-1 | Declarative runner fleet lifecycle. | Orchestrates self-hosted GitHub Actions runners via `config/runner_fleet.yaml` and `scripts/manage_runner.sh`; outbound-only polling; proxy routing; precise slot matching and process group boundary isolation; ghost runner pruning. | `tests/test_manage_runner.py`, `tests/test_runner_fleet.py` |
| TASK-1 | Task board dispatch and handoff. | Dispatches coding tasks to remote POSIX runtimes (Claude Code / Codex); protocol-observed execution; native session resumption or new session handoff; transcript lineage recorded in SQLite. | `tests/test_tasks.py`, `tests/test_sessions.py` |

## 5. Platform × Agent × Capability Matrix

The support matrix across target operating systems, agents, and platform capabilities:

### 5.1 Agent × Capability Matrix

| Agent ID | Display Name | Probe & Discovery | Quota Ingestion | Task Dispatch & Handoff | Agent Upgrade |
| --- | --- | --- | --- | --- | --- |
| `claude` | Claude Code | **Supported (Full)** | Unsupported (No consumer API) | **Supported (Full)** (POSIX) | **Supported (Full)** (`cli` / `npm`) |
| `codex` | Codex CLI | **Supported (Full)** | **Supported (Full)** (OAuth / Magpie) | **Supported (Full)** (POSIX) | **Supported (Full)** (`npm`) |
| `agy` | Antigravity (AGY) | **Supported (Full)** | **Supported (Full)** (Google Quotas) | Unsupported (Roadmap) | **Supported (Full)** (`agy update`) |
| `opencode` | OpenCode | **Supported (Full)** | Unsupported (BYOK / Multi-provider) | Unsupported (Roadmap) | **Supported (Full)** (`cli` / `npm`) |
| `pi` | Pi Coding Agent | **Supported (Full)** | Unsupported (BYOK / Multi-provider) | Unsupported (Roadmap) | **Supported (Full)** (`cli` / `npm`) |
| `hermes` | Hermes Agent | **Supported (Full)** | Unsupported (BYOK / Multi-provider) | Unsupported (Roadmap) | **Supported (Full)** (`cli` / installer) |
| `codebuddy` | CodeBuddy / WorkBuddy | **Supported (Full)** | **Supported (Full)** (Token / Local Ext) | Unsupported (Roadmap) | **Supported (Full)** (`npm`) |
| `qoder` | Qoder / QoderCN | **Supported (Full)** | **Supported (Full)** (OpenAPI / Token) | Unsupported (Roadmap) | **Supported (Full)** (`cli` / `npm`) |

### 5.2 Platform × Capability Matrix

| Platform / Environment | Probe & Discovery | Quota Collection | Task Dispatch & Handoff | Agent Upgrade Execution | Runner Fleet Orchestration |
| --- | --- | --- | --- | --- | --- |
| **macOS (Darwin)** | **Supported (Full)** (POSIX sh) | **Supported (Full)** (Controller / local snapshot) | **Supported (Full)** (Local / SSH POSIX) | **Supported (Full)** (POSIX scripts) | **Supported (Partial)** (Bash runner; launchd pending verification) |
| **Linux (Deb/Ubuntu/RHEL)** | **Supported (Full)** (POSIX sh) | **Supported (Full)** (Controller / local snapshot) | **Supported (Full)** (Local / SSH POSIX) | **Supported (Full)** (POSIX scripts) | **Supported (Full)** (systemd / background daemon + proxy) |
| **Windows (Native PowerShell)** | **Supported (Full)** (Chunked b64 PowerShell) | **Supported (Full)** (Controller / local snapshot) | Unsupported (POSIX streaming required) | **Supported (Full)** (PowerShell scripts) | Unsupported (Bash / Linux runner environment required) |
| **WSL2 (Windows Subsystem)** | **Supported (Full)** (`wsl.exe` / SSH POSIX) | **Supported (Full)** (Controller / local snapshot) | **Supported (Full)** (SSH POSIX target) | **Supported (Full)** (POSIX scripts) | **Supported (Partial)** (Runs inside Linux distro) |

> **Verification status notes**:
> - *Supported (Full)*: Covered by unit/integration test suites and verified against target specifications.
> - *Supported (Partial)*: Functional under documented constraints (e.g. WSL requiring direct SSH runtime for task handoffs; macOS runner fleet requiring manual supervisor configuration).
> - *Pending Real-Host Verification*: Script synthesis and unit mocks implemented; awaiting dedicated automated test on real bare-metal Windows CI node.
> - *Unsupported / Not Applicable*: By architectural design (e.g. Claude Code having no public consumer quota API; Windows native shell lacking POSIX pty streaming for task board handoffs).

## 6. Non-goals

Deliberately out of scope for the control plane:

* **Arbitrary command execution:** No arbitrary shell commands or untyped scripts can be sent via the API; all remote execution paths are whitelisted and parameterless.
* **Persistent remote daemons:** No proprietary daemon, background agent or telemetry service is installed on remote targets.
* **Remote credential stores tampering:** No collection, modification or transmission of SSH private keys, cloud credentials (`.aws/`), or raw authorization tokens.
* **Continuous polling / aggressive scraping:** Probes are manual, event-driven, or cleanly scheduled.
* **Multi-tenant enterprise RBAC:** Phase 1 and 2 operate on a trusted operator model with optional shared API secret token access control.

## 7. Known gaps that still satisfy the requirements

Tracked in `progress.md`; they are honest limits, not violations:

* Some Windows-specific config layouts parse as `partial`, not `parsed`.
* Skills are detected by directory/`SKILL.md` presence; frontmatter is not parsed.
* Renaming a WSL distro in the inventory leaves the old runtime row behind.
* Claude's per-project MCP servers recorded in `~/.claude.json` are listed but
  not merged into the user-scope list.
* Task board dispatch requires POSIX-compliant streaming and cannot currently target Windows native PowerShell runtimes directly.

### 7.1 Operational isolation & security guarantees (Fixes #49–#53)

The following operational fixes are formally incorporated into the platform contract:
* **WebUI Token Injection (#49):** Unified Bearer token authorization across all WebUI API requests with graceful 401 interception and settings token management.
* **OpenSSH Alias Preservation (#50):** Preserves `~/.ssh/config` alias semantics (`IdentityFile`, `ProxyJump`, `Port`) while honoring explicit IP overrides via `-o HostName=<address>`.
* **Windows & WSL Runtime Topology (#51):** First-run inventory seeding retains Windows native transport contracts and preserves declared `wsl:` child runtimes.
* **Precision Runner Slot Matching (#52):** Single-slot runner stopping strictly targets the specified instance, restricting legacy un-suffixed fallbacks exclusively to slot 0.
* **Process Boundary Isolation (#53):** Enforces trailing-slash directory path boundaries (`$target_dir/`) to prevent cross-slot process termination (e.g., slot-1 vs slot-10).

## 8. Sources and change log

Requirement sources, in order of authority:

1. The hard constraints in §3 (product decisions; they override features).
2. Real-machine findings in `research.md` §2-§5 - what each agent writes to
   disk, which shells are available, and which remote mechanisms survive
   quoting, encoding and length limits.
3. The implementation and its tests (`backend/`, `tests/`), which record the
   behavior that was actually verified.

| Date | Change | Reason |
| --- | --- | --- |
| 2026-10-29 | Reconciled operational boundary (dual nature: read-only discovery + controlled operations), added Trust Boundary, Platform × Agent × Capability Matrix, and operational requirements (UPG-1, RUN-1, TASK-1, OPS-3). | Issue #63: align product specifications with operational capabilities. |
| 2026-10-18 | Scope discovery and precedence (CFG-1..CFG-4), comparison (CMP-1..CMP-2), scheduling (SCAN-3..SCAN-4), export (EXP-1). | Stage 8; `resolution` added so "declared" and "effective" are no longer conflated. |
| 2026-10-18 | This document created. | The project had research, architecture and progress notes but no requirements source. |
