export type Runtime = {
  id: string
  host_id: string
  host: string
  key: string
  kind: string
  label: string
  os_name: string | null
  arch: string | null
  home_dir: string | null
  collect_user: string | null
  transport: string
  ssh_target: string | null
  wsl_distro: string | null
  enabled: boolean
  reachable: boolean | null
  last_seen_at: string | null
  last_success_at: string | null
  last_scan_at: string | null
  last_error: string | null
  snapshot_age_s: number | null
  stale: boolean | null
  agents_installed: number
  agents?: string[]
  agent_details?: {
    agent: string
    version: string | null
    channel?: string | null
    status: string
    version_state?: string
    latest_available?: string | null
    package?: string | null
    source?: string | null
    release_url?: string | null
  }[]
}

export type AgentMeta = { id: string; name: string; config_supported: boolean }

export type MatrixCell = {
  state: string
  installation_id: string | null
  version?: string | null
  config_status?: string | null
  error?: string | null
  last_success?: string | null
}

export type MatrixColumn = { runtime: Runtime; cells: Record<string, MatrixCell> }

export type Overview = {
  totals: {
    runtimes: number
    hosts: number
    online: number
    offline: number
    unknown: number
    installations: number
  }
  agents: { id: string; installations: number }[]
  last_successful_scan: string | null
  recent_runs: {
    id: string
    status: string
    started: string | null
    finished: string | null
    total: number
    succeeded: number
    failed: number
  }[]
  stale_after_s: number
}

export type Installation = {
  id: string
  runtime_id: string
  agent: string
  status: string
  detected: boolean
  version: string | null
  version_raw: string | null
  exec_path: string | null
  install_method: string | null
  config_dir: string | null
  detected_at: string | null
  error: string | null
  config_status: string
  runtime?: {
    id: string
    key: string | null
    kind: string | null
    host: string | null
    collect_user: string | null
  }
}

export type ConfigSource = {
  id: string
  scope: string
  path: string
  format: string | null
  kind: string
  present: boolean
  readable: boolean
  size_bytes: number | null
  status: string
  error: string | null
  captured_at: string | null
}

export type QuotaBucket = {
  id?: string | null
  name: string
  window: 'weekly' | '5h' | string
  remaining_fraction: number
  reset_at: string
}

export type QuotaGroup = {
  id: string
  name: string
  buckets: QuotaBucket[]
}

export type QuotaAccount = {
  email: string
  name: string
  aliases?: string[]
  tier?: { id?: string | null; name?: string | null } | null
  observed_at?: string | null
  groups: QuotaGroup[]
  min_remaining_fraction?: number | null
}

export type QuotaSnapshot = {
  source: string
  path?: string | null
  observed_at?: string | null
  accounts: QuotaAccount[]
}

export type QuotaSummary = {
  source?: string | null
  account_count: number
  observed_at?: string | null
  min_remaining_fraction?: number | null
}

export type AgentConfig = {
  id: string
  resolution: string
  parse_status: string
  model: string | null
  provider: string | null
  base_url: string | null
  base_url_masked: boolean
  mcp_servers: any[] | null
  skills: any[] | null
  settings: any | null
  quota?: QuotaSnapshot | null
  unsupported_fields: string[] | null
  sanitized: any | null
  warnings: string[] | null
  collected_at: string | null
}

export type RuntimeRef = {
  id: string
  key: string | null
  kind: string | null
  label?: string | null
  host: string | null
  collect_user: string | null
  os_name?: string | null
  last_success_at?: string | null
  reachable?: boolean | null
}

export type InstallationDetail = Installation & {
  config: AgentConfig | null
  sources: ConfigSource[]
  runtime: RuntimeRef
}

export type QuotaEntry = {
  installation_id: string
  agent: string
  host: string | null
  runtime_id: string
  runtime_key: string
  kind: string | null
  collect_user: string | null
  config_status: string
  collected_at: string | null
  quota: QuotaSnapshot
  quota_summary: QuotaSummary | null
}

export type QuotaProviderSummary = {
  agent: string
  name: string
  installations: number
  accounts: number
}

export type QuotaProviderInfo = {
  id: string
  name: string
  agent: string
  credential_label: string
  credential_hint: string
  online: boolean
}

export type ImportedQuotaAccount = {
  id: string
  agent: string
  provider: string
  provider_name: string
  label: string | null
  email: string | null
  name: string | null
  plan: string | null
  status: 'ok' | 'error' | 'unknown'
  last_error: string | null
  created_at: string | null
  updated_at: string | null
  last_refreshed_at: string | null
  quota: QuotaSnapshot | null
  quota_summary: QuotaSummary | null
}

export type QuotasResponse = {
  quotas: QuotaEntry[]
  providers: QuotaProviderSummary[]
  generated_at: string
}

export type ScanTask = {
  id: string
  runtime_id: string
  status: string
  duration_ms: number | null
  agents_found: number
  error: string | null
  started_at: string | null
  finished_at: string | null
}

export type ScanRun = {
  id: string
  status: string
  triggered_by: string
  started_at: string | null
  finished_at: string | null
  total: number
  succeeded: number
  failed: number
  concurrency: number
  live: { done: number; ok: number; failed: number; status: string | null }
  tasks: ScanTask[]
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch('/api' + path)
  if (!res.ok) throw new Error('GET ' + path + ' failed: ' + res.status)
  return (await res.json()) as T
}

async function post<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch('/api' + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body ?? {})
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(text || 'POST ' + path + ' failed: ' + res.status)
  }
  return (await res.json()) as T
}

async function del<T>(path: string): Promise<T> {
  const res = await fetch('/api' + path, { method: 'DELETE' })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(text || 'DELETE ' + path + ' failed: ' + res.status)
  }
  return (await res.json()) as T
}

export type DiffChange = { path: string; kind: string; a: any; b: any }

export type InstallationDiff = {
  kind: string
  same_agent: boolean
  identical: boolean
  a: { id: string; agent: string; runtime_id: string; runtime?: any }
  b: { id: string; agent: string; runtime_id: string; runtime?: any }
  changes: DiffChange[]
}

export type SnapshotDiff = {
  kind: string
  identical: boolean
  a: { id: string; runtime_id: string; runtime?: any; created_at?: string | null }
  b: { id: string; runtime_id: string; runtime?: any; created_at?: string | null }
  per_agent: Record<string, DiffChange[]>
  changes: DiffChange[]
}

export type CompareCandidate = {
  installation_id: string
  agent: string
  status: string
  version: string | null
  runtime_id: string
  host: string | null
  runtime_key: string | null
  kind: string | null
  user: string | null
  model: string | null
  config_status: string
  collected_at: string | null
}

export const api = {
  agents: () => get<{ agents: AgentMeta[] }>('/agents'),
  overview: () => get<Overview>('/overview'),
  matrix: () =>
    get<{ agents: AgentMeta[]; columns: MatrixColumn[]; stale_after_s: number }>('/matrix'),
  runtimes: (enabledOnly = false, includeDisabledHosts = false) => {
    const q = new URLSearchParams()
    if (enabledOnly) q.set('enabled_only', 'true')
    if (includeDisabledHosts) q.set('include_disabled_hosts', 'true')
    const qs = q.toString() ? '?' + q.toString() : ''
    return get<{ runtimes: Runtime[]; stale_after_s: number }>('/runtimes' + qs)
  },
  installations: (params: { runtime_id?: string; agent_id?: string; detected_only?: boolean }) => {
    const q = new URLSearchParams()
    if (params.runtime_id) q.set('runtime_id', params.runtime_id)
    if (params.agent_id) q.set('agent_id', params.agent_id)
    if (params.detected_only) q.set('detected_only', 'true')
    const suffix = q.toString() ? '?' + q.toString() : ''
    return get<{ installations: Installation[] }>('/installations' + suffix)
  },
  installation: (id: string) => get<InstallationDetail>('/installations/' + id),
  quotas: (includeDisabledHosts = false) => {
    const qs = includeDisabledHosts ? '?include_disabled_hosts=true' : ''
    return get<QuotasResponse>('/quotas' + qs)
  },
  quotaProviders: () => get<{ providers: QuotaProviderInfo[] }>('/quota-providers'),
  quotaAccounts: () => get<{ accounts: ImportedQuotaAccount[] }>('/quota-accounts'),
  addQuotaAccount: (payload: { provider: string; credential: string; label?: string }) =>
    post<ImportedQuotaAccount>('/quota-accounts', payload),
  refreshQuotaAccount: (id: string) =>
    post<ImportedQuotaAccount>('/quota-accounts/' + id + '/refresh'),
  deleteQuotaAccount: (id: string) => del<{ ok: boolean }>('/quota-accounts/' + id),
  scans: () => get<{ runs: any[] }>('/scans'),
  snapshots: (runtimeId?: string, limit = 100) => {
    const q = new URLSearchParams()
    if (runtimeId) q.set('runtime_id', runtimeId)
    q.set('limit', String(limit))
    return get<{
      snapshots: {
        id: string
        runtime_id: string
        created_at: string | null
        agents_found: number
      }[]
    }>('/snapshots?' + q.toString())
  },
  compare: (agentId: string) => get<{ agent: string; candidates: CompareCandidate[] }>('/compare/' + agentId),
  agentView: (agentId: string) =>
    get<{
      agent: string
      name: string
      detected_count: number
      consistent_configs: boolean
      config_groups: { signature: string; machines: string[]; count: number }[]
      installations: {
        id: string
        host: string
        runtime_key: string
        kind: string
        os_name?: string | null
        arch?: string | null
        ssh_target?: string | null
        collect_user: string | null
        status: string
        detected: boolean
        version: string | null
        install_method: string | null
        config_status: string
        config_summary: {
          model: string | null
          provider: string | null
          base_url: string | null
          parse_status: string | null
          resolution: string | null
          channel?: string | null
        } | null
        channel?: string | null
        version_state: string
        latest_available?: string | null
        package?: string | null
        source?: string | null
      }[]
      version: {
        package: string | null
        latest_available: string | null
        source: string
        release_url?: string | null
        latest_installed: string | null
        distinct_versions: string[]
        up_to_date: number
        behind: number
        unknown_source: boolean
        error?: string
      }
    }>('/agent-view/' + agentId),
  diffInstallations: (a: string, b: string) =>
    get<InstallationDiff>('/diff/installations?a=' + a + '&b=' + b),
  diffSnapshots: (a: string, b: string) =>
    get<SnapshotDiff>('/diff/snapshots?a=' + a + '&b=' + b),
  scan: (id: string) => get<ScanRun>('/scan/' + id),
  activeScans: () => get<{ runs: any[] }>('/scan/active'),
  startScan: (runtimeIds: string[] | null) =>
    post<{ run_id: string; total: number }>('/scan', {
      runtime_ids: runtimeIds ?? [],
      all: !runtimeIds
    }),
  setRuntimeEnabled: (id: string, enabled: boolean) =>
    post<{ ok: boolean }>('/runtimes/' + id + '/enabled', { enabled }),
  machine: (hostId: string) =>
    get<{
      id: string
      name: string
      hostname: string | null
      enabled: boolean
      agents_detected: number
      runtimes: {
        id: string
        key: string
        kind: string
        os_name: string | null
        reachable: boolean | null
        stale: boolean | null
        agents_installed: number
        last_error: string | null
        installations: {
          id: string
          agent: string
          status: string
          detected: boolean
          version: string | null
          channel?: string | null
          version_state?: string
          latest_available?: string | null
          package?: string | null
          source?: string | null
          release_url?: string | null
          exec_path: string | null
          install_method: string | null
          config_dir: string | null
          config_status: string
          error: string | null
          config_summary: {
            model: string | null
            provider: string | null
            base_url: string | null
            parse_status: string | null
            resolution: string | null
            channel?: string | null
          } | null
        }[]
      }[]
    }>('/machines/' + hostId),
  hosts: (includeDisabled = false) =>
    get<{
      hosts: {
        id: string
        name: string
        hostname: string | null
        source: string
        group: string | null
        enabled: boolean
        tags: any
        runtimes?: Runtime[]
      }[]
      include_disabled: boolean
    }>('/hosts?include_disabled=' + (includeDisabled ? 'true' : 'false')),
  updates: () =>
    get<
      Record<
        string,
        {
          source: string
          package: string | null
          latest_available: string | null
          release_url?: string | null
        }
      >
    >(
      '/updates'
    ),
  syncInventory: () => post<{ ok: boolean; hosts_seen?: number }>('/inventory/sync'),
  setHostEnabled: (id: string, enabled: boolean) =>
    post<{ ok: boolean; host?: string }>('/hosts/' + id + '/enabled', { enabled }),
  deleteRuntime: (id: string) =>
    fetch('/api/runtimes/' + id, { method: 'DELETE' }).then(async (r) => {
      if (!r.ok) throw new Error((await r.text()) || 'delete failed')
      return (await r.json()) as { ok: boolean; deleted: string }
    }),
  upgradeInstallation: (instId: string) =>
    post<UpgradeTask>('/installations/' + instId + '/upgrade'),
  upgradeRuntimeAgent: (runtimeId: string, agentId: string) =>
    post<UpgradeTask>('/runtimes/' + runtimeId + '/agents/' + agentId + '/upgrade'),
  getUpgradeTask: (taskId: string) =>
    get<UpgradeTask>('/upgrades/' + taskId),
  getActiveUpgradeForInstallation: (instId: string) =>
    get<{ active_task: UpgradeTask | null }>('/installations/' + instId + '/upgrade/active'),
  listUpgradeTasks: () =>
    get<{ tasks: UpgradeTask[] }>('/upgrades'),
  upgradeBatch: (installationIds: string[]) =>
    post<{ tasks: UpgradeTask[]; queued: number }>('/upgrades/batch', {
      installation_ids: installationIds
    }),
  upgradeMachineAll: (hostId: string) =>
    post<{ tasks: UpgradeTask[]; queued: number; host_id: string }>('/machines/' + hostId + '/upgrade-all'),
  upgradeAgentAll: (agentId: string) =>
    post<{ tasks: UpgradeTask[]; queued: number; agent_id: string }>('/agents/' + agentId + '/upgrade-all'),
  upgradeFleetAll: () =>
    post<{ tasks: UpgradeTask[]; queued: number }>('/fleet/upgrade-all'),
  listSessions: (params?: {
    q?: string
    host_name?: string
    agent_id?: string
    project?: string
    pinned_only?: boolean
    limit?: number
    offset?: number
  }) => {
    const sp = new URLSearchParams()
    if (params?.q) sp.set('q', params.q)
    if (params?.host_name) sp.set('host_name', params.host_name)
    if (params?.agent_id) sp.set('agent_id', params.agent_id)
    if (params?.project) sp.set('project', params.project)
    if (params?.pinned_only) sp.set('pinned_only', 'true')
    if (params?.limit) sp.set('limit', String(params.limit))
    if (params?.offset) sp.set('offset', String(params.offset))
    const qs = sp.toString()
    return get<SessionsResponse>('/sessions' + (qs ? '?' + qs : ''))
  },
  syncSessions: () =>
    post<{ ok: boolean; total_sessions: number; runtimes_scanned: number; errors: string[] }>('/sessions/sync'),
  getSession: (sessionId: string) =>
    get<SessionSummary>('/sessions/' + sessionId),
  getSessionTranscript: (sessionId: string) =>
    get<SessionTranscriptResponse>('/sessions/' + sessionId + '/transcript'),
  toggleSessionPin: (sessionId: string) =>
    post<{ ok: boolean; session_id: string; is_pinned: boolean }>('/sessions/' + sessionId + '/pin'),
  getSessionResumeCommand: (sessionId: string) =>
    get<{ session_id: string; resume_command: string }>('/sessions/' + sessionId + '/resume-command')
}

export type SessionSummary = {
  id: string
  runtime_id: string
  host_name: string
  agent_id: string
  session_key: string
  title: string
  project_path: string | null
  project_name: string
  file_path: string | null
  started_at: string | null
  updated_at: string | null
  message_count: number
  token_count: number | null
  is_pinned: boolean
  summary: string | null
  resume_command: string
  has_cached_transcript: boolean
}

export type SessionsResponse = {
  items: SessionSummary[]
  total: number
  facets: {
    agents: string[]
    hosts: string[]
    projects: string[]
  }
}

export type ConversationTurn = {
  role: 'user' | 'assistant' | 'tool'
  content: string
  thinking?: string | null
  tool_calls?: Array<{ id?: string; name?: string; input?: any }> | null
  timestamp?: string | null
}

export type SessionTranscriptResponse = {
  session_id: string
  turns: ConversationTurn[]
  has_secrets_masked: boolean
  masked_count: number
  cached: boolean
  cached_at: string | null
}

export type UpgradeTask = {
  task_id: string
  installation_id: string
  runtime_id: string
  agent: string
  host: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  started_at: number
  finished_at?: number | null
  duration_ms: number
  ok?: boolean | null
  exit_code?: number | null
  stdout: string
  stderr: string
  error?: string | null
  old_version?: string | null
  new_version?: string | null
  scan_refreshed?: boolean
}

// For backwards compatibility
export type UpgradeResult = UpgradeTask



const HERMES_INSTALL_URL =
  'https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh'

export function getUpdateCommand(
  agent: string,
  pkg?: string | null,
  targetHost?: string | null
): { local: string; remote: string } {
  const npmFallback = pkg ? `npm install -g ${pkg}@latest` : ''
  let cmd = ''
  if (agent === 'claude') cmd = withFallback('claude update', npmFallback)
  else if (agent === 'qoder') cmd = withFallback('qodercli update', npmFallback)
  else if (agent === 'opencode') cmd = withFallback('opencode upgrade', npmFallback)
  else if (agent === 'pi') cmd = withFallback('pi update', npmFallback)
  else if (agent === 'agy') cmd = 'agy update'
  else if (agent === 'hermes')
    cmd = `hermes update || bash -c 'tmp=$(mktemp) && curl -fsSL ${HERMES_INSTALL_URL} -o $tmp && bash $tmp; status=$?; rm -f $tmp; exit $status'`
  else if (pkg) cmd = npmFallback
  else cmd = `npm install -g ${agent}@latest`

  const remote =
    targetHost && targetHost !== 'macbook-air' && targetHost !== 'local-controller'
      ? `ssh ${targetHost} "${cmd}"`
      : cmd
  return { local: cmd, remote }
}

function withFallback(primary: string, fallback: string): string {
  return fallback ? `${primary} || ${fallback}` : primary
}

