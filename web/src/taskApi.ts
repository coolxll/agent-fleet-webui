import { authFetch } from './api'

export type TaskStatus = 'todo' | 'in_progress' | 'waiting' | 'review' | 'done'
export type TaskStage = {
  id: string; task_id: string; active_task_id?: string | null; runtime_id: string | null; session_id: string | null
  source_session_id: string | null; host_name: string; agent_id: string
  native_session_id: string | null; project_path: string; kind: string; status: string
  output: string; error: string | null; created_at: string; updated_at: string
}

export type TaskRecoveryReasonCategory =
  | 'ssh_disconnected'
  | 'controller_restarted'
  | 'interactive_blocked'
  | 'process_failed'

export type TaskRecoveryInfo = {
  task_id: string
  task_title: string
  task_status: string
  stage_id: string | null
  stage_status: string | null
  target_host: string
  runtime_id: string | null
  agent_id: string | null
  project_path: string
  working_directory: string
  last_updated_at: string
  execution_phase: string
  reason_category: TaskRecoveryReasonCategory
  reason_message: string
  native_session_id: string | null
  session_id: string | null
  herdr_pane: Record<string, any> | null
  paseo_location: Record<string, any> | null
  session_locator: {
    native_session_id: string | null
    has_native_session: boolean
    session_id: string | null
    herdr_pane: Record<string, any> | null
    paseo_location: Record<string, any> | null
  }
  safety_check: {
    is_active: boolean
    safe_to_retry: boolean
    active_task_id: string | null
    active_stage_id: string | null
    message: string
  }
  is_process_active: boolean
  safe_to_retry: boolean
  can_resume: boolean
  can_retry: boolean
  is_recovery_needed?: boolean
}

export type FleetTask = {
  id: string; title: string; goal: string; status: TaskStatus; project_path: string
  created_at: string; updated_at: string; stages: TaskStage[]
  recovery_info?: TaskRecoveryInfo | null
}
export type TaskDraft = Pick<FleetTask, 'title' | 'goal' | 'project_path' | 'status'>
export type HandoffDraft = {
  runtime_id: string; agent_id: 'claude' | 'codex'; project_path: string
  source_session_id?: string | null; notes: string; session_mode: 'auto' | 'new'
}
export type HandoffPreview = {
  prompt: string; preview_hash: string; warnings: string[]; source_session_id: string | null
  resume_session_id: string | null; target_git: { exists: boolean; commit?: string | null; status?: string | null }
}

// --------------------------------------------------------------------------
// GitHub Pipeline Board Types
// --------------------------------------------------------------------------

export type GitHubLabel = {
  id?: string
  name: string
  color?: string
  description?: string
}

export type GitHubActionRun = {
  databaseId: number
  name: string
  status: string // completed, in_progress, queued
  conclusion: string | null // success, failure, cancelled, null
  headBranch: string
  event: string
  updatedAt: string
  createdAt?: string
  url: string
}

export type GitHubPullRequest = {
  number: number
  title: string
  state: 'OPEN' | 'CLOSED' | 'MERGED'
  headRefName: string
  baseRefName: string
  updatedAt: string
  createdAt: string
  url: string
  body?: string
  isDraft?: boolean
  linked_issue_numbers: number[]
  latest_action?: GitHubActionRun | null
}

export type GitHubIssue = {
  number: number
  title: string
  state: 'OPEN' | 'CLOSED'
  labels: GitHubLabel[]
  assignees?: { login: string }[]
  updatedAt: string
  createdAt: string
  url: string
  body?: string
  linked_prs: GitHubPullRequest[]
  latest_action?: GitHubActionRun | null
}

export type GitHubBoardResponse = {
  ok: boolean
  repo: string
  issues: GitHubIssue[]
  pull_requests: GitHubPullRequest[]
  actions: GitHubActionRun[]
  counts: {
    total_issues: number
    open_issues: number
    open_prs: number
    actions_count: number
  }
}

async function request<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await authFetch('/api/tasks' + path, {
    method, headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body)
  })
  const data = await response.json()
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail ?? data))
  return data as T
}

export const taskApi = {
  list: () => request<{ tasks: FleetTask[] }>(''),
  create: (draft: TaskDraft) => request<FleetTask>('', 'POST', draft),
  update: (id: string, draft: Partial<TaskDraft>) => request<FleetTask>('/' + id, 'PATCH', draft),
  link: (id: string, sessionId: string) => request<FleetTask>('/' + id + '/sessions', 'POST', { session_id: sessionId }),
  preview: (id: string, draft: HandoffDraft) => request<HandoffPreview>('/' + id + '/handoff/preview', 'POST', draft),
  handoff: (id: string, draft: HandoffDraft, hash: string) => request<FleetTask>('/' + id + '/handoff', 'POST', { ...draft, preview_hash: hash }),
  cancel: (id: string) => request<FleetTask>('/' + id + '/cancel', 'POST'),
  getRecoveryInfo: async (id: string): Promise<TaskRecoveryInfo | null> => {
    try {
      return await request<TaskRecoveryInfo>('/' + id + '/recovery')
    } catch {
      const task = await request<FleetTask>('/' + id)
      return task.recovery_info ?? null
    }
  },

  // GitHub Board APIs
  getGitHubBoard: (repo?: string) =>
    request<GitHubBoardResponse>('/github/board' + (repo ? `?repo=${encodeURIComponent(repo)}` : '')),
  createGitHubIssue: (payload: { title: string; body: string; labels?: string[]; repo?: string }) =>
    request<{ ok: boolean; url: string; number?: number }>('/github/issues', 'POST', payload),
  rerunGitHubAction: (runId: number | string, repo?: string) =>
    request<{ ok: boolean; message: string }>(
      `/github/actions/${runId}/rerun` + (repo ? `?repo=${encodeURIComponent(repo)}` : ''),
      'POST'
    ),
}
