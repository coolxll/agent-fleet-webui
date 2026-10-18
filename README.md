# Agent Fleet Explorer

Read-only control plane for the AI coding agents installed across your
machines. It answers one question per machine: **which agents are installed,
and what are they configured to use?**

Phase 1 is discovery, collection and display only. It never modifies a remote
machine, never installs anything, and never executes a command that comes from
the web UI. The requirements it satisfies (and the verification behind each)
are in `docs/requirements.md`.

Supported agents: **Codex CLI, Claude Code, OpenCode, Pi, Hermes**.
Supported targets: **macOS, Linux, Windows (PowerShell) and WSL distros**,
reached over your existing SSH configuration.

Requirements: Python 3.13, Node 20+, and the `ssh` client on the controller.

## Quick start

The backend and CLI import the `agent_fleet` package, so run them from
`backend/` (or from the repo root with `PYTHONPATH=backend` in front).

    # from the repo root
    cd backend
    uv venv ../.venv --python 3.13
    uv pip install --python ../.venv/bin/python -r requirements.txt

    # backend (proxied by the dev UI; serves the built UI when web/dist exists)
    ../.venv/bin/python -m uvicorn agent_fleet.main:app --host 127.0.0.1 --port 8000
    # (or, from the repo root: PYTHONPATH=backend .venv/bin/python -m uvicorn agent_fleet.main:app)

    # frontend development, or build it and open http://127.0.0.1:8000
    cd ../web
    npm install
    npm run dev        # http://localhost:5173 (proxies /api to :8000)
    npm run build      # writes web/dist, served by the backend

First run (from `backend/`):

    ../.venv/bin/python -m agent_fleet.cli sync              # load inventory
    ../.venv/bin/python -m agent_fleet.cli runtimes          # list what was found
    ../.venv/bin/python -m agent_fleet.cli scan --host <name> # probe one machine

Then open the UI and press **Scan all**.

## Where the machine list comes from

Two source kinds, merged by host name (later sources only fill gaps):

1. **Inventory files** - every path in `AGENT_FLEET_INVENTORY_PATHS`, in
   order, either an Ansible inventory (YAML `all.children.<group>.hosts`, or
   INI, including `ansible_host`/`ansible_user`/`ansible_port`) or the project
   YAML format. The default is the repo's `inventory.yaml`, which exists only
   to express what SSH config cannot: runtime kind hints and WSL distro names.
2. **OpenSSH client config** - every `Host` alias in `~/.ssh/config`
   (ProxyJump, ports, Tailscale names, per-host users all keep working). It is
   merged last, but only fills fields the inventory left empty.

Merging fills gaps per field: `hostname`, `user`, `port`, `group`, tags, WSL
declarations and a non-`auto` kind hint; it never overwrites a value an
earlier source already provided.

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

This is a read-only observer.

* Only whitelisted, known config paths are read; `.ssh/`, private keys,
  `.gnupg/`, `.aws/credentials`, `.npmrc`, `.netrc`, `.docker/config.json`,
  `.kube/config`, `/etc/shadow`, keychain files and similar are on a hard deny
  list in the probe generator.
* `~/.pi/agent/auth.json` and `~/.hermes/.env` are never requested.
* The API has no authentication and is meant to bind to loopback; do not
  expose it to untrusted networks.
* Output is sanitized three ways: field whitelists, recursive dropping of
  credential-named keys, and redaction of credential-looking values.
* URLs keep scheme + host + port + path; query strings, embedded credentials
  and secret-looking path segments are stripped.
* Environment blocks (user settings and per-server MCP `env`/`headers`) are
  reported as key names only.
* Raw remote config lives in memory during a scan only; it is never written to
  disk. The database stores the sanitized structure.
* Every configuration carries a `resolution`:

  * `declared` - at least one scope file existed but could not be read or
    parsed, so stored values are "as written in the files that were readable"
    and may miss an override. A warning names the unreadable file.
  * `effective` - every file in the agent's precedence chain was readable or
    definitively absent, so the merged values reflect all declared scopes
    (for example project settings overriding user settings for Claude).
    A project-scope file that was never probed because the second pass failed
    does not lower this verdict; the scope table shows `not_probed` in that
    case.

  Even an `effective` answer only covers files: environment variables and CLI
  flags are outside what the probe can see, and that is never claimed.

## Configuration (env vars)

| Variable | Default | Meaning |
| --- | --- | --- |
| `AGENT_FLEET_DB_PATH` | `<repo>/data/fleet.db` | SQLite location |
| `AGENT_FLEET_INVENTORY_PATHS` | `["<repo>/inventory.yaml"]` | extra inventory files/dirs (absolute default) |
| `AGENT_FLEET_SSH_CONFIG_PATH` | `~/.ssh/config` | ssh config to read |
| `AGENT_FLEET_SCAN_CONCURRENCY` | `4` | concurrent runtimes per run (hard ceiling 8) |
| `AGENT_FLEET_COMMAND_TIMEOUT` | `25` | per-probe timeout (s); Windows chunks share it |
| `AGENT_FLEET_SSH_CONNECT_TIMEOUT` | `10` | ssh connect timeout (s) |
| `AGENT_FLEET_SCAN_TTL_SECONDS` | `21600` | snapshot staleness threshold |
| `AGENT_FLEET_INCLUDE_LOCAL_RUNTIME` | `true` | also probe the controller machine |
| `AGENT_FLEET_LOCAL_HOST_LABEL` | `local-controller` | host name that maps to the local runner |

`AGENT_FLEET_API_PREFIX`, `AGENT_FLEET_PERSIST_RAW_CONFIG` and
`AGENT_FLEET_WORKSPACE_ROOT` are defined in `settings.py` but not wired up
yet: the API prefix is hardcoded to `/api`, and raw config is never persisted
regardless of the flag.

## Tests

    .venv/bin/python -m pytest tests/ -q

92 tests: adapters against fixtures (installed / absent / config-only /
missing / malformed / unreadable), the sanitizer, the probe protocol,
all three inventory formats, timeout and connection-failure handling,
concurrency, last-known-good retention, diff, scope precedence, scheduler and
export, plus the HTTP API.

Run from the repo root, as shown above.

## Repo layout

    backend/agent_fleet/   inventory, remotes (ssh/wsl/powershell), adapters, services, api
    web/src/               overview, matrix, inspector, compare
    tests/fixtures/        real-shaped agent configs including fake credentials
    docs/                  requirements, research, architecture, progress
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
* Hosts you do not want in the fleet can be excluded at the host level; they
  keep their history but disappear from every view until you turn them back on.
* Snapshot history is kept indefinitely; there is no pruning.
* The API has no authentication and is meant for loopback use.

See `docs/progress.md` and `docs/requirements.md`.
