# Agent Fleet Explorer

Read-only control plane for the AI coding agents installed across your
machines. It answers one question per machine: **which agents are installed,
and what are they configured to use?**

Phase 1 is discovery, collection and display only. It never modifies a remote
machine, never installs anything, and never executes a command that comes from
the web UI.

Supported agents: **Codex CLI, Claude Code, OpenCode, Pi, Hermes**.
Supported targets: **macOS, Linux, Windows (PowerShell) and WSL distros**,
reached over your existing SSH configuration.

## Quick start

    # backend
    cd backend
    uv venv ../.venv --python 3.13
    uv pip install --python ../.venv/bin/python -r requirements.txt
    cd ..
    .venv/bin/python -m uvicorn agent_fleet.main:app --host 127.0.0.1 --port 8000

    # frontend (development, proxies /api to :8000)
    cd web && npm install && npm run dev      # http://localhost:5173

    # or serve the built UI from the backend itself
    cd web && npm run build                   # then open http://127.0.0.1:8000

First run:

    .venv/bin/python -m agent_fleet.cli sync        # load inventory
    .venv/bin/python -m agent_fleet.cli runtimes    # list what was found
    .venv/bin/python -m agent_fleet.cli scan --host <name>   # probe one machine

Then open the UI and press **Scan all**.

## Where the machine list comes from

Three sources, merged by host name (later sources only fill gaps):

1. **Ansible inventory** - YAML (`all.children.<group>.hosts`) or INI,
   including `ansible_host`/`ansible_user`/`ansible_port`.
2. **OpenSSH client config** - every `Host` alias in `~/.ssh/config`
   (ProxyJump, ports, Tailscale names, per-host users all keep working).
3. **Project inventory** (`inventory.yaml`) - only for what SSH config cannot
   express: runtime kind hints and WSL distro names.

To add an Ansible inventory:

    AGENT_FLEET_INVENTORY_PATHS='["/path/to/ansible/inventory"]' .venv/bin/python -m agent_fleet.cli sync

Example:

    AGENT_FLEET_INVENTORY_PATHS='["../homelab/ansible/inventory"]'

Nothing is duplicated: if a host appears in several sources it is stored once.

## Views

* **Fleet overview** - totals, reachable/unreachable, per-agent distribution,
  last successful scan, and the runtime table (click a row to inspect it,
  scan or disable a single machine).
* **Matrix** - agents x machines; every cell distinguishes installed / absent /
  config-only / scan-failed / not-scanned. Click a cell to open its config.
* **Inspector** - version, exec path, install method, model, provider, masked
  base URL, MCP servers, skills, config scopes (system / user / project with
  their state), unsupported fields, warnings, and a sanitized JSON view.
* **Compare** - the same agent on two machines side by side, or two snapshots
  of one runtime, as a field-level change list.

## Scanning

* Manual: **Scan all**, or **Scan** on a single machine row.
* Scheduled: the Schedule button turns on periodic fleet scans (10 minutes to
  24 hours). State survives a restart; a run that comes due while another run
  is active is skipped rather than queued.
* Concurrency is bounded (default 4, hard ceiling 8 concurrent SSH sessions)
  and every runtime has its own timeout, so one dead host cannot stall a run.
* Progress is visible while a run is in flight.
* A failed scan keeps the previous data: the machine keeps showing its last
  known state and is flagged `stale` after `AGENT_FLEET_SCAN_TTL_SECONDS`
  (6h by default).
* Every successful collection also writes a historical snapshot, and any two
  snapshots (or two installations of the same agent) can be compared.

## CLI

    .venv/bin/python -m agent_fleet.cli sync         # reload inventory
    .venv/bin/python -m agent_fleet.cli runtimes     # list runtimes
    .venv/bin/python -m agent_fleet.cli probe <host> # one runtime, full JSON
    .venv/bin/python -m agent_fleet.cli scan         # scan every enabled runtime
    .venv/bin/python -m agent_fleet.cli scan --host <name>

## Security

This is a read-only observer.

* Only whitelisted, known config paths are read; `.ssh/`, private keys,
  `.aws/credentials`, `.npmrc`, `.env`, keychain files and similar are on a
  hard deny list in the probe generator.
* `~/.pi/agent/auth.json` and `~/.hermes/.env` are never requested.
* Output is sanitized three ways: field whitelists, recursive dropping of
  credential-named keys, and redaction of credential-looking values.
* URLs keep scheme + host + port + path; query strings, embedded credentials
  and secret-looking path segments are stripped.
* Environment blocks are reported as key names only.
* Raw remote config lives in memory during a scan only; it is never written to
  disk. The database stores the sanitized structure.
* Configurations are stored as **declared** (as written in the file). The tool
  does not claim to know the effective value, because env vars, project-level
  config and CLI flags can all override it.

## Configuration (env vars)

| Variable | Default | Meaning |
| --- | --- | --- |
| `AGENT_FLEET_DB_PATH` | `./data/fleet.db` | SQLite location |
| `AGENT_FLEET_INVENTORY_PATHS` | `["./inventory.yaml"]` | extra inventory files/dirs |
| `AGENT_FLEET_SSH_CONFIG_PATH` | `~/.ssh/config` | ssh config to read |
| `AGENT_FLEET_SCAN_CONCURRENCY` | `4` | concurrent runtimes per run |
| `AGENT_FLEET_COMMAND_TIMEOUT` | `25` | per-runtime probe timeout (s) |
| `AGENT_FLEET_SSH_CONNECT_TIMEOUT` | `10` | ssh connect timeout (s) |
| `AGENT_FLEET_SCAN_TTL_SECONDS` | `21600` | snapshot staleness threshold |
| `AGENT_FLEET_INCLUDE_LOCAL_RUNTIME` | `true` | also probe the controller machine |

## Tests

    .venv/bin/python -m pytest tests/ -q

92 tests: adapters against fixtures (installed / absent / config-only /
missing / malformed / unreadable), the sanitizer, the probe protocol,
all three inventory formats, timeout and connection-failure handling,
concurrency, snapshot retention, diff, scope precedence, scheduler and
export, plus the HTTP API.

## Repo layout

    backend/agent_fleet/   inventory, remotes (ssh/wsl/powershell), adapters, services, api
    web/src/               overview, matrix, inspector
    tests/fixtures/        real-shaped agent configs including fake credentials
    docs/                  research, architecture, progress
    inventory.yaml         your fleet (kind hints + WSL distros)

## Adding an agent

1. Add `backend/agent_fleet/adapters/<agent>.py` implementing
   `probe_spec`, `detect`, `discover_config`, `parse_config`.
2. Register it in `adapters/registry.py`.
3. Add a fixture plus a test that asserts both the parsed fields and the
   absence of credentials.

The adapter only ever sees `RemoteFacts`, so it is testable without a machine.

## Known limitations

See `docs/progress.md`.
