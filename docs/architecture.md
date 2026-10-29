# Architecture

Agent Fleet Explorer is a dual-mode control plane over heterogeneous development
machines reached via standard OpenSSH. It balances two fundamental principles:

1. **Read-only Discovery & Observability:** routine fleet discovery, configuration
   inspection, version tracking, and quota monitoring are strictly non-invasive
   and side-effect-free. Probes never alter files or system state.
2. **Controlled Remote Write Operations:** explicit, user-initiated operational
   actions:
   - **One-click Agent Upgrades:** binary/package upgrades across runtimes via
     server-synthesized whitelist commands (`action_service.py`).
   - **Runner Fleet Lifecycle:** declarative management and process supervision
     of GitHub Actions self-hosted runner slots (`scripts/manage_runner.sh`).
   - **Task Board Dispatch & Handoff:** dispatching coding tasks to remote POSIX
     runtimes and orchestrating cross-agent session transitions (`task_service.py`).

Requirements it implements: `requirements.md`. Architectural decision records:
`docs/adr/`.

## Shape

    React + TS (Vite)  --->  FastAPI (single process)  --->  ssh(1)  --->  hosts
                                     |
                                     +--> SQLite (fleet.db)

One backend process. No queue, no workers, no microservices. Scans and
operational actions run as bounded asyncio tasks inside the API process.

## Collection model

The central idea is **one main round trip per runtime**, plus one small second
pass when project scopes exist.

1. The backend renders a read-only shell script from the union of everything
   every adapter wants to know (binaries, user/system config paths, skills
   directories).
2. The script is streamed to `sh -s` over one `ssh` connection (or to
   `wsl.exe -d <distro> -- sh -s`, or to `powershell.exe -EncodedCommand ...`
   in chunks on Windows).
3. The script replies with a framed record stream, `AF<TAB>tag<TAB>fields...`,
   including whole file contents for whitelisted config paths.
4. The reply is parsed into `RemoteFacts` - a plain data object.
5. If an adapter derives project roots from the user-scope config (Claude's
   projects table, Codex's `[projects]`, OpenCode's declared workspace roots),
   a second files-only pass fetches project-scope files. It is capped at 30
   unique files and at 5 roots per agent and reuses the same connection code.
   If that pass fails, the scan still succeeds, project values are missing
   and those scopes are reported `not_probed` (see "Declared vs effective").
   Windows chunks run one after another within a single shared timeout
   budget, so a runtime never opens more than one SSH connection at a time.

Because adapters only ever see `RemoteFacts`, they are pure functions and can
be tested from fixtures with no machine and no network.

## Remote record stream

| Tag | Payload |
| --- | --- |
| `os arch kernel home user osrelease macosver haswsl` | environment facts |
| `probed <key>` | "we looked for this binary" (distinguishes absent from never-checked) |
| `which <key> <path>` | resolved executable path |
| `version <key> <exit> <output>` | real exit code plus the version output |
| `file <path> <size> <state>` ... `endfile <path>` | file presence and content |
| `b64` variant | same, with base64 content (used by the PowerShell probe) |
| `dir <path> <state>` / `ls <path> <name> <kind>` | directory listings |
| `skilldir <path>` / `skill <path> <name> <file>` | skills directories |

## Adapters

    AgentAdapter
      probe_spec()     what to collect remotely
      detect()         installed / absent / unknown, version, path, install method
      discover_config()  which files exist and are readable
      parse_config()   normalized + whitelisted configuration
      sanitize()       recursive scrub

`ParsedConfig.quota` is an optional, adapter-owned account-quota snapshot. The
AGY adapter fills it from `agy-swap`'s local account registry (email, name,
aliases, tier, weekly/5-hour buckets and reset times). It is an explicit
whitelist assembled by the adapter rather than a view over the raw file; the
registry's credential references are never copied. Quota files are auxiliary
sources: an unreadable one does not lower the configuration `resolution`.

Every adapter result passes through one central `scrub_config()` before it can
be stored, so a future adapter cannot leak by forgetting to sanitize.

## Data model & Domain Architecture (SQLite)

> Detailed E-R diagram, entity dictionary, and extensibility contracts are documented in [domain-models.md](domain-models.md).

The database (`data/fleet.db`) acts as the single source of truth for:
- **Managed hosts & runner fleets** (`hosts` 1---n `runtimes`);
- **Agent detection & sanitized configurations** (`agent_installations`, `config_sources`, `agent_configurations`);
- **Interactive sessions & sanitized transcripts** (`sessions` 1---1 `session_transcripts`);
- **Workflow tasks & multi-agent execution stages** (`fleet_tasks` 1---n `task_stages`);
- **Daemon observers** (`herdr_observations`, `paseo_observations`);
- **Scan history & snapshots** (`scan_runs` 1---n `scan_tasks`, `snapshots`).

```
hosts 1---n runtimes 1---n agent_installations 1---n config_sources
               |              1---1 agent_configurations
               +---n sessions 1---1 session_transcripts
               +---n snapshots (history, kept indefinitely)
               +---1 herdr_observations / paseo_observations
fleet_tasks 1---n task_stages (concurrency mutex & cross-agent handoffs)
scan_runs 1---n scan_tasks
app_state (schedule & runtime metadata key/value)
```

**Identity of an install is (host, runtime, user, agent).** A Windows machine
has a `native` runtime plus one runtime per WSL distro; each has its own home
directory, its own user and its own agents. They are never merged.

Statuses are kept separate on purpose:

* `installed` - binary found
* `not_installed` - looked, binary absent, no config either
* `config_only` - binary gone, config still on disk
* `not_scanned` - never probed
* `unknown` - probed but the answer is inconclusive
* `scan_failed` - connectivity failed; previous data is retained

## Failure handling

* Per-runtime timeout and per-connection timeout.
* A bounded worker pool (default 4, hard ceiling 8 concurrent SSH sessions)
  means one hung host cannot stall the fleet.
* A failed scan updates connectivity fields and leaves the last known good
  snapshot untouched, so offline machines keep showing their last state with a
  `stale` marker (after the TTL plus a 60s grace).
* When a scan produces no usable config (`empty`/`error`/`unsupported`), the
  previous configuration row is retained and `config_status` keeps describing
  it, so a broken parse never blanks the inspector.

## Operational execution pipeline

While observation scans are read-only, Agent Fleet provides three explicit,
controlled remote write capabilities:

### 1. One-click Agent Upgrades (`action_service.py`)
- **Strict command synthesis:** Zero client-supplied command strings are accepted.
  Upgrade commands are generated entirely server-side from `UPGRADE_RULES` based
  on agent identifier, target platform (POSIX vs Windows PowerShell), and channel
  (`latest` vs `stable`).
- **Official updater precedence:** For agents with native self-updaters
  (e.g. `claude update`, `opencode upgrade`, `pi update`, `qodercli update`, `agy update`),
  the official command is attempted first before falling back to package managers (`npm`).
- **Execution safety:** Commands run under `PATH_BOOTSTRAP` (or `WIN_PATH_BOOTSTRAP`)
  to ensure correct binary resolution without polluting system environments.
- **Concurrency & isolation:** Protected by per-runtime `asyncio.Lock` to prevent
  conflicting concurrent updates on the same node.
- **Observability:** Supports both synchronous execution and background task
  queues with polling (`GET /api/upgrades/{task_id}`). On completion, an automated
  post-upgrade probe is dispatched to refresh state.

### 2. Declarative Runner Fleet Lifecycle (`manage_runner.sh`)
- **Declarative topology:** Self-hosted GitHub Actions runners are defined in
  `config/runner_fleet.yaml`, declaring host endpoints, slot counts, proxies, and labels.
- **Outbound-only networking:** Runners register with GitHub via temporary JIT
  tokens and poll over HTTPS/WSS. Target hosts require **zero inbound open ports**.
- **Shared proxy egress:** Enables multi-node clusters to share a centralized
  Tailscale SOCKS5 or LAN HTTP proxy for outbound GitHub communication.
- **Process group isolation:** Stop and prune routines use exact slot matching
  (distinguishing `slot-0` from legacy instances) and enforce trailing-slash
  directory boundaries in `pkill` to prevent collateral process kills.

### 3. Task Board Dispatch & Handoff (`task_service.py`)
- **Task & Stage orchestration:** Dispatches coding tasks to remote POSIX runtimes
  hosting Claude Code or Codex.
- **Working directory verification:** Ensures the target repository exists, checks
  Git branch and status (`rev-parse HEAD`, `git status --short`), and verifies
  preconditions before handoff.
- **Session lineage:** Recovers previous native sessions when resuming on the
  same host and directory, or initializes a clean session with full context handoff.
- **Supervision:** Monitored via CLI streaming protocols with cancellation on
  process shutdown and state tracking recorded in SQLite (`fleet_tasks` and `task_stages`).

## Security model and trust boundaries

The system implements defense-in-depth across the network, authentication,
remote execution, and data layers:

### Trust Boundaries & Network Privileges
1. **OpenSSH Credential Boundary:** The controller relies exclusively on existing
   local OpenSSH credentials (`~/.ssh/config`, ssh-agent, or identity keys) to
   authenticate with remote nodes. The application never stores, prompts for, or
   duplicates private keys or passwords.
2. **User Privilege Boundary:** All remote operations execute strictly within the
   permissions of the configured SSH user account on the target machine. Root or
   `sudo` privileges are never assumed or required.
3. **OpenSSH Alias Preservation:** Network targets preserve configured SSH aliases
   as destination operands while supplying `-o HostName=<address>`, ensuring host-specific
   directives (`IdentityFile`, `ProxyJump`, `CertificateFile`) in `~/.ssh/config`
   remain fully effective.
4. **API Access Control:** Configurable via `AGENT_FLEET_API_SECRET`:
   - *Default (Anonymous Read-Only):* Read-only routes are unauthenticated for seamless
     local loopback developer workflows, while write operations (upgrades, syncs,
     task dispatch) require Bearer authentication.
   - *Strict Mode (`AGENT_FLEET_ALLOW_ANONYMOUS_READONLY=false`):* Enforces Bearer
     token authentication across all API endpoints for exposed or shared network
     deployments.
   - *WebUI Bearer Injection:* Frontend requests route through a unified `authFetch`
     layer with local token persistence and automatic 401 handling.

### Remote Execution Safety
1. **Zero Untyped Remote Execution:** The API provides no endpoint for executing
   arbitrary shell commands.
2. **Parameterless Synthesis:** Operational scripts use fixed templates and server-controlled
   identifiers.
3. **Process Slot Isolation:** Runner management bounds process signals strictly to
   the configured instance path, eliminating cross-slot process leakage.

### Data Sanitization (Three Layers in `sanitize.py`)
1. **Field Whitelist:** Adapters emit known fields only. Unknown keys are
   reported by name and their values are dropped.
2. **Name-based Dropping:** Any key whose name looks credential-ish
   (`apiKey`, `token`, `password`, `Authorization`, ...) is removed
   recursively at any nesting depth.
3. **Value-based Redaction:** Values matching credential patterns (`sk-...`,
   `ghp_...`, JWTs, `Bearer <token>`) are replaced with masked placeholders.
4. **Memory-Only Raw Bytes:** Raw remote configuration streams exist only in
   memory during a scan and are never written to disk or database. Only the
   sanitized structure reaches SQLite.
5. **Credential Store Separation:** Imported quota credentials live in
   `data/quota_accounts.json` with mode `0600`, outside SQLite; the API never
   returns raw credentials. Antigravity OAuth client secrets are similarly isolated.

### Declared vs effective

Every stored configuration carries `resolution`, set by `apply_scope()` from
the agent's precedence chain:

* `effective` - every file in the chain was readable or definitively absent,
  so the normalized values reflect every declared scope (Claude merges system
  < user < local < project; OpenCode parses the highest-precedence readable
  document).
* `declared` - at least one existing file was unreadable, oversized,
  truncated or malformed; a warning names it, and the values must be read as
  "as written in the files that could be read". This is the honest answer
  when a scope could not be inspected. A file that is entirely absent from
  the probe output (`not_probed`, for example when the project-scope pass
  failed) is not part of the check; those scopes show `not_probed`.

The stored values are always declarations from files. Environment variables
and CLI flags are invisible to the probe, so even `effective` never means
"what the agent will actually use at runtime". Codex, Pi and Hermes normalize
the user-scope file and report the system policy as a scope without merging
it; their `resolution` still only says whether every declared scope file was
readable.

## Comparison, export and scheduling

* `services/diff.py` produces field-level change records (`path`, `kind`,
  `a`, `b`) for two installations or two snapshots; the UI's Compare tab
  renders them.
* `services/export.py` builds one sanitized `agent-fleet-export/1` document
  and exposes it at `GET /api/export`.
* `services/scheduler.py` runs one loop inside the API process; its state is
  persisted in `app_state` and a due run is skipped while another is active.

## Layout

    backend/agent_fleet/
      inventory/      Ansible (YAML+INI) and project YAML; no auto ssh-config import
      remotes/
        executor.py   Local / SSH / Windows / WSL runners
        probe.py      POSIX probe script + record parser
        probe_win.py  PowerShell probe (chunked, base64)
      adapters/       sanitize, base, and one module per agent
      services/       scanner, persistence, scan runner, inventory sync,
                      diff, export, scheduler
      api/            HTTP routes
    web/src/          overview, matrix, inspector, compare (Diff.tsx)
    tests/            fixtures + unit/integration tests
