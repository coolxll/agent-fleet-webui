export type TaskStatus = 'todo' | 'in_progress' | 'waiting' | 'review' | 'done'
export type TaskStage = {
  id: string; task_id: string; runtime_id: string | null; session_id: string | null
  source_session_id: string | null; host_name: string; agent_id: string
  native_session_id: string | null; project_path: string; kind: string; status: string
  output: string; error: string | null; created_at: string; updated_at: string
}
export type FleetTask = {
  id: string; title: string; goal: string; status: TaskStatus; project_path: string
  created_at: string; updated_at: string; stages: TaskStage[]
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
  const response = await fetch('/api/tasks' + path, {
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
