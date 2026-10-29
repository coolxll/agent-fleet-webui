import { authFetch } from './api'

export type PaseoSession = {
  source: 'paseo'
  runtime_id: string
  external_id: string
  provider: string | null
  provider_label: string | null
  provider_session_id: null
  title: string | null
  cwd: string | null
  status: string
}

export type PaseoObservation = {
  runtime_id: string
  host_id: string
  host: string
  runtime_key: string
  enabled: boolean
  status: string
  installed: boolean | null
  version: string | null
  cli_version: string | null
  latest_version?: string | null
  upgrade_available?: boolean
  package?: string | null
  release_url?: string | null
  checked_at: string | null
  last_success_at: string | null
  stale: boolean
  error_code: string | null
  error: string | null
  connection: { transport: string; endpoint: string | null; server_id: string | null }
  sessions: PaseoSession[]
  sessions_status: string
  sessions_stale: boolean
  sessions_checked_at: string | null
  sessions_success_at: string | null
}

export type HerdrWorkspace = {
  workspace_id: string | null
  label: string | null
  agent_status: string | null
  pane_count: number
  tab_count: number
  focused: boolean
}

export type HerdrSession = {
  source: 'herdr'
  runtime_id: string
  external_id: string
  workspace_id: string | null
  tab_id: string | null
  provider: string | null
  provider_label: string | null
  provider_session_id: string | null
  title: string | null
  cwd: string | null
  status: string
}

export type HerdrObservation = {
  runtime_id: string
  host_id: string
  host: string
  runtime_key: string
  enabled: boolean
  status: string
  installed: boolean | null
  version: string | null
  cli_version: string | null
  latest_version?: string | null
  upgrade_available?: boolean
  package?: string | null
  release_url?: string | null
  checked_at: string | null
  last_success_at: string | null
  stale: boolean
  error_code: string | null
  error: string | null
  connection: { transport: string; socket: string | null; protocol: string | null; channel: string | null }
  workspaces: HerdrWorkspace[]
  sessions: HerdrSession[]
  sessions_status: string
  sessions_stale: boolean
  sessions_checked_at: string | null
  sessions_success_at: string | null
}

export type DaemonsResponse = {
  paseo: PaseoObservation[]
  herdr: HerdrObservation[]
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await authFetch('/api' + path, init)
  if (!res.ok) throw new Error(`Daemon request failed (${res.status})`)
  return res.json()
}

export const getDaemons = (runtimeId?: string) =>
  request<DaemonsResponse>('/daemons' + (runtimeId ? `?runtime_id=${encodeURIComponent(runtimeId)}` : ''))

export const refreshDaemons = (runtimeIds?: string[], services?: string[]) =>
  request<DaemonsResponse>('/daemons/refresh', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ runtime_ids: runtimeIds, services }),
  })

export const getPaseo = (runtimeId?: string) =>
  request<{ items: PaseoObservation[] }>('/paseo' + (runtimeId ? `?runtime_id=${encodeURIComponent(runtimeId)}` : ''))

export const refreshPaseo = (runtimeIds?: string[]) =>
  request<{ items: PaseoObservation[] }>('/paseo/refresh', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ runtime_ids: runtimeIds }),
  })

export const getHerdr = (runtimeId?: string) =>
  request<{ items: HerdrObservation[] }>('/herdr' + (runtimeId ? `?runtime_id=${encodeURIComponent(runtimeId)}` : ''))

export const refreshHerdr = (runtimeIds?: string[]) =>
  request<{ items: HerdrObservation[] }>('/herdr/refresh', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ runtime_ids: runtimeIds }),
  })
