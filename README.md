# Agent Fleet Explorer

Read-only control plane for the AI coding agents installed across your
machines. It answers one question per machine: **which agents are installed,
and what are they configured to use?**

Phase 1 is discovery, collection and display only. It never modifies a remote
machine, never installs anything, and never executes a command that comes from
the web UI. The requirements it satisfies (and the verification behind each)
are in `docs/requirements.md`.

Supported agents: Codex CLI, Claude Code, OpenCode, Pi, Hermes,
Antigravity (AGY), CodeBuddy, and Qoder (QoderCN).
Supported targets: macOS, Linux, Windows (PowerShell) and WSL distros,
reached through your existing SSH configuration. The machine list itself comes
only from inventory files; `~/.ssh/config` is not imported automatically.

Requirements: Python 3.13, Node 20+, and the `ssh` client on the controller.

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
* `--build`: force rebuild the frontend bundle before serving.
* `--open`: launch browser automatically once ready.
* `--dev`: run full development mode with hot reload.

---

### Manual step-by-step setup

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
* **Quotas** - a standalone, account-based menu. You import an account
  credential (verified against the vendor's online API before it is saved),
  and the view shows the account, plan, weekly/5-hour bars, remaining
  percentage and reset countdown. Refreshing calls the online API in real time.
  Accounts are grouped by Agent. Codex/ChatGPT and Antigravity are supported.
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
* Imported quota credentials live in `data/quota_accounts.json` with mode
  `0600`, outside SQLite; the API never returns the credential, and the fleet
  export never contains it. Credentials are only used for the vendor usage
  request.
* Antigravity OAuth client secrets are not stored in source. Put them in
  `data/antigravity_clients.json` (mode `0600`, gitignored) as a JSON object
  mapping `client_id` to `client_secret`, or pass the same object through
  `AGENT_FLEET_ANTIGRAVITY_CLIENTS`. Only the public client IDs live in
  `antigravity_quota.py`.
* `~/.gemini/agy-swap/accounts.json` is read for quota display only. The
  adapter copies an explicit whitelist (email, name, aliases, tier, quota
  buckets), so `token_data`, `token_hash`, `secret_ref` and every other
  credential field are dropped; the raw file is never persisted.
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
* Hosts you do not want in the fleet can be disabled or removed at the host
  level; disabled hosts keep their history but disappear from every view until
  you turn them back on. Inventory files are the only source of new hosts.
* Snapshot history is kept indefinitely; there is no pruning.
* The API has no authentication and is meant for loopback use.

See `docs/progress.md` and `docs/requirements.md`.
