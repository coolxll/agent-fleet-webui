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
  runtimes: () => get<{ runtimes: Runtime[]; stale_after_s: number }>('/runtimes'),
  installations: (params: { runtime_id?: string; agent_id?: string; detected_only?: boolean }) => {
    const q = new URLSearchParams()
    if (params.runtime_id) q.set('runtime_id', params.runtime_id)
    if (params.agent_id) q.set('agent_id', params.agent_id)
    if (params.detected_only) q.set('detected_only', 'true')
    const suffix = q.toString() ? '?' + q.toString() : ''
    return get<{ installations: Installation[] }>('/installations' + suffix)
  },
  installation: (id: string) => get<InstallationDetail>('/installations/' + id),
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
      }[]
      include_disabled: boolean
    }>('/hosts?include_disabled=' + (includeDisabled ? 'true' : 'false')),
  syncInventory: () => post<{ ok: boolean; hosts_seen?: number }>('/inventory/sync'),
  setHostEnabled: (id: string, enabled: boolean) =>
    post<{ ok: boolean; host?: string }>('/hosts/' + id + '/enabled', { enabled }),
  deleteRuntime: (id: string) =>
    fetch('/api/runtimes/' + id, { method: 'DELETE' }).then(async (r) => {
      if (!r.ok) throw new Error((await r.text()) || 'delete failed')
      return (await r.json()) as { ok: boolean; deleted: string }
    })
}
