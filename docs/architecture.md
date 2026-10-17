# Architecture

Read-only observer: it connects to machines you already have SSH access to,
reads agent installs and their user-scope configuration, sanitizes everything,
and stores a normalized snapshot. It never writes to a remote machine.

## Shape

    React + TS (Vite)  --->  FastAPI (single process)  --->  ssh(1)  --->  hosts
                                     |
                                     +--> SQLite (fleet.db)

One backend process. No queue, no workers, no microservices. Scans run as
asyncio tasks inside the API process.

## Collection model

The central idea is **one round trip per runtime**.

1. The backend renders a read-only shell script from the union of everything
   every adapter wants to know (binaries, config paths, skills directories).
2. The script is streamed to `sh -s` over one `ssh` connection (or to
   `wsl.exe -d <distro> -- sh -s`, or to `powershell.exe -EncodedCommand ...`
   in chunks on Windows).
3. The script replies with a framed record stream, `AF<TAB>tag<TAB>fields...`,
   including whole file contents for whitelisted config paths.
4. The reply is parsed into `RemoteFacts` - a plain data object.

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

Every adapter result passes through one central `scrub_config()` before it can
be stored, so a future adapter cannot leak by forgetting to sanitize.

## Data model (SQLite)

    hosts 1---n runtimes 1---n agent_installations 1---n config_sources
                                    1---1 agent_configurations
    scan_runs 1---n scan_tasks      snapshots (history per runtime)

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
  `stale` marker.

## Security model

Three layers, all in `backend/agent_fleet/adapters/sanitize.py`:

1. **Field whitelist.** Adapters emit known fields only. Unknown keys are
   reported by name and their values are dropped.
2. **Name-based dropping.** Any key whose name looks credential-ish
   (`apiKey`, `token`, `password`, `Authorization`, ...) is removed
   recursively - nesting is what leaked a real key during development.
3. **Value-based redaction.** Values that look like credentials (`sk-...`,
   `ghp_...`, JWTs, `Bearer <token>`) are replaced even under an innocent key.

Additional rules:

* URLs are reduced to scheme + host + port + path; query strings, fragments,
  embedded credentials and secret-looking path segments are stripped.
* Environment blocks are reported as key names only, never values.
* The probe generator refuses to touch `.ssh/`, `id_rsa`, `.aws/credentials`,
  `.npmrc`, keychain and similar paths even if an adapter asked for them.
* Raw remote config data exists only in memory during a scan and is never
  persisted; only the sanitized structure reaches SQLite.
* Declarations vs effective values: configurations are stored as **declared**
  ("as written in the file"). The tool never claims to know the effective
  value, because environment variables, project-level files and CLI flags can
  all override it.

## Layout

    backend/agent_fleet/
      inventory/      Ansible (YAML+INI), ssh config, project YAML
      remotes/
        executor.py   Local / SSH / Windows / WSL runners
        probe.py      POSIX probe script + record parser
        probe_win.py  PowerShell probe (chunked, base64)
      adapters/       sanitize, base, and one module per agent
      services/       scanner, persistence, scan runner, inventory sync
      api/            HTTP routes
    web/src/          overview, matrix, inspector
    tests/            fixtures + unit/integration tests
