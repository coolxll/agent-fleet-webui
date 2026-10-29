# Agent Fleet Explorer

<p align="center">
  <b>English</b> •
  <a href="README_ZH.md">简体中文</a>
</p>

Control plane for the AI coding agents installed across your machines:
inspect installations and configuration, manage work on a task board, coordinate
declarative runner fleets, and observe daemon services across the fleet.

The system operates on an explicit **operational dual nature**:
- **Read-only Discovery & Observability:** routine fleet discovery, configuration
  inspection, version tracking, and quota monitoring are strictly non-invasive
  and side-effect-free. Probes never alter files or system state.
- **Controlled Remote Write Operations:** explicit, user-initiated actions—such
  as one-click agent upgrades, GitHub Actions runner fleet lifecycle management,
  and task board dispatch/handoff—execute over authorized OpenSSH sessions under
  strict safety whitelists and parameterless command synthesis.

Supported agents: Codex CLI, Claude Code, OpenCode, Pi, Hermes,
Antigravity (AGY), CodeBuddy, and Qoder (QoderCN).
Supported targets: macOS, Linux, Windows (PowerShell) and WSL distros,
reached through your existing SSH configuration. The machine list itself comes
only from inventory files; `~/.ssh/config` is not imported automatically.

Requirements: Python 3.11+ (Python 3.13 is tested and recommended), Node 20+, and the `ssh` client on the controller.

## Quick start

### One-command startup (Recommended)

From the repository root:

```bash
# Production mode: automatically builds frontend if needed, syncs venv, and starts backend on http://localhost:8000
./start.sh

# Or start and automatically open your default browser:
./start.sh --open

# Full dev mode: runs Vite hot-reloading frontend (:5173) + FastAPI backend (:8000) concurrently
./dev.sh
```

Common `./start.sh` options:
* `--port <num>`: custom backend port (default: 8000).
* `--host <ip>`: bind address (default: 127.0.0.1).
* `--frontend-port <num>`: Vite dev server port (dev mode, default: 5173 or next free).
* `--frontend-host <ip>`: Vite bind address (dev mode, default: the Tailscale IP, or `127.0.0.1` when Tailscale is absent; `0.0.0.0` also exposes it to the LAN).
* `--build`: force rebuild the frontend bundle before serving.
* `--open`: launch browser automatically once ready.
* `--dev`: run full development mode with hot reload.

In dev mode the Vite server binds the **Tailscale interface** by default, so the UI
is reachable from the tailnet — `http://<tailscale-ip>:5173` or
`http://<magicdns-name>:5173` — while staying off the LAN. Without Tailscale it
falls back to `127.0.0.1`. `/api` requests are proxied server-side, so the backend
stays on `127.0.0.1` and is never exposed directly.

Note: By default, the API operates in loopback development mode. For external exposure, configure `AGENT_FLEET_API_SECRET` in `.env` for bearer token authentication.

---

### Quick Start with Docker

Deploy with Docker Compose in a single command:

```bash
docker compose up -d
```

The WebUI will be accessible at `http://localhost:8000`. The container:
- Unifies the pre-built React frontend and FastAPI backend into a single service.
- Mounts `./data` to `/app/data` to persist the SQLite database and state.
- Mounts `~/.ssh` as read-only (`/root/.ssh:ro`) so the controller can reach remote fleet nodes via host SSH credentials.

---

### Manual step-by-step setup

The backend and CLI import the `agent_fleet` package, so run them from
`backend/` (or from the repo root with `PYTHONPATH=backend` in front).

    # from the repo root
    cd backend
    uv venv ../.venv --python 3.13   # or: python3 -m venv ../.venv (Python 3.11+)
    uv pip install --python ../.venv/bin/python -r requirements.txt

    # backend (proxied by the dev UI; serves the built UI when web/dist exists)
    ../.venv/bin/python -m uvicorn agent_fleet.main:app --host 127.0.0.1 --port 8000
    # (or, from the repo root: PYTHONPATH=backend .venv/bin/python -m uvicorn agent_fleet.main:app)

    # frontend development, or build it and open http://127.0.0.1:8000
    cd ../web
    npm install
    npm run dev        # http://localhost:5173 local-only; set FRONTEND_HOST=<ip> to expose
    npm run build      # writes web/dist, served by the backend

When starting via `./start.sh` or launching the backend service, initialization (database setup, inventory migration, and local controller discovery) happens automatically. Open the WebUI at `http://127.0.0.1:8000` and click **Scan all** to trigger fleet discovery.

Optional: the CLI is also available for headless management (from `backend/`):

    ../.venv/bin/python -m agent_fleet.cli sync              # reload inventory
    ../.venv/bin/python -m agent_fleet.cli runtimes          # list what was found
    ../.venv/bin/python -m agent_fleet.cli scan --host <name> # probe one machine

## Where the machine list comes from

The fleet is **exactly what the inventory files declare**. `~/.ssh/config` is
not read automatically, so an SSH alias such as `kindle` does not create a
host row on its own. It is still used by `ssh` for the inventory hosts that
reference it (ProxyJump, ports, Tailscale names, per-host users keep working).

Every path in `AGENT_FLEET_INVENTORY_PATHS` is read in order. Each path may be
an Ansible inventory (YAML `all.children.<group>.hosts`, or INI, including
`ansible_host`/`ansible_user`/`ansible_port`) or the project YAML format. The
default is the repo's `inventory.yaml`, which declares runtime kind hints,
WSL distro names and optional tags.

Merging fills gaps per field: `hostname`, `user`, `port`, `group`, tags, WSL
declarations and a non-`auto` kind hint; it never overwrites a value an
earlier source already provided. An optional `exclude:` list is still honored
if an inventory deliberately wants to drop a name.

To add an Ansible inventory (run from `backend/`):

    AGENT_FLEET_INVENTORY_PATHS='["/path/to/inventory.yaml","../inventory.yaml"]' \
      ../.venv/bin/python -m agent_fleet.cli sync

Setting the variable replaces the default list, so include `../inventory.yaml`
(as above, relative to `backend/`) if you still want its kind hints and WSL
declarations. Relative entries resolve against the current working directory.

Nothing is duplicated: if a host appears in several sources it is stored once.

## Views

* **Fleet overview** - totals, reachable/unreachable, per-agent distribution,
  last successful scan, and the runtime table (click a row to inspect it,
  scan or disable a single machine).
* **Matrix** - agents x machines; every cell distinguishes installed / absent /
  config-only / scan-failed / not-scanned / unknown / error. Click a cell to
  open its config.
* **Inspector** - version, exec path, install method, model, provider, masked
  base URL, MCP servers, skills, config scopes (system / user / project with
  their state), `resolution` (effective vs declared), unsupported fields,
  warnings, and a sanitized JSON view.
* **Compare** - the same agent on two machines side by side, or two snapshots
  of one runtime, as a field-level change list.
* **Quotas & Auth Settings** - a standalone, account-based menu. You import an account
  credential (verified against the vendor's online API before it is saved),
  and the view shows the account, plan, weekly/5-hour bars, remaining
  percentage and reset countdown. Refreshing calls the online API in real time.
  The top **Auth Settings** button allows managing Antigravity OAuth client secrets
  directly in the UI, masked securely and persisted with 0600 file permissions.
  Accounts are grouped by Agent. Codex/ChatGPT, Antigravity, QoderCN,
  WorkBuddy/CodeBuddy and Trae CN are supported. Trae CN accepts a
  `Cloud-IDE-JWT` access token or JSON with `access_token` and optional
  `device_id`; it reads the online credit summary and package details.
  Antigravity OAuth credentials are imported once; the backend independently
  renews access tokens and reads Google quota pools. Accounts have no machine
  association.
* **AGY account quotas** - when a machine runs Antigravity with `agy-swap`, the
  agent drawer and the AGY fleet view also show the account registry (name,
  email, aliases, tier) and its Google quota pools: weekly and 5-hour bars,
  remaining percentage, reset countdown, and the lowest pool per account. The
  data is the last quota snapshot stored by `agy-swap` in
  `~/.gemini/agy-swap/accounts.json` (run `agy-swap limits --refresh` to update
  it). A normal scan only reads that snapshot; Agent Fleet never calls Google
  or writes to the remote machine.

### Read-only magpie quota collection

In **Quotas → Link magpie accounts**, select the Codex accounts to associate.
An existing import with the same account identity is reused, keeping its label
and last quota snapshot. Linking removes its copied credentials from our store;
only the magpie login reference is saved. Other accounts stay manually managed.
Removing a linked account removes only our record, leaving magpie untouched.

Each query reads magpie's current access token from `logins.json` and calls the
vendor usage endpoint. This integration never uses a refresh token, invokes
magpie, writes its files, or changes an Agent's login. A rejected access token
causes one re-read of the source; a retry happens only if magpie has changed the
token. Otherwise the account asks you to update its login in magpie. Failed
queries retain the last successful quota and its original collection time.

Background queries default to every 10 minutes, apply **only to linked Codex
accounts**, and can be paused or have their interval changed in Quotas. Settings
and per-account next-query times survive restarts. Failures back off up to an
hour (or the configured interval when longer); collection uses at most three
concurrent account queries. Manual and background requests for the same account
share in-flight work and a 30-second cooldown. The page polls saved snapshots;
opening it does not query vendors. Run one backend worker for collection.

The source defaults to `~/.config/magpie`. Set
`AGENT_FLEET_MAGPIE_CONFIG_DIR` when magpie uses another directory. This directory
is on the backend machine; remote account discovery is not included. Skills,
MCP configuration and token renewal remain managed by magpie.

## Task board and handoff

The **Task board** is for coding work; the header's **Jobs** drawer remains
for administrative background jobs such as upgrades. A task keeps its goal,
status, linked sessions and execution history across Agent changes.

Create a task, optionally link an indexed session, then select a target runtime,
Claude Code or Codex, and an existing absolute project directory. Preview the
handoff before starting it. Agent status comes from Claude's structured output
or Codex's app-server protocol; independently launched sessions may have unknown
live status. A completed Agent turn moves work toward review, not user acceptance.
When returning to an Agent on the same runtime and directory, automatic mode
tries its previous native session. If that session cannot be resumed, select
**New session**, preview again, and retry with the full available context.

Use one backend worker for managed execution. Its connections belong to that
process; shutdown cancels the executions it started, and restart marks unfinished
stages as interrupted instead of assuming they are still running.

Managed execution supports POSIX local and SSH runtimes. For a WSL target,
use its own SSH runtime; native Windows/PowerShell and nested `wsl.exe` execution
are not supported for managed handoff. Cross-machine handoff transfers context;
prepare the target repository and working directory separately.

## Daemons and supervisors

The **Daemons** view and machine details show cached service observations per
runtime for Paseo and Herdr: installation, daemon/server status, listen port or
Unix socket, CLI/server version, upgrade availability, workspaces and sessions.
Press **Refresh** to run read-only native CLI queries through the existing runners.
Opening the page does not connect to remote machines. Services are not modified,
started or stopped by these read-only observation checks.

Paseo supplies its own Agent IDs, and Herdr tracks workspaces, panes and recognized
coding agents. These observations retain their respective source tags (`paseo`, `herdr`).
Failed checks keep previously retrieved data and mark it stale. Authentication, stopped
servers and unsupported CLI schemas are shown as explicit statuses.

## Scanning

* Manual: **Scan all**, or **Scan** on a single machine row. Each row can also be
  disabled (stop scanning) or removed entirely (drop its collected data).
* Scheduled: the Schedule button turns on periodic fleet scans (10 minutes to
  24 hours). State survives a restart; a run that comes due while another run
  is active is skipped rather than queued.
* Concurrency is bounded (default 4, hard ceiling 8 concurrent SSH sessions)
  and every runtime has its own timeout, so one dead host cannot stall a run.
* Progress is visible while a run is in flight.
* A failed scan keeps the previous data: the machine keeps showing its last
  known state (with an error and `reachable: false`) and is flagged `stale`
  after `AGENT_FLEET_SCAN_TTL_SECONDS` (6h by default).
* Every successful collection also writes a historical snapshot (kept
  indefinitely), and any two snapshots (or two installations of the same
  agent) can be compared.

## GitHub Actions Runner Fleet (Declarative Management)

The fleet supports declarative, multi-machine self-hosted runner orchestration driven by `config/runner_fleet.yaml` and `scripts/manage_runner.sh`:

* **Outbound-only Security (Zero Inbound Port Exposure)**: Runners poll GitHub via HTTPS/WSS; machines need no public IPs and expose no open ports.
* **Shared Proxy Egress**: Centralizes proxy routing (e.g. `runner-node-01` Tailscale SOCKS5 `100.64.0.1:1080` or LAN HTTP `192.168.1.100:8118`) across all runner nodes.
* **Commands**:
  * `./scripts/manage_runner.sh overview`: Global cross-repository & cross-host status matrix.
  * `./scripts/manage_runner.sh sync`: Declaratively reconcile runners to match desired state.
  * `./scripts/manage_runner.sh prune`: One-click garbage collection of offline ghost runners.
  * Detailed architecture and setup: see [docs/runner-fleet-guide.md](docs/runner-fleet-guide.md).

## Platform × Agent × Capability Matrix

The support matrix across operating systems, AI coding agents, and system capabilities:

### Agent Capabilities

| Agent | Probe & Discovery | Quota Ingestion | Task Dispatch & Handoff | One-Click Upgrade |
| --- | --- | --- | --- | --- |
| **Claude Code** (`claude`) | Full | Unsupported (Direct console billing) | Full (POSIX local/SSH) | Full (`claude update` / `npm`) |
| **Codex CLI** (`codex`) | Full | Full (ChatGPT token / Magpie) | Full (POSIX local/SSH) | Full (`npm`) |
| **Antigravity** (`agy`) | Full | Full (Google Quota Pools) | Unsupported (Roadmap) | Full (`agy update`) |
| **OpenCode** (`opencode`) | Full | Unsupported (Multi-provider BYOK) | Unsupported (Roadmap) | Full (`opencode upgrade` / `npm`) |
| **Pi** (`pi`) | Full | Unsupported (Multi-provider BYOK) | Unsupported (Roadmap) | Full (`pi update` / `npm`) |
| **Hermes** (`hermes`) | Full | Unsupported (Multi-provider BYOK) | Unsupported (Roadmap) | Full (`hermes update` / install) |
| **CodeBuddy** (`codebuddy`) | Full | Full (WorkBuddy/CodeBuddy API) | Unsupported (Roadmap) | Full (`npm`) |
| **Qoder** (`qoder`) | Full | Full (QoderCN OpenAPI) | Unsupported (Roadmap) | Full (`qodercli update` / `npm`) |

### Platform Support

| Platform / Runtime | Probe & Discovery | Quota Collection | Task Board Dispatch | Remote Upgrade Execution | Runner Fleet Orchestration |
| --- | --- | --- | --- | --- | --- |
| **macOS (Darwin)** | Full (POSIX `sh`) | Full | Full (POSIX) | Full | Partial (Bash runner; launchd pending verification) |
| **Linux (Debian/Ubuntu/RHEL)** | Full (POSIX `sh`) | Full | Full (POSIX) | Full | Full (systemd / background daemon + proxy) |
| **Windows (Native PowerShell)** | Full (Chunked b64 PowerShell) | Full | Unsupported (Requires POSIX streaming) | Full (PowerShell) | Unsupported (Requires bash/Linux runner environment) |
| **WSL2 (Windows Subsystem)** | Full (`wsl.exe` / SSH) | Full | Full (via WSL SSH target) | Full (POSIX) | Partial (Runs inside Linux distro) |

> Detailed architectural contracts and design proposals: see [docs/requirements.md](docs/requirements.md) and [docs/adr/ADR-001-operation-modes.md](docs/adr/ADR-001-operation-modes.md).

## CLI

From `backend/` (or prefix any command with `PYTHONPATH=backend` at the repo
root):

    ../.venv/bin/python -m agent_fleet.cli sync         # reload inventory
    ../.venv/bin/python -m agent_fleet.cli runtimes     # list runtimes
    ../.venv/bin/python -m agent_fleet.cli probe <host> # one runtime, full JSON
    ../.venv/bin/python -m agent_fleet.cli scan         # scan every enabled runtime
    ../.venv/bin/python -m agent_fleet.cli scan --host <name>
    curl http://127.0.0.1:8000/api/export -o fleet.json   # sanitized export

## Security

Agent Fleet enforces a defense-in-depth architecture covering network access,
execution boundaries, authentication, and data sanitization:

### 1. Operational Dual Nature & Trust Boundaries
* **Read-Only Discovery by Default:** Fleet inventory scanning, configuration
  probing, version inspection, and daemon observation are strictly read-only.
  Probes run stateless scripts that never write to disk or alter environment variables.
* **Controlled Remote Operations:** Explicit actions (upgrades, runner lifecycle,
  task handoff) execute only upon explicit user invocation.
* **Target User Privilege Boundary:** Remote operations execute strictly under
  the permissions of the configured SSH user account on the target machine. Root or
  `sudo` privileges are never requested or assumed.
* **Zero Arbitrary Remote Execution:** The backend rejects arbitrary client-supplied
  shell commands. All operational scripts are synthesized on the server from strict
  whitelists (`UPGRADE_RULES`) and parameterless templates run through `PATH_BOOTSTRAP`
  with per-runtime concurrency locks.

### 2. API Authentication & Network Access Control
* **Loopback Development Mode:** By default, the API is unauthenticated for seamless
  local loopback development.
* **Bearer Token Authentication (`AGENT_FLEET_API_SECRET`):** When binding to shared
  networks or exposing the UI:
  * *Default (Anonymous Read-Only):* `AGENT_FLEET_ALLOW_ANONYMOUS_READONLY=true` allows
    unauthenticated reads while requiring Bearer authorization for write operations
    (sync, scan triggers, upgrades, runner actions).
  * *Strict Mode:* Setting `AGENT_FLEET_ALLOW_ANONYMOUS_READONLY=false` enforces Bearer
    token authentication across all API endpoints.
* **WebUI Bearer Injection (Fixes #49):** Frontend requests route through a unified `authFetch`
  layer that persists access tokens and handles authentication challenges.

### 3. Safe OpenSSH Practices & Process Isolation
* **Preserving OpenSSH Alias Semantics (Fixes #50):** The runner pipeline prioritizes configured
  SSH aliases as the destination operand while specifying `-o HostName=<address>`,
  guaranteeing that host-specific directives in `~/.ssh/config` (`IdentityFile`,
  `ProxyJump`, ports, users) are preserved without conflict.
* **Windows & WSL Runtime Topology (Fixes #51):** First-run inventory seeding retains Windows
  native transport contracts and preserves declared `wsl:` child runtimes.
* **Controller Public Key Diagnostics (Fixes #47):** The controller provides safe, read-only
  inspection of its local SSH public key (`GET /api/inventory/controller-ssh-key`)
  along with structured connectivity diagnostics and remediation suggestions for
  remote targets.
* **Process Slot & Path Isolation (Fixes #52, #53):** GitHub Actions runner management enforces
  exact slot name matching (`slot-0` vs legacy instances) and path-boundary
  isolated termination (`pkill -f "<path>/"`), preventing cross-slot or collateral
  process termination.
* **Outbound-Only Runners:** GitHub Actions runners communicate with GitHub
  exclusively via outbound HTTPS/WSS long polling. Nodes require zero inbound open ports.

### 4. Multi-Layer Sanitization & Secrecy
* **Hard Path Deny List:** The probe generator unconditionally refuses to inspect
  `.ssh/`, private keys, `.aws/credentials`, `.npmrc`, `.netrc`, `.kube/config`,
  `/etc/shadow`, keychain files, `~/.pi/agent/auth.json`, and `~/.hermes/.env`.
* **Three-Layer Sanitization (`sanitize.py`):**
  1. *Field Whitelisting:* Adapters emit only recognized fields; unknown keys are
     recorded by name with values discarded.
  2. *Recursive Name Dropping:* Keys matching credential patterns (`apiKey`, `token`,
     `password`, `Authorization`) are removed recursively at any nesting level.
  3. *Pattern Redaction:* Values resembling secrets (`sk-...`, `ghp_...`, JWTs,
     `Bearer <token>`) are replaced with masked placeholders.
* **URL & Environment Scrubbing:** URLs keep only scheme, host, port, and path;
  query parameters and secrets are stripped. Environment blocks list variable
  names only, never values.
* **Memory-Only Raw Config:** Raw remote configuration streams exist only in memory
  during active parsing and are never persisted to disk or SQLite.

### 5. Credential Store Isolation
* **Quota Account Store:** Imported quota credentials live in `data/quota_accounts.json`
  with restricted mode `0600`, outside SQLite. The API never outputs raw credentials.
* **Antigravity OAuth Secrets:** Client secrets are stored in `data/antigravity_clients.json`
  (mode `0600`) or via `AGENT_FLEET_ANTIGRAVITY_CLIENTS`, masked in API responses
  and UI views.
* **AGY Swap Whitelist:** Host quota snapshots read from `~/.gemini/agy-swap/accounts.json`
  copy only whitelisted quota metrics (tier, bucket limits, reset times), dropping all
  token hashes and secret references.

## Configuration (.env and environment variables)

Agent Fleet supports out-of-the-box configuration using a `.env` file in the repository root or via standard environment variables. Copy the example file to get started:

```bash
cp .env.example .env
```

Settings defined in `.env` are automatically loaded on startup (prefixed with `AGENT_FLEET_`). Shell environment variables take precedence over values defined in `.env`.

| Variable | Default | Meaning |
| --- | --- | --- |
| `AGENT_FLEET_HOST` | `127.0.0.1` | server bind host address |
| `AGENT_FLEET_PORT` | `8000` | server HTTP port |
| `AGENT_FLEET_DATA_DIR` | `<repo>/data` | runtime data directory (database, quota tokens, logs) |
| `AGENT_FLEET_DB_PATH` | `<repo>/data/fleet.db` | SQLite database file location |
| `AGENT_FLEET_INVENTORY_PATHS` | `["<repo>/inventory.yaml"]` | inventory files/dirs (comma-separated or JSON list) |
| `AGENT_FLEET_API_SECRET` | `None` | optional bearer secret/token for API access control |
| `AGENT_FLEET_ALLOW_ANONYMOUS_READONLY` | `true` | allow unauthenticated read requests when `API_SECRET` is set |
| `AGENT_FLEET_AUTO_SCAN_ENABLED` | `false` | automatically start scheduled scanning loop on startup |
| `AGENT_FLEET_AUTO_SCAN_INTERVAL_MINUTES` | `60` | interval in minutes between automated scans |
| `AGENT_FLEET_SCAN_CONCURRENCY` | `4` | concurrent runtimes per run (hard ceiling 8) |
| `AGENT_FLEET_COMMAND_TIMEOUT` | `25` | per-probe timeout (s); Windows chunks share it |
| `AGENT_FLEET_SSH_CONNECT_TIMEOUT` | `10` | ssh connect timeout (s) |
| `AGENT_FLEET_SCAN_TTL_SECONDS` | `21600` | snapshot staleness threshold (6 hours) |
| `AGENT_FLEET_INCLUDE_LOCAL_RUNTIME` | `true` | also probe the controller machine |
| `AGENT_FLEET_LOCAL_HOST_LABEL` | `local-controller` | host name that maps to the local runner |

`AGENT_FLEET_API_PREFIX`, `AGENT_FLEET_PERSIST_RAW_CONFIG` and
`AGENT_FLEET_WORKSPACE_ROOT` are defined in `settings.py` but not wired up
yet: the API prefix is hardcoded to `/api`, and raw config is never persisted
regardless of the flag.

## Trae credential upkeep

The `trae` quota account holds a **manual, static** access token: `trae_quota.py`
only reads quota and never renews a login, so the row turns red with
`Trae login expired or was rejected` about two weeks after import (Trae CN
access tokens live ~14 days).

`trae2api-web` on `runner-node-01` renews the same account via refresh_token and
writes the fresh token back to its auths file. `scripts/sync_trae_credential.py`
mirrors that token into agent-fleet (upsert through `POST /api/quota-accounts`,
which re-verifies the credential before saving):

    # reads runner-node-01:<TRAE_AUTH_PATH> over ssh, updates the local account
    .venv/bin/python scripts/sync_trae_credential.py

Silent and exit 0 when there is nothing to do; exit 1 when the upstream token is
already expired (trae2api renewal stalled), 2 on ssh/transport failure, 3 when
agent-fleet rejects the credential. Runs every 6h via the Hermes cron job
`Trae 凭据同步到 agent-fleet` (shim: `~/.hermes/scripts/trae_credential_sync.py`).

Overrides: `TRAE_AUTH_SSH`, `TRAE_AUTH_PATH`, `AGENT_FLEET_API`,
`TRAE_SYNC_STATE`, `TRAE_SYNC_MIN_HOURS`.

## Tests

    # Fast multi-worker execution via pytest-xdist:
    .venv/bin/python -m pytest tests/ -n auto --dist=loadscope

426+ tests: adapters against fixtures (installed / absent / config-only /
missing / malformed / unreadable), the sanitizer, the probe protocol,
all three inventory formats, timeout and connection-failure handling,
concurrency, last-known-good retention, diff, scope precedence, scheduler and
export, quota authentication settings, runner fleet management, plus the HTTP API.

Run from the repo root, as shown above.

## Architecture & Domain Models

Detailed architecture, entity relationship modeling, and design contracts:
* **[System Architecture](docs/architecture.md)**: Process model, remote probe execution protocol, and failure isolation.
* **[Domain Models & E-R Diagram](docs/domain-models.md)**: Complete Mermaid E-R entity model covering all 11 SQLite tables, data dictionary, lifecycle state machines, and five extensibility contracts.
* **[Runner Fleet Operations Guide](docs/runner-fleet-guide.md)**: Multi-host GitHub Actions runner orchestration, proxy sharing, and pool reconciliation.
* **[Documentation Hub](docs/README.md)**: Full index of research, requirements, and specifications.

## Repo layout

    backend/agent_fleet/   inventory, remotes (ssh/wsl/powershell), adapters, services, api
    web/src/               overview, matrix, inspector, compare, quotas, tasks, daemons
    config/                declarative configuration (runner_fleet.yaml)
    scripts/               maintenance & fleet tools (manage_runner.sh, sync_trae_credential.py)
    tests/fixtures/        real-shaped agent configs including fake credentials
    docs/                  architecture, domain-models, requirements, runner-fleet-guide
    inventory.yaml         your fleet (kind hints + WSL distros)

## Adding an agent

1. Add `backend/agent_fleet/adapters/<agent>.py` implementing
   `probe_spec`, `detect`, `discover_config`, `parse_config`.
2. Register it in `adapters/registry.py`.
3. Add a fixture plus a test that asserts both the parsed fields and the
   absence of credentials.

The adapter only ever sees `RemoteFacts`, so it is testable without a machine.

## Known limitations

* Config is stored as **declared** unless `resolution` says `effective`; even
  then, environment variables and CLI flags are not visible to the probe and
  can still override what is shown (see Security above).
* Precedence merging is per agent, not uniform: Claude merges system < user <
  local < project settings plus project `.mcp.json`; OpenCode parses the single
  highest-precedence readable document; Codex, Pi and Hermes normalize the
  user-scope file and report the system policy as a scope without applying it.
  Claude's per-project MCP servers recorded in `~/.claude.json` are listed
  separately, with a warning saying so.
* Windows layout variants may show `partial` instead of `parsed`.
* Skills are counted and listed by directory/`SKILL.md` presence; frontmatter
  is not parsed.
* Hosts you do not want in the fleet can be disabled or removed at the host
  level; disabled hosts keep their history but disappear from every view until
  you turn them back on. Inventory files are the only source of new hosts.
* Snapshot history is kept indefinitely; there is no pruning.
* The API has no authentication and is meant for loopback use.

See `docs/progress.md` and `docs/requirements.md`.
