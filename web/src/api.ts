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

export type AttentionItemType = 'scan_failed' | 'scan_stale' | 'task_blocked'
export type AttentionSeverity = 'error' | 'warning'
export type AttentionTargetType = 'host' | 'task'
export type AttentionActionView = 'machines' | 'tasks'

export interface AttentionItem {
  id: string
  type: AttentionItemType
  severity: AttentionSeverity
  target_type: AttentionTargetType
  target_id: string
  target_name: string
  message: string
  first_detected_at: string | null
  last_updated_at: string | null
  action_view: AttentionActionView
  action_payload: Record<string, any>
}

export interface AttentionSummary {
  total: number
  items: AttentionItem[]
  counts: {
    error: number
    warning: number
  }
}

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

export type QuotaPackage = {
  name: string
  total: number
  used: number
  remaining: number
  count?: number | null
  unit?: string | null
  reset_at?: string | null
}

export type DailyConsumption = {
  name: string
  used: number
  total?: number | null
  remaining?: number | null
  unit?: string | null
  reset_at?: string | null
}

export type QuotaBucket = {
  id?: string | null
  name: string
  window: 'weekly' | '5h' | string
  remaining_fraction: number
  reset_at?: string | null
  reset_kind?: 'expires' | 'partial_expiry' | 'reset'
  used?: number | null
  total?: number | null
  remaining?: number | null
  unit?: string | null
}

export type QuotaGroup = {
  id: string
  name: string
  buckets: QuotaBucket[]
  packages?: QuotaPackage[] | null
  daily_consumption?: DailyConsumption[] | null
}

export type QuotaAccount = {
  email: string
  name: string
  aliases?: string[]
  tier?: { id?: string | null; name?: string | null } | null
  observed_at?: string | null
  groups: QuotaGroup[]
  min_remaining_fraction?: number | null
  packages?: QuotaPackage[] | null
  daily_consumption?: DailyConsumption[] | null
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

export type MagpieQuotaSource = {
  available: boolean
  error: string | null
  accounts: { id: string; email: string | null; label: string; plan: string | null; linked_account_id: string | null }[]
}

export type QuotaCollectionSettings = { enabled: boolean; interval_seconds: number }

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
  credential_source: 'manual' | 'magpie'
  source_account_id?: string | null
  last_checked_at: string | null
  next_query_at: string | null
  error_code: 'auth_required' | 'source_unavailable' | 'query_failed' | null
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

export const TOKEN_STORAGE_KEY = 'agent_fleet_api_token'
export const AUTH_REQUIRED_EVENT = 'agent_fleet_auth_required'

let inMemoryToken: string = ''
try {
  inMemoryToken =
    (typeof localStorage !== 'undefined' ? localStorage.getItem(TOKEN_STORAGE_KEY) : '') || ''
} catch {
  // LocalStorage might be disabled in certain restricted environments
}

export function getApiToken(): string {
  if (inMemoryToken) return inMemoryToken
  try {
    if (typeof localStorage !== 'undefined') {
      const stored = localStorage.getItem(TOKEN_STORAGE_KEY)
      if (stored) {
        inMemoryToken = stored
        return stored
      }
    }
  } catch {
    // ignore
  }
  return ''
}

export function setApiToken(token: string): void {
  inMemoryToken = token.trim()
  try {
    if (typeof localStorage !== 'undefined') {
      if (inMemoryToken) {
        localStorage.setItem(TOKEN_STORAGE_KEY, inMemoryToken)
      } else {
        localStorage.removeItem(TOKEN_STORAGE_KEY)
      }
    }
  } catch {
    // ignore
  }
}

export function clearApiToken(): void {
  setApiToken('')
}

export async function authFetch(
  input: RequestInfo | URL,
  init?: RequestInit
): Promise<Response> {
  const token = getApiToken()
  const headers = new Headers(init?.headers)

  if (token && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${token}`)
  }

  const response = await fetch(input, {
    ...init,
    headers,
  })

  if (response.status === 401) {
    if (typeof window !== 'undefined' && window.dispatchEvent) {
      window.dispatchEvent(
        new CustomEvent(AUTH_REQUIRED_EVENT, {
          detail: {
            status: 401,
            url: typeof input === 'string' ? input : (input as any)?.url || String(input),
            message: 'Unauthorized: invalid or missing API secret',
          },
        })
      )
    }
  }

  return response
}

export const fetchWithAuth = authFetch

export type TokenCheckResult = {
  valid: boolean
  authRequired: boolean
  message?: string
}

export async function verifyApiToken(tokenToTest?: string): Promise<TokenCheckResult> {
  const token = (tokenToTest !== undefined ? tokenToTest : getApiToken()).trim()
  const headers = new Headers({ 'Content-Type': 'application/json' })
  if (token) {
    headers.set('Authorization', `Bearer ${token}`)
  }

  try {
    // 1. Probe /api/settings to inspect backend auth requirement
    const settingsRes = await fetch('/api/settings', { headers })
    if (settingsRes.status === 401) {
      // In strict mode (allow_anonymous_readonly=False), 401 means auth is required and secret is invalid
      return {
        valid: false,
        authRequired: true,
        message: 'Unauthorized: invalid or missing API secret (Strict mode)',
      }
    }

    if (settingsRes.ok) {
      const data = await settingsRes.json().catch(() => ({}))
      const authRequired = Boolean(data.auth_required)

      if (!authRequired) {
        return {
          valid: true,
          authRequired: false,
          message: 'Backend does not require authentication (open mode)',
        }
      }

      // Backend auth is enabled; test against lightweight route to check token validity
      const testRes = await fetch('/api/inventory/validate', {
        method: 'POST',
        headers,
        body: JSON.stringify({ content: '' }),
      })

      if (testRes.status === 401) {
        return {
          valid: false,
          authRequired: true,
          message: 'Unauthorized: invalid API secret',
        }
      }

      if (testRes.ok) {
        return {
          valid: true,
          authRequired: true,
          message: 'API secret verified successfully',
        }
      }
    }
  } catch (err: any) {
    return {
      valid: false,
      authRequired: true,
      message: err.message || 'Connection failed',
    }
  }

  return {
    valid: false,
    authRequired: true,
    message: 'Verification failed',
  }
}

async function get<T>(path: string): Promise<T> {
  const res = await authFetch('/api' + path)
  if (!res.ok) {
    const text = await res.text()
    throw new Error(text || 'GET ' + path + ' failed: ' + res.status)
  }
  return (await res.json()) as T
}

async function post<T>(path: string, body?: unknown, method = 'POST'): Promise<T> {
  const res = await authFetch('/api' + path, {
    method,
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
  const res = await authFetch('/api' + path, { method: 'DELETE' })
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

export type SystemVersion = {
  version: string
  commit: string
  commit_short: string
  build_time: string | null
  is_release: boolean
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
  magpieQuotaAccounts: () => get<MagpieQuotaSource>('/quota-sources/magpie'),
  linkMagpieQuotaAccount: (source_account_id: string) =>
    post<ImportedQuotaAccount>('/quota-sources/magpie/link', { source_account_id }),
  quotaCollection: () => get<QuotaCollectionSettings>('/quota-collection'),
  setQuotaCollection: (settings: QuotaCollectionSettings) =>
    post<QuotaCollectionSettings>('/quota-collection', settings, 'PUT'),
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
  deleteRuntime: (id: string) => del<{ ok: boolean; deleted: string }>('/runtimes/' + id),
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
    get<{ session_id: string; resume_command: string }>('/sessions/' + sessionId + '/resume-command'),
  getSchedule: () => get<any>('/schedule'),
  updateSchedule: (patch: any) => post<any>('/schedule', patch, 'PUT'),
  exportData: () => get<any>('/export'),
  getControllerSSHKey: () =>
    get<{ found: boolean; public_key?: string }>('/inventory/controller-ssh-key'),
  verifyToken: (token?: string) => verifyApiToken(token),
  getAttention: (limit = 50) => get<AttentionSummary>('/attention?limit=' + limit),
  getVersion: () => get<SystemVersion>('/system/version'),
  version: () => get<SystemVersion>('/system/version')
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
  else if (agent === 'herdr') cmd = 'herdr update'
  else if (agent === 'paseo') cmd = withFallback('npm install -g @getpaseo/cli@latest', npmFallback)
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

export type InventoryRawResponse = {
  ok: boolean
  exists: boolean
  path: string
  content: string
  example_content: string
}

export type InventoryValidateResponse = {
  ok: boolean
  valid: boolean
  error: string | null
  summary: {
    type?: string
    host_count?: number
    hosts?: string[]
    wsl_count?: number
    kinds?: Record<string, number>
    [key: string]: any
  }
}

export type InventorySaveResponse = {
  ok: boolean
  path: string
  summary?: any
  sync_result?: any
}

export async function getInventoryRaw(): Promise<InventoryRawResponse> {
  const res = await authFetch('/api/inventory/raw')
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

export async function validateInventory(content: string): Promise<InventoryValidateResponse> {
  const res = await authFetch('/api/inventory/validate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

export async function saveInventoryRaw(content: string): Promise<InventorySaveResponse> {
  const res = await authFetch('/api/inventory/raw', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  })
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    throw new Error(data.detail || `HTTP ${res.status}`)
  }
  return res.json()
}

export type AntigravityClientStatus = {
  client_id: string
  is_default: boolean
  configured: boolean
  masked_secret: string | null
  source: 'env' | 'file' | 'none'
}

export type AntigravitySettingsResponse = {
  ok: boolean
  clients: AntigravityClientStatus[]
}

export async function getAntigravityQuotaSettings(): Promise<AntigravitySettingsResponse> {
  const res = await authFetch('/api/quota-settings/antigravity')
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

export async function updateAntigravityQuotaSettings(
  secrets: Record<string, string>
): Promise<AntigravitySettingsResponse> {
  const res = await authFetch('/api/quota-settings/antigravity', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ secrets }),
  })
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    throw new Error(data.detail || `HTTP ${res.status}`)
  }
  return res.json()
}

// --------------------------------------------------------------------------
// SQLite-backed Inventory & Runner Fleet APIs
// --------------------------------------------------------------------------

export type InventoryHostRuntime = {
  id: string
  key: string
  kind: string
  transport: string
  enabled: boolean
  reachable: boolean | null
  last_seen_at: string | null
}

export type InventoryHost = {
  id: string
  name: string
  hostname: string
  description?: string
  inventory_source?: string
  group?: string
  tags?: Record<string, any>
  ssh_host?: string
  ssh_port?: number
  ssh_user?: string
  proxy?: string
  runner_enabled: boolean
  runner_max_concurrency: number
  runner_labels: string[]
  target_repos: string[]
  enabled: boolean
  created_at?: string
  updated_at?: string
  runtimes?: InventoryHostRuntime[]
  runtimes_count?: number
}

export type InventoryHostPayload = {
  name?: string
  hostname?: string
  description?: string
  group?: string
  tags?: Record<string, any>
  ssh_host?: string
  ssh_port?: number
  ssh_user?: string
  proxy?: string
  runner_enabled?: boolean
  runner_max_concurrency?: number
  runner_labels?: string[]
  target_repos?: string[]
  enabled?: boolean
}

export type SSHTestResult = {
  ok: boolean
  host: string
  ssh_target: string
  latency_ms?: number
  banner?: string
  error?: string
}

export async function listInventoryHosts(): Promise<InventoryHost[]> {
  const res = await authFetch('/api/inventory/hosts')
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const data = await res.json()
  return data.hosts || []
}

export async function getInventoryHost(idOrName: string): Promise<InventoryHost> {
  const res = await authFetch(`/api/inventory/hosts/${encodeURIComponent(idOrName)}`)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const data = await res.json()
  return data.host
}

export async function createInventoryHost(payload: InventoryHostPayload): Promise<InventoryHost> {
  const res = await authFetch('/api/inventory/hosts', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    throw new Error(data.detail || `HTTP ${res.status}`)
  }
  const data = await res.json()
  return data.host
}

export async function updateInventoryHost(
  idOrName: string,
  payload: InventoryHostPayload
): Promise<InventoryHost> {
  const res = await authFetch(`/api/inventory/hosts/${encodeURIComponent(idOrName)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    throw new Error(data.detail || `HTTP ${res.status}`)
  }
  const data = await res.json()
  return data.host
}

export async function deleteInventoryHost(idOrName: string): Promise<boolean> {
  const res = await authFetch(`/api/inventory/hosts/${encodeURIComponent(idOrName)}`, {
    method: 'DELETE',
  })
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    throw new Error(data.detail || `HTTP ${res.status}`)
  }
  return true
}

export async function testHostSSH(idOrName: string): Promise<SSHTestResult> {
  const res = await authFetch(`/api/inventory/hosts/${encodeURIComponent(idOrName)}/test-ssh`, {
    method: 'POST',
  })
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    throw new Error(data.detail || `HTTP ${res.status}`)
  }
  return res.json()
}

export async function exportInventoryYaml(): Promise<string> {
  const res = await authFetch('/api/inventory/export-yaml')
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const data = await res.json()
  return data.content || ''
}

export async function seedInventoryFromLegacy(): Promise<{ created: number; updated: number }> {
  const res = await authFetch('/api/inventory/seed', {
    method: 'POST',
  })
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const data = await res.json()
  return data.migration || { created: 0, updated: 0 }
}

export async function getSystemVersion(): Promise<SystemVersion> {
  return api.getVersion()
}

export async function getVersion(): Promise<SystemVersion> {
  return api.getVersion()
}




