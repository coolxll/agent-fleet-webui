# Technical research

Goal of this phase: decide what to reuse, what to implement, and which remote
mechanisms are reliable enough to collect agent installs from macOS, Linux,
Windows and WSL machines over SSH. Findings here feed the constraints and
verification notes in `requirements.md`.

## 1. Existing projects considered

| Project | What it does well | Reused here? |
| --- | --- | --- |
| **CC-Switch** | Per-machine switching of provider/model/config for locally installed agents; knows each agent's config file layout and formats. | Concepts reused (per-agent config paths and formats). Its code targets a single interactive workstation and mutates config, so nothing was imported. |
| **Magpie** | Agent discovery plus provider/model/MCP/skills handling. | Concept of a per-agent adapter reused. Not imported: it is built around local interactive use and write operations. |
| **Agent Deck** | Manages agents on remote hosts over SSH. | Validated the SSH-only, no-agent-installed-on-remote approach. Not imported. |
| **FleetDM** | Device inventory + snapshotting at scale. Terms: host, inventory, snapshot, stale data. | Data-model vocabulary and the snapshot/staleness concept reused. Not imported (it requires an agent on the endpoint). |
| **Semaphore UI** | Runs Ansible inventories and remote tasks. | Confirmed "reuse the existing Ansible inventory, do not duplicate it". Not imported. |

Conclusion: all five solve a different problem (mostly local, mostly
mutating). The read-only, no-remote-install constraint of this project makes
the integration surface small: **inventory reuse + a probe script + adapters**.

## 2. What an agent leaves on disk (verified on real machines)

Every check below was executed against real hosts and against this machine.

| Agent | Executable | User config | Format | Notes |
| --- | --- | --- | --- | --- |
| Codex CLI | `codex` | `~/.codex/config.toml` | TOML | `model`, `model_provider`, `[model_providers.*]`, `[mcp_servers.*]`, `[[skills.config]]`. Real installs keep `experimental_bearer_token` and MCP `env` blocks in this file. |
| Claude Code | `claude` | `~/.claude/settings.json`, `~/.claude.json` | JSON | `settings.json.env` holds `ANTHROPIC_BASE_URL` and `ANTHROPIC_AUTH_TOKEN`; `~/.claude.json` holds user-scope `mcpServers` and per-project servers. Skills live in `~/.claude/skills/<name>/SKILL.md`. |
| OpenCode | `opencode` | `~/.config/opencode/opencode.json` (`.jsonc` also valid) | JSON / JSONC | `model`, `provider.<name>.options.apiKey/baseURL`, `mcp`, `agent`. Skills in `~/.config/opencode/skills`. |
| Pi | `pi` | `~/.pi/agent/settings.json`, `~/.pi/agent/models.json` | JSON | `defaultModel`/`defaultProvider`, provider catalog with `baseUrl`. `~/.pi/agent/auth.json` holds credentials and is never read. |
| Hermes | `hermes` | `~/.hermes/config.yaml` | YAML | `model.default`/`model.provider`, `providers.<n>.base_url` and inline `api_key`, `mcp_servers`. `~/.hermes/.env` holds live credentials and is never read. |

On Windows the same agents use `%USERPROFILE%` with backslash paths; WSL
distros are independent machines with their own home directory and their own
installs of the same agents.

## 3. Version probing

All five agents answer `--version` on stdout.

* `codex` -> `codex-cli 0.163.0`
* `claude` -> `2.1.280 (Claude Code)`
* `opencode` -> `1.19.0`
* `pi` -> `1.1.0`
* `hermes` -> `Hermes Agent v0.22.2 (2026.9.24) ...`

Two traps found on real hosts and handled:

1. **Exit codes were being masked.** Piping straight into `head`/`tr`
   replaced the real status with the status of the last pipe element, so a
   missing binary reported success with the text `<shell>: <line>: not found`.
   The version extractor then happily parsed the shell's line number as a
   version ("57"). Fixed by capturing `$?` immediately after the assignment.
2. **Agents installed outside PATH.** Non-interactive shells do not source
   nvm/bun/volta. The probe prepends the usual `~/.local/bin`,
   `~/.nvm/versions/node/<latest>/bin`, `~/.bun/bin`, cargo, fnm and Homebrew
   directories, and adapters also check known install locations directly.

## 4. Remote execution: native ssh(1) vs a Python SSH library

Chosen: **native OpenSSH**, one script per runtime streamed to `sh -s`.

Reasons: the user's `~/.ssh/config` (ProxyJump, per-host ports, Tailscale
names, ssh-agent identities, JumpHosts such as `pve -> r9kp-win`) keeps
working untouched; no new credentials or key handling in this project; it
matches "use existing SSH credentials for already authorized machines".

Cost: each scan opens one SSH connection per runtime, which is why everything
is collected in a single round trip.

## 5. Windows (this turned out to be the largest research effort)

Findings that drove the implementation:

* The default shell of OpenSSH-for-Windows is `cmd.exe`, so POSIX scripts do
  not run. Fingerprinting (`sh -s` fails) detects it.
* Streaming a script to `powershell.exe -Command -` **executes line by line**:
  multi-line `if/elseif` blocks break.
* Passing the script inline through `cmd.exe` breaks on quoting and length.
* `-EncodedCommand <base64 of UTF-16LE>` works, but the remote command line
  is **silently truncated around 8k characters**; a truncated script fails to
  parse with "missing ')'" and produces no output at all.
  => The PowerShell probe is generated as a compact data-driven script and
  split into chunks that each stay under that limit.
* PowerShell treats `(<expr>), ' '` in a `-replace` argument as an array
  literal, which makes the substitution parse as a missing parenthesis.
  Replaced with `-split`/`-join`.
* WSL is a separate runtime: `wsl.exe -l -q` enumerates distros (output can
  arrive UTF-16 with nulls), and each distro is probed with a normal POSIX
  script through `wsl.exe -d <distro> [-u user] -- sh -s`.

## 6. Things deliberately not implemented

* Config write-back, provider switching, key sync, remote installs, remote
  task execution, session orchestration. Phase 1 is a read-only observer.
* Continuous polling: scans are explicit.
* Reading `auth.json`, `.env`, SSH keys, keychain and `.npmrc` - they are on a
  hard deny list in the probe generator.
