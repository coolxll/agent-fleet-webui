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
  cancel: (id: string) => request<FleetTask>('/' + id + '/cancel', 'POST')
}
