import { useCallback, useEffect, useState } from 'react'
import {
  FleetTask,
  TaskDraft,
  TaskStatus,
  taskApi,
  GitHubBoardResponse,
  GitHubIssue,
  GitHubPullRequest,
} from '../taskApi'
import { useI18n, type TranslationKey } from '../i18n'
import './taskBoard.css'

const localColumns: { id: TaskStatus; labelKey: TranslationKey; fallbackZh: string; fallbackEn: string }[] = [
  { id: 'todo', labelKey: 'tasks.column.todo', fallbackZh: '待办', fallbackEn: 'To Do' },
  { id: 'in_progress', labelKey: 'tasks.column.in_progress', fallbackZh: '进行中', fallbackEn: 'In Progress' },
  { id: 'waiting', labelKey: 'tasks.column.waiting', fallbackZh: '等待我', fallbackEn: 'Waiting for Input' },
  { id: 'review', labelKey: 'tasks.column.review', fallbackZh: '待验收', fallbackEn: 'In Review' },
  { id: 'done', labelKey: 'tasks.column.done', fallbackZh: '已完成', fallbackEn: 'Completed' },
]

const getExecutionLabel = (
  status: string,
  t: (key: TranslationKey) => string,
  zh: boolean
): string => {
  const key = `tasks.exec.${status}` as TranslationKey
  try {
    const val = t(key)
    if (val && val !== key) return val
  } catch {
    // fallback
  }
  const fallbackZh: Record<string, string> = {
    unknown: '活动未知',
    starting: '正在连接',
    running: '执行中',
    completed: '本轮已结束',
    failed: '执行失败',
    interrupted: '执行已中断',
    blocked: '需要处理权限或输入',
  }
  const fallbackEn: Record<string, string> = {
    unknown: 'Unknown',
    starting: 'Connecting',
    running: 'Running',
    completed: 'Completed',
    failed: 'Failed',
    interrupted: 'Interrupted',
    blocked: 'Needs Input / Blocked',
  }
  return zh ? fallbackZh[status] || status : fallbackEn[status] || status
}

const empty: TaskDraft = { title: '', goal: '', project_path: '', status: 'todo' }

const ISSUE_TEMPLATE = `## 1. 问题与证据
当前行为、复现步骤/代码具体位置，区分既有事实与推测。

## 2. 唯一目标 / 交付物
完成后会改变什么，涉及的核心模块与 Owner。

## 3. 范围 / 非目标 (Out of Scope)
本次明确做与不做的事情。

## 4. 验收标准
1. 标准项 1
2. 标准项 2

## 5. 验证方式与风险
本地与 CI 自动化测试核验。

## 6. 关闭条件 / 依赖
达成何种具体证据即可关闭。`

export default function TaskBoardView({
  refreshSignal = 0,
  setToast,
}: {
  refreshSignal?: number
  setToast?: (message: string) => void
}) {
  const { t, language } = useI18n()
  const zh = language === 'zh'

  const [boardMode, setBoardMode] = useState<'github' | 'local'>('github')

  // GitHub Board States
  const [ghBoard, setGhBoard] = useState<GitHubBoardResponse | null>(null)
  const [ghLoading, setGhLoading] = useState(false)
  const [selectedIssue, setSelectedIssue] = useState<GitHubIssue | null>(null)
  const [selectedPr, setSelectedPr] = useState<GitHubPullRequest | null>(null)
  const [isNewIssueOpen, setIsNewIssueOpen] = useState(false)
  const [newIssueTitle, setNewIssueTitle] = useState('')
  const [newIssueLabels, setNewIssueLabels] = useState('feat')
  const [newIssueBody, setNewIssueBody] = useState(ISSUE_TEMPLATE)
  const [submittingIssue, setSubmittingIssue] = useState(false)
  const [rerunningRunId, setRerunningRunId] = useState<number | null>(null)

  // Local Task Board States
  const [tasks, setTasks] = useState<FleetTask[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [draft, setDraft] = useState<TaskDraft | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [copiedSession, setCopiedSession] = useState(false)
  const [confirmRetryOpen, setConfirmRetryOpen] = useState(false)
  const [retrying, setRetrying] = useState(false)

  // 1. Fetch GitHub Board
  const reloadGitHub = useCallback(async () => {
    try {
      const data = await taskApi.getGitHubBoard()
      setGhBoard(data)
    } catch (err: any) {
      console.warn('Failed to fetch github board:', err)
    }
  }, [])

  // 2. Fetch Local Tasks
  const reloadLocal = useCallback(async () => {
    const result = await taskApi.list()
    setTasks(result.tasks)
  }, [])

  useEffect(() => {
    if (boardMode === 'github') {
      setGhLoading(true)
      reloadGitHub().finally(() => setGhLoading(false))
    } else {
      reloadLocal()
    }
  }, [boardMode, reloadGitHub, reloadLocal, refreshSignal])

  // Periodic Polling
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (boardMode === 'github') {
        reloadGitHub()
      } else {
        reloadLocal()
      }
    }, 6000)
    return () => window.clearInterval(timer)
  }, [boardMode, reloadGitHub, reloadLocal])

  // Local task actions
  const selected = tasks.find((task) => task.id === selectedId)

  useEffect(() => {
    let alive = true
    if (boardMode === 'local') {
      taskApi.list()
        .then((work) => {
          if (!alive) return
          setTasks(work.tasks)
        })
        .catch((err) => {
          if (alive) setError(String(err.message ?? err))
        })
    }
    return () => {
      alive = false
    }
  }, [boardMode, refreshSignal])

  const run = async (action: () => Promise<unknown>, message?: string) => {
    setBusy(true)
    setError('')
    try {
      await action()
      await reloadLocal()
      if (message) setToast?.(message)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const openLocal = (task: FleetTask) => {
    setSelectedId(task.id)
    setDraft({
      title: task.title,
      goal: task.goal,
      status: task.status,
      project_path: task.project_path,
    })
    setError('')
    setConfirmRetryOpen(false)
    setCopiedSession(false)
  }

  const handleCopySessionId = async (sessionId: string) => {
    if (!sessionId) return
    try {
      await navigator.clipboard.writeText(sessionId)
      setCopiedSession(true)
      setTimeout(() => setCopiedSession(false), 2000)
      setToast?.(zh ? 'Session ID 已复制到剪贴板' : 'Session ID copied to clipboard')
    } catch {
      setToast?.(sessionId)
    }
  }

  const handleResetToTodo = async (taskId: string) => {
    await run(async () => {
      await taskApi.update(taskId, { status: 'todo' })
      if (draft) setDraft({ ...draft, status: 'todo' })
    }, zh ? '任务已重置为待办' : 'Task reset to To Do')
  }

  const handleExecuteRetry = async (
    taskId: string,
    runtimeId?: string | null,
    agentId?: string | null,
    projectPath?: string | null,
    resumeSessionId?: string | null
  ) => {
    setRetrying(true)
    setError('')
    try {
      const targetAgent = (agentId === 'claude' ? 'claude' : 'codex') as 'claude' | 'codex'
      if (runtimeId && projectPath) {
        const preview = await taskApi.preview(taskId, {
          runtime_id: runtimeId,
          agent_id: targetAgent,
          project_path: projectPath,
          notes: zh ? '从中断/阻塞状态恢复执行' : 'Resumed from interrupted/blocked state',
          session_mode: resumeSessionId ? 'auto' : 'new',
        })
        await taskApi.handoff(
          taskId,
          {
            runtime_id: runtimeId,
            agent_id: targetAgent,
            project_path: projectPath,
            notes: zh ? '从中断/阻塞状态恢复执行' : 'Resumed from interrupted/blocked state',
            session_mode: resumeSessionId ? 'auto' : 'new',
          },
          preview.preview_hash
        )
        setToast?.(zh ? '已成功重新派发任务执行' : 'Successfully re-dispatched task execution')
      } else {
        await taskApi.update(taskId, { status: 'in_progress' })
        if (draft) setDraft({ ...draft, status: 'in_progress' })
        setToast?.(zh ? '任务状态已更新为进行中' : 'Task updated to in progress')
      }
      setConfirmRetryOpen(false)
      await reloadLocal()
    } catch (err: any) {
      setError(String(err.message || err))
    } finally {
      setRetrying(false)
    }
  }

  const handleCreateGitHubIssue = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!newIssueTitle.trim()) return
    setSubmittingIssue(true)
    try {
      const labels = newIssueLabels
        .split(/[,，\s]+/)
        .map((s) => s.trim())
        .filter(Boolean)
      const res = await taskApi.createGitHubIssue({
        title: newIssueTitle.trim(),
        body: newIssueBody.trim(),
        labels,
      })
      setToast?.(zh ? `Issue #${res.number || ''} 创建成功` : `Created Issue #${res.number || ''}`)
      setIsNewIssueOpen(false)
      setNewIssueTitle('')
      setNewIssueBody(ISSUE_TEMPLATE)
      await reloadGitHub()
    } catch (err: any) {
      setError(String(err.message || err))
    } finally {
      setSubmittingIssue(false)
    }
  }

  const handleRerunAction = async (runId: number) => {
    setRerunningRunId(runId)
    try {
      await taskApi.rerunGitHubAction(runId)
      setToast?.(zh ? `已触发重新运行 Action #${runId}` : `Triggered rerun for Action #${runId}`)
      await reloadGitHub()
    } catch (err: any) {
      setError(String(err.message || err))
    } finally {
      setRerunningRunId(null)
    }
  }

  // Group GitHub Data into 5 Pipeline Columns
  const openIssues = (ghBoard?.issues || []).filter(
    (i) => i.state === 'OPEN' && (!i.linked_prs || i.linked_prs.length === 0)
  )

  const inProgressPRs = (ghBoard?.pull_requests || []).filter(
    (p) => p.state === 'OPEN' && (!p.latest_action || p.latest_action.status !== 'completed' || p.latest_action.conclusion !== 'success')
  )

  const recentActions = (ghBoard?.actions || []).slice(0, 15)

  const reviewPRs = (ghBoard?.pull_requests || []).filter(
    (p) => p.state === 'OPEN' && p.latest_action && p.latest_action.status === 'completed' && p.latest_action.conclusion === 'success'
  )

  const closedAndMerged = [
    ...(ghBoard?.pull_requests || []).filter((p) => p.state === 'MERGED').slice(0, 10).map((p) => ({ ...p, _type: 'pr' })),
    ...(ghBoard?.issues || []).filter((i) => i.state === 'CLOSED').slice(0, 10).map((i) => ({ ...i, _type: 'issue' })),
  ].sort((a: any, b: any) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime()).slice(0, 15)

  return (
    <div className="task-board">
      {/* 顶部标题与模式切换 */}
      <div className="task-board-heading">
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <h2 style={{ margin: 0 }}>
              {boardMode === 'github'
                ? zh
                  ? '🐙 GitHub 研发全生命周期看板'
                  : '🐙 GitHub Lifecycle Pipeline'
                : zh
                ? '💻 本地 Agent 执行任务看板'
                : '💻 Local Agent Tasks'}
            </h2>
            <div
              style={{
                display: 'inline-flex',
                background: 'var(--panel, #141b29)',
                border: '1px solid var(--border, #2b3546)',
                borderRadius: 8,
                padding: 2,
              }}
            >
              <button
                type="button"
                className={`btn small ${boardMode === 'github' ? 'primary' : ''}`}
                style={{ padding: '3px 10px', fontSize: 12 }}
                onClick={() => setBoardMode('github')}
              >
                🐙 GitHub 驱动
              </button>
              <button
                type="button"
                className={`btn small ${boardMode === 'local' ? 'primary' : ''}`}
                style={{ padding: '3px 10px', fontSize: 12 }}
                onClick={() => setBoardMode('local')}
              >
                💻 本地任务
              </button>
            </div>
          </div>
          <p style={{ margin: '6px 0 0' }}>
            {boardMode === 'github'
              ? zh
                ? `以 GitHub Issues 需求为源头，贯穿分支研发、自建 Runner CI 验证、PR Review 到合并闭环。`
                : 'Driven by GitHub Issues, pull requests, and GitHub Actions CI workflow runs.'
              : zh
              ? '一项任务关联多次 Agent 执行。任务进度由你管理，执行状态来自连接事件。'
              : 'Local persistent work stages with cross-agent handoff.'}
          </p>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          {boardMode === 'github' ? (
            <>
              <button
                type="button"
                className="btn small"
                onClick={() => reloadGitHub()}
                disabled={ghLoading}
                title="刷新 GitHub 数据"
              >
                🔄 {ghLoading ? '刷新中...' : '刷新看板'}
              </button>
              <button
                type="button"
                className="btn primary"
                onClick={() => setIsNewIssueOpen(true)}
                style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
              >
                <span>➕</span>
                <span>提出新 Issue</span>
              </button>
            </>
          ) : (
            <button
              className="btn primary"
              onClick={() => {
                setSelectedId(null)
                setDraft({ ...empty })
                setError('')
              }}
            >
              ＋ 新建本地任务
            </button>
          )}
        </div>
      </div>

      {error && (
        <div className="task-error" role="alert" style={{ marginBottom: 14 }}>
          {error}
        </div>
      )}

      {/* 模式 1: GITHUB 流水线看板 */}
      {boardMode === 'github' && (
        <div>
          {/* 仓库信息与统计条 */}
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              padding: '8px 14px',
              background: 'var(--panel, #141b29)',
              border: '1px solid var(--border, #2b3546)',
              borderRadius: 8,
              marginBottom: 16,
              fontSize: 12,
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <span className="small muted">{zh ? '当前目标仓库:' : 'Target Repository:'}</span>
              <span className="bold mono" style={{ color: '#58a6ff' }}>
                {ghBoard?.repo || 'coolxll/agent-fleet-webui'}
              </span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
              <span>
                📋 待办 Issue: <strong>{openIssues.length}</strong>
              </span>
              <span>
                🌿 开发中 PR: <strong>{inProgressPRs.length}</strong>
              </span>
              <span>
                👀 待评审: <strong>{reviewPRs.length}</strong>
              </span>
              <span>
                ⚡ 活跃 Action: <strong>{recentActions.length}</strong>
              </span>
            </div>
          </div>

          {/* 五列流水线看板 */}
          <div className="task-columns" style={{ gridTemplateColumns: 'repeat(5, minmax(230px, 1fr))' }}>
            {/* Column 1: Open Issues */}
            <section className="task-column">
              <h3>
                <span>📋 待办需求 / Issue</span>
                <span>{openIssues.length}</span>
              </h3>
              {openIssues.map((iss) => (
                <div
                  key={iss.number}
                  className="task-card"
                  style={{ cursor: 'pointer' }}
                  onClick={() => setSelectedIssue(iss)}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                    <span className="mono bold" style={{ color: '#58a6ff' }}>
                      #{iss.number}
                    </span>
                    <span className="small muted" style={{ fontSize: 10 }}>
                      {new Date(iss.createdAt).toLocaleDateString()}
                    </span>
                  </div>
                  <strong>{iss.title}</strong>
                  {iss.labels && iss.labels.length > 0 && (
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: 6 }}>
                      {iss.labels.map((l, i) => (
                        <span
                          key={i}
                          className="tag"
                          style={{
                            fontSize: 10,
                            padding: '1px 5px',
                            background: l.color ? `#${l.color}25` : 'rgba(255,255,255,0.05)',
                            borderColor: l.color ? `#${l.color}80` : 'var(--border)',
                          }}
                        >
                          {l.name}
                        </span>
                      ))}
                    </div>
                  )}
                  <div style={{ marginTop: 8, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <small className="muted">{zh ? '未挂载 PR' : 'No PR yet'}</small>
                    <a
                      href={iss.url}
                      target="_blank"
                      rel="noreferrer"
                      className="small"
                      style={{ fontSize: 11, color: '#58a6ff' }}
                      onClick={(e) => e.stopPropagation()}
                    >
                      GitHub ↗
                    </a>
                  </div>
                </div>
              ))}
              {openIssues.length === 0 && <div className="task-empty">{zh ? '暂无待办 Issue' : 'No open issues'}</div>}
            </section>

            {/* Column 2: In Progress / Branches */}
            <section className="task-column">
              <h3>
                <span>🌿 进行中 / 分支研发</span>
                <span>{inProgressPRs.length}</span>
              </h3>
              {inProgressPRs.map((pr) => (
                <div
                  key={pr.number}
                  className="task-card"
                  style={{ cursor: 'pointer' }}
                  onClick={() => setSelectedPr(pr)}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                    <span className="mono bold" style={{ color: '#a371f7' }}>
                      PR #{pr.number}
                    </span>
                    {pr.isDraft && <span className="tag small" style={{ fontSize: 10 }}>Draft</span>}
                  </div>
                  <strong>{pr.title}</strong>
                  <div className="mono small" style={{ fontSize: 11, color: 'var(--muted)', marginTop: 4 }}>
                    {pr.headRefName}
                  </div>
                  {pr.latest_action && (
                    <div style={{ marginTop: 8 }}>
                      <span
                        className={'tag ' + (pr.latest_action.conclusion === 'success' ? 'ok' : pr.latest_action.status === 'in_progress' ? 'unknown' : 'bad')}
                        style={{ fontSize: 10, padding: '1px 6px' }}
                      >
                        {pr.latest_action.status === 'in_progress'
                          ? '⏳ CI 运行中...'
                          : pr.latest_action.conclusion === 'success'
                          ? '✓ CI Passed'
                          : '✕ CI Failed'}
                      </span>
                    </div>
                  )}
                  <div style={{ marginTop: 8, textAlign: 'right' }}>
                    <a
                      href={pr.url}
                      target="_blank"
                      rel="noreferrer"
                      className="small"
                      style={{ fontSize: 11, color: '#58a6ff' }}
                      onClick={(e) => e.stopPropagation()}
                    >
                      PR 页面 ↗
                    </a>
                  </div>
                </div>
              ))}
              {inProgressPRs.length === 0 && <div className="task-empty">{zh ? '无进行中 PR' : 'No active PRs'}</div>}
            </section>

            {/* Column 3: CI & Actions */}
            <section className="task-column">
              <h3>
                <span>⚡ CI & Actions 状态</span>
                <span>{recentActions.length}</span>
              </h3>
              {recentActions.map((act) => {
                const isSuccess = act.conclusion === 'success'
                const isRunning = act.status === 'in_progress' || act.status === 'queued'
                const isFail = act.conclusion === 'failure'
                return (
                  <div key={act.databaseId} className="task-card" style={{ padding: '10px 12px' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <span
                        className={'tag ' + (isSuccess ? 'ok' : isRunning ? 'unknown' : 'bad')}
                        style={{ fontSize: 10, padding: '1px 6px' }}
                      >
                        {isRunning ? '⏳ Running' : isSuccess ? '✓ Success' : '✕ Failed'}
                      </span>
                      <small className="muted" style={{ margin: 0 }}>
                        {new Date(act.updatedAt).toLocaleTimeString()}
                      </small>
                    </div>
                    <strong style={{ fontSize: 12, marginTop: 6, display: 'block' }}>
                      {act.name} ({act.event})
                    </strong>
                    <div className="mono small" style={{ fontSize: 11, color: 'var(--muted)', marginTop: 2 }}>
                      {act.headBranch}
                    </div>
                    <div style={{ marginTop: 8, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      {isFail && (
                        <button
                          type="button"
                          className="btn small"
                          style={{ fontSize: 10, padding: '1px 6px' }}
                          onClick={() => handleRerunAction(act.databaseId)}
                          disabled={rerunningRunId === act.databaseId}
                        >
                          {rerunningRunId === act.databaseId ? '重跑中...' : '🔄 重跑'}
                        </button>
                      )}
                      <a
                        href={act.url}
                        target="_blank"
                        rel="noreferrer"
                        className="small"
                        style={{ fontSize: 11, color: '#58a6ff', marginLeft: 'auto' }}
                      >
                        Run #{act.databaseId} ↗
                      </a>
                    </div>
                  </div>
                )
              })}
              {recentActions.length === 0 && <div className="task-empty">{zh ? '无最近 Actions' : 'No actions'}</div>}
            </section>

            {/* Column 4: Review / Ready to Merge */}
            <section className="task-column">
              <h3>
                <span>👀 待评审 / 验收</span>
                <span>{reviewPRs.length}</span>
              </h3>
              {reviewPRs.map((pr) => (
                <div
                  key={pr.number}
                  className="task-card"
                  style={{ borderLeft: '3px solid #3fb950', cursor: 'pointer' }}
                  onClick={() => setSelectedPr(pr)}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                    <span className="mono bold" style={{ color: '#3fb950' }}>
                      PR #{pr.number}
                    </span>
                    <span className="tag ok" style={{ fontSize: 10, padding: '1px 5px' }}>
                      ✓ CI 绿灯
                    </span>
                  </div>
                  <strong>{pr.title}</strong>
                  <div className="mono small" style={{ fontSize: 11, color: 'var(--muted)', marginTop: 4 }}>
                    {pr.headRefName} → {pr.baseRefName}
                  </div>
                  {pr.linked_issue_numbers && pr.linked_issue_numbers.length > 0 && (
                    <div style={{ marginTop: 6 }}>
                      {pr.linked_issue_numbers.map((n) => (
                        <span key={n} className="mono small" style={{ fontSize: 10, color: '#58a6ff' }}>
                          Closes #{n}{' '}
                        </span>
                      ))}
                    </div>
                  )}
                  <div style={{ marginTop: 8, textAlign: 'right' }}>
                    <a
                      href={pr.url}
                      target="_blank"
                      rel="noreferrer"
                      className="small"
                      style={{ fontSize: 11, color: '#58a6ff' }}
                      onClick={(e) => e.stopPropagation()}
                    >
                      前往验收合并 ↗
                    </a>
                  </div>
                </div>
              ))}
              {reviewPRs.length === 0 && <div className="task-empty">{zh ? '暂无待验收 PR' : 'No PRs awaiting review'}</div>}
            </section>

            {/* Column 5: Merged & Closed */}
            <section className="task-column">
              <h3>
                <span>✅ 已交付 / 闭环</span>
                <span>{closedAndMerged.length}</span>
              </h3>
              {closedAndMerged.map((item: any) => {
                const isPR = item._type === 'pr'
                return (
                  <div
                    key={`${item._type}-${item.number}`}
                    className="task-card"
                    style={{ opacity: 0.75, padding: '10px 12px' }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <span
                        className="mono bold"
                        style={{ color: isPR ? '#a371f7' : '#8b949e', fontSize: 12 }}
                      >
                        {isPR ? `PR #${item.number} (Merged)` : `Issue #${item.number} (Closed)`}
                      </span>
                      <small className="muted" style={{ margin: 0 }}>
                        {new Date(item.updatedAt).toLocaleDateString()}
                      </small>
                    </div>
                    <strong style={{ fontSize: 12, marginTop: 4, display: 'block' }}>
                      {item.title}
                    </strong>
                    <div style={{ marginTop: 6, textAlign: 'right' }}>
                      <a
                        href={item.url}
                        target="_blank"
                        rel="noreferrer"
                        className="small"
                        style={{ fontSize: 11, color: '#58a6ff' }}
                      >
                        查看 ↗
                      </a>
                    </div>
                  </div>
                )
              })}
              {closedAndMerged.length === 0 && <div className="task-empty">{zh ? '无交付记录' : 'No merged/closed items'}</div>}
            </section>
          </div>

          {/* 新建 Issue 模态框 */}
          {isNewIssueOpen && (
            <div className="task-modal-backdrop" onClick={() => setIsNewIssueOpen(false)}>
              <section
                className="task-modal"
                style={{ maxWidth: 680 }}
                role="dialog"
                onClick={(e) => e.stopPropagation()}
              >
                <div className="task-board-heading">
                  <h2>➕ 提出新 GitHub Issue (规范模板)</h2>
                  <button className="btn small" onClick={() => setIsNewIssueOpen(false)}>
                    ✕
                  </button>
                </div>
                <form onSubmit={handleCreateGitHubIssue}>
                  <label>
                    Issue 标题 *
                    <input
                      required
                      placeholder="feat(scope): 详细描述交付物"
                      value={newIssueTitle}
                      onChange={(e) => setNewIssueTitle(e.target.value)}
                    />
                  </label>
                  <label>
                    标签 (Labels，逗号分隔)
                    <input
                      placeholder="feat, enhancement"
                      value={newIssueLabels}
                      onChange={(e) => setNewIssueLabels(e.target.value)}
                    />
                  </label>
                  <label>
                    Issue 描述模板 (遵循 AGENTS.md 规范)
                    <textarea
                      rows={12}
                      className="mono"
                      style={{ fontSize: 12 }}
                      value={newIssueBody}
                      onChange={(e) => setNewIssueBody(e.target.value)}
                    />
                  </label>
                  <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 12 }}>
                    <button type="button" className="btn" onClick={() => setIsNewIssueOpen(false)}>
                      取消
                    </button>
                    <button
                      type="submit"
                      className="btn primary"
                      disabled={submittingIssue || !newIssueTitle.trim()}
                    >
                      {submittingIssue ? '提交中...' : '提交 Issue 到 GitHub'}
                    </button>
                  </div>
                </form>
              </section>
            </div>
          )}

          {/* Issue 详情弹窗 */}
          {selectedIssue && (
            <div className="task-modal-backdrop" onClick={() => setSelectedIssue(null)}>
              <section
                className="task-modal"
                style={{ maxWidth: 720 }}
                role="dialog"
                onClick={(e) => e.stopPropagation()}
              >
                <div className="task-board-heading">
                  <div>
                    <span className="mono bold" style={{ color: '#58a6ff' }}>
                      Issue #{selectedIssue.number}
                    </span>
                    <h2 style={{ marginTop: 4 }}>{selectedIssue.title}</h2>
                  </div>
                  <button className="btn small" onClick={() => setSelectedIssue(null)}>
                    ✕
                  </button>
                </div>

                <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
                  <span className={`tag ${selectedIssue.state === 'OPEN' ? 'ok' : 'unknown'}`}>
                    {selectedIssue.state}
                  </span>
                  {selectedIssue.labels.map((l, i) => (
                    <span key={i} className="tag">
                      {l.name}
                    </span>
                  ))}
                  <a
                    href={selectedIssue.url}
                    target="_blank"
                    rel="noreferrer"
                    className="btn small"
                    style={{ marginLeft: 'auto' }}
                  >
                    在 GitHub 打开 ↗
                  </a>
                </div>

                {selectedIssue.linked_prs && selectedIssue.linked_prs.length > 0 && (
                  <div
                    style={{
                      background: 'rgba(56, 189, 248, 0.08)',
                      border: '1px solid rgba(56, 189, 248, 0.2)',
                      borderRadius: 8,
                      padding: 10,
                      marginBottom: 14,
                    }}
                  >
                    <strong>🔗 关联 Pull Request:</strong>
                    {selectedIssue.linked_prs.map((p) => (
                      <div key={p.number} style={{ marginTop: 4, display: 'flex', alignItems: 'center', gap: 8 }}>
                        <span className="mono bold">PR #{p.number}</span>
                        <span>{p.title}</span>
                        <span className="mono small muted">({p.headRefName})</span>
                      </div>
                    ))}
                  </div>
                )}

                <div style={{ background: 'var(--bg, #0e1522)', padding: 14, borderRadius: 8, border: '1px solid var(--border)' }}>
                  <pre style={{ whiteSpace: 'pre-wrap', margin: 0, font: 'inherit', fontSize: 13, lineHeight: 1.6 }}>
                    {selectedIssue.body || '无详细描述'}
                  </pre>
                </div>
              </section>
            </div>
          )}

          {/* PR 详情弹窗 */}
          {selectedPr && (
            <div className="task-modal-backdrop" onClick={() => setSelectedPr(null)}>
              <section
                className="task-modal"
                style={{ maxWidth: 720 }}
                role="dialog"
                onClick={(e) => e.stopPropagation()}
              >
                <div className="task-board-heading">
                  <div>
                    <span className="mono bold" style={{ color: '#a371f7' }}>
                      PR #{selectedPr.number}
                    </span>
                    <h2 style={{ marginTop: 4 }}>{selectedPr.title}</h2>
                  </div>
                  <button className="btn small" onClick={() => setSelectedPr(null)}>
                    ✕
                  </button>
                </div>

                <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 12 }}>
                  <span className={`tag ${selectedPr.state === 'OPEN' ? 'ok' : 'unknown'}`}>
                    {selectedPr.state}
                  </span>
                  <span className="mono small">
                    {selectedPr.headRefName} → {selectedPr.baseRefName}
                  </span>
                  <a
                    href={selectedPr.url}
                    target="_blank"
                    rel="noreferrer"
                    className="btn small primary"
                    style={{ marginLeft: 'auto' }}
                  >
                    在 GitHub 审查 / 合并 ↗
                  </a>
                </div>

                {selectedPr.latest_action && (
                  <div
                    style={{
                      background: 'rgba(255, 255, 255, 0.04)',
                      border: '1px solid var(--border)',
                      borderRadius: 8,
                      padding: 10,
                      marginBottom: 14,
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'center',
                    }}
                  >
                    <div>
                      <strong>⚡ CI Action:</strong>{' '}
                      <span className="mono">{selectedPr.latest_action.name}</span> (
                      {selectedPr.latest_action.conclusion || selectedPr.latest_action.status})
                    </div>
                    <a
                      href={selectedPr.latest_action.url}
                      target="_blank"
                      rel="noreferrer"
                      className="small"
                      style={{ color: '#58a6ff' }}
                    >
                      查看 Action 日志 ↗
                    </a>
                  </div>
                )}

                <div style={{ background: 'var(--bg, #0e1522)', padding: 14, borderRadius: 8, border: '1px solid var(--border)' }}>
                  <pre style={{ whiteSpace: 'pre-wrap', margin: 0, font: 'inherit', fontSize: 13, lineHeight: 1.6 }}>
                    {selectedPr.body || '无详细描述'}
                  </pre>
                </div>
              </section>
            </div>
          )}
        </div>
      )}

      {/* 模式 2: 本地 AGENT 任务看板 */}
      {boardMode === 'local' && (
        <div className="task-columns">
          {localColumns.map((column) => (
            <section
              className="task-column"
              key={column.id}
              onDragOver={(event) => event.preventDefault()}
              onDrop={(event) => {
                event.preventDefault()
                const id = event.dataTransfer.getData('text/task-id')
                if (id && tasks.some((task) => task.id === id))
                  void run(() => taskApi.update(id, { status: column.id }))
              }}
            >
              <h3>
                {t(column.labelKey)}
                <span>{tasks.filter((task) => task.status === column.id).length}</span>
              </h3>
              {tasks
                .filter((task) => task.status === column.id)
                .map((task) => {
                  const latest = task.stages[task.stages.length - 1]
                  return (
                    <button
                      className={'task-card' + (selectedId === task.id ? ' selected' : '')}
                      key={task.id}
                      draggable={!busy}
                      onDragStart={(event) => event.dataTransfer.setData('text/task-id', task.id)}
                      onClick={() => openLocal(task)}
                    >
                      <strong>{task.title}</strong>
                      <p>{task.goal || (zh ? '尚未填写任务目标' : 'No task goal specified')}</p>
                      {task.project_path && <small title={task.project_path}>{task.project_path}</small>}
                      {latest ? (
                        <>
                          <div className="task-agent">
                            {latest.host_name} · {latest.agent_id}
                          </div>
                          <small>
                            {getExecutionLabel(latest.status, t, zh)} ·{' '}
                            {new Date(latest.updated_at).toLocaleString()}
                          </small>
                        </>
                      ) : (
                        <small>{zh ? '尚未关联 Agent 会话' : 'No associated agent session'}</small>
                      )}
                    </button>
                  )
                })}
              {!tasks.some((task) => task.status === column.id) && (
                <div className="task-empty">{zh ? '拖动卡片到这里' : 'Drag card here'}</div>
              )}
            </section>
          ))}
        </div>
      )}

      {/* 本地任务详情模态框 */}
      {draft && (
        <div
          className="task-modal-backdrop"
          onClick={() => {
            if (!busy) setDraft(null)
          }}
        >
          <section
            className="task-modal"
            role="dialog"
            aria-modal="true"
            aria-label={selected ? (zh ? '任务详情' : 'Task Details') : (zh ? '创建任务' : 'New Task')}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="task-board-heading">
              <h2>{selected ? (zh ? '任务详情' : 'Task Details') : (zh ? '新建任务' : 'New Task')}</h2>
              <button className="btn" disabled={busy} onClick={() => setDraft(null)}>
                {zh ? '关闭' : 'Close'}
              </button>
            </div>
            {error && (
              <div className="task-error" role="alert">
                {error}
              </div>
            )}

            {/* 恢复指引面板：当任务处于 interrupted, blocked, failed 或 waiting 状态时高亮展示 */}
            {(() => {
              if (!selected) return null
              const latestStage = selected.stages?.[selected.stages.length - 1]
              const recoveryInfo = selected.recovery_info
              const isRecoverable = Boolean(
                ['interrupted', 'blocked', 'failed', 'waiting'].includes(selected.status) ||
                ['interrupted', 'blocked', 'failed'].includes(latestStage?.status || '') ||
                recoveryInfo?.is_recovery_needed
              )
              if (!isRecoverable) return null

              const reasonCat =
                recoveryInfo?.reason_category ||
                (latestStage?.status === 'blocked' || selected.status === 'waiting'
                  ? 'interactive_blocked'
                  : (latestStage?.error || '').toLowerCase().includes('fleet restarted')
                  ? 'controller_restarted'
                  : (latestStage?.error || '').toLowerCase().includes('connection')
                  ? 'ssh_disconnected'
                  : 'process_failed')

              const execPhase = recoveryInfo?.execution_phase || latestStage?.status || selected.status || 'unknown'
              const reasonMessage =
                recoveryInfo?.reason_message ||
                latestStage?.error ||
                latestStage?.output ||
                (zh ? '无详细错误原因' : 'No failure details available')

              const targetHost = recoveryInfo?.target_host || latestStage?.host_name || ''
              const workingDir =
                recoveryInfo?.working_directory ||
                latestStage?.project_path ||
                selected.project_path ||
                ''
              const lastUpdated =
                recoveryInfo?.last_updated_at || latestStage?.updated_at || selected.updated_at
              const nativeSessionId =
                recoveryInfo?.native_session_id ?? latestStage?.native_session_id
              const hasNativeSession = Boolean(
                nativeSessionId &&
                nativeSessionId.trim() &&
                nativeSessionId.toLowerCase() !== 'none' &&
                nativeSessionId.toLowerCase() !== 'null'
              )
              const linkedSessionId = recoveryInfo?.session_id ?? latestStage?.session_id
              const herdrPane = recoveryInfo?.herdr_pane
              const paseoLoc = recoveryInfo?.paseo_location
              const isProcessActive = Boolean(
                recoveryInfo?.is_process_active || latestStage?.active_task_id
              )
              const safeToRetry = Boolean(
                recoveryInfo ? recoveryInfo.safe_to_retry : !latestStage?.active_task_id
              )

              const getReasonLabel = (cat: string) => {
                switch (cat) {
                  case 'ssh_disconnected':
                    return zh ? '🔌 SSH 连接中断' : '🔌 SSH Disconnected'
                  case 'controller_restarted':
                    return zh ? '🔄 控制器重启' : '🔄 Controller Restarted'
                  case 'interactive_blocked':
                    return zh ? '⏸️ 交互阻塞 / 等待用户' : '⏸️ Interactive Blocked'
                  case 'process_failed':
                    return zh ? '❌ 进程异常退出' : '❌ Process Failed'
                  default:
                    return zh ? '⚠️ 异常待恢复' : '⚠️ Interrupted'
                }
              }

              return (
                <div
                  className="task-recovery-panel"
                  style={{
                    marginBottom: 18,
                    padding: '16px 18px',
                    borderRadius: 10,
                    background:
                      reasonCat === 'interactive_blocked'
                        ? 'rgba(234, 179, 8, 0.09)'
                        : reasonCat === 'ssh_disconnected'
                        ? 'rgba(59, 130, 246, 0.09)'
                        : reasonCat === 'controller_restarted'
                        ? 'rgba(168, 85, 247, 0.09)'
                        : 'rgba(239, 68, 68, 0.09)',
                    border:
                      reasonCat === 'interactive_blocked'
                        ? '1px solid rgba(234, 179, 8, 0.4)'
                        : reasonCat === 'ssh_disconnected'
                        ? '1px solid rgba(59, 130, 246, 0.4)'
                        : reasonCat === 'controller_restarted'
                        ? '1px solid rgba(168, 85, 247, 0.4)'
                        : '1px solid rgba(239, 68, 68, 0.4)',
                  }}
                >
                  {/* Header */}
                  <div
                    style={{
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'center',
                      marginBottom: 12,
                      flexWrap: 'wrap',
                      gap: 8,
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <span style={{ fontSize: 16 }}>🛠️</span>
                      <strong style={{ fontSize: 14 }}>
                        {zh ? '任务恢复指引面板' : 'Task Recovery Guidance Panel'}
                      </strong>
                    </div>
                    <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                      <span
                        className="tag"
                        style={{
                          fontSize: 11,
                          padding: '2px 8px',
                          borderRadius: 4,
                          fontWeight: 600,
                          background:
                            reasonCat === 'interactive_blocked'
                              ? '#eab30825'
                              : reasonCat === 'ssh_disconnected'
                              ? '#3b82f625'
                              : reasonCat === 'controller_restarted'
                              ? '#a855f725'
                              : '#ef444425',
                          color:
                            reasonCat === 'interactive_blocked'
                              ? '#facc15'
                              : reasonCat === 'ssh_disconnected'
                              ? '#60a5fa'
                              : reasonCat === 'controller_restarted'
                              ? '#c084fc'
                              : '#f87171',
                        }}
                      >
                        {getReasonLabel(reasonCat)}
                      </span>
                      <span className="tag" style={{ fontSize: 11, padding: '2px 8px', borderRadius: 4 }}>
                        {zh ? `阶段: ${execPhase}` : `Phase: ${execPhase}`}
                      </span>
                    </div>
                  </div>

                  {/* Failure / Block Reason Box */}
                  <div
                    style={{
                      padding: '10px 12px',
                      borderRadius: 6,
                      background: 'rgba(0, 0, 0, 0.25)',
                      marginBottom: 12,
                      fontSize: 12,
                      lineHeight: 1.6,
                    }}
                  >
                    <div style={{ color: 'var(--muted, #8793a8)', fontSize: 11, marginBottom: 4 }}>
                      {zh ? '阻断 / 中断原因:' : 'Failure / Block Reason:'}
                    </div>
                    <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontFamily: 'monospace' }}>
                      {reasonMessage}
                    </div>
                  </div>

                  {/* Meta Attributes Grid */}
                  <div
                    style={{
                      display: 'grid',
                      gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
                      gap: 10,
                      fontSize: 12,
                      marginBottom: 14,
                    }}
                  >
                    <div style={{ padding: '8px 10px', background: 'rgba(0,0,0,0.15)', borderRadius: 6 }}>
                      <span className="muted" style={{ display: 'block', fontSize: 11, marginBottom: 2 }}>
                        {zh ? '🖥️ 目标主机 / Runtime' : '🖥️ Target Host / Runtime'}
                      </span>
                      <strong className="mono">{targetHost || (zh ? '未关联' : 'None')}</strong>
                    </div>
                    <div style={{ padding: '8px 10px', background: 'rgba(0,0,0,0.15)', borderRadius: 6 }}>
                      <span className="muted" style={{ display: 'block', fontSize: 11, marginBottom: 2 }}>
                        {zh ? '📁 工作目录' : '📁 Working Directory'}
                      </span>
                      <span className="mono" title={workingDir} style={{ wordBreak: 'break-all' }}>
                        {workingDir || (zh ? '未配置' : 'None')}
                      </span>
                    </div>
                    <div style={{ padding: '8px 10px', background: 'rgba(0,0,0,0.15)', borderRadius: 6 }}>
                      <span className="muted" style={{ display: 'block', fontSize: 11, marginBottom: 2 }}>
                        {zh ? '🕒 最后更新时间' : '🕒 Last Updated'}
                      </span>
                      <span>{lastUpdated ? new Date(lastUpdated).toLocaleString() : '-'}</span>
                    </div>
                  </div>

                  {/* Native Session Locator */}
                  <div
                    style={{
                      padding: '10px 12px',
                      background: 'rgba(0,0,0,0.2)',
                      borderRadius: 8,
                      marginBottom: 14,
                    }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                      <span style={{ fontWeight: 600, fontSize: 12 }}>
                        {zh ? '🔍 原生会话定位 (Native Session Locator)' : '🔍 Native Session Locator'}
                      </span>
                    </div>

                    {hasNativeSession ? (
                      <div
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'space-between',
                          gap: 10,
                          flexWrap: 'wrap',
                        }}
                      >
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                          <span className="muted" style={{ fontSize: 11 }}>Session ID:</span>
                          <code
                            style={{
                              background: '#0004',
                              padding: '2px 6px',
                              borderRadius: 4,
                              fontSize: 12,
                              color: '#58a6ff',
                            }}
                          >
                            {nativeSessionId}
                          </code>
                          {herdrPane && (
                            <span className="tag" style={{ fontSize: 10 }}>
                              Herdr: {herdrPane.pane_id || herdrPane.workspace_id}
                            </span>
                          )}
                          {paseoLoc && (
                            <span className="tag" style={{ fontSize: 10 }}>
                              Paseo: {paseoLoc.endpoint || paseoLoc.session_id}
                            </span>
                          )}
                        </div>
                        <div style={{ display: 'flex', gap: 8 }}>
                          <button
                            type="button"
                            className="btn small"
                            onClick={() => handleCopySessionId(nativeSessionId!)}
                            style={{ fontSize: 11, padding: '4px 10px' }}
                          >
                            {copiedSession ? (zh ? '✓ 已复制' : '✓ Copied') : (zh ? '📋 复制 Session ID' : '📋 Copy ID')}
                          </button>
                          {linkedSessionId && (
                            <button
                              type="button"
                              className="btn small"
                              onClick={() => {
                                window.location.hash = '#sessions'
                                setDraft(null)
                              }}
                              style={{ fontSize: 11, padding: '4px 10px' }}
                              title={zh ? '前往会话视图查看完整会话' : 'Go to sessions view'}
                            >
                              {zh ? '👁️ 查看会话' : '👁️ View Session'}
                            </button>
                          )}
                        </div>
                      </div>
                    ) : (
                      <div
                        style={{
                          color: '#fbbf24',
                          fontSize: 12,
                          lineHeight: 1.5,
                          padding: '6px 0',
                        }}
                      >
                        {zh
                          ? '⚠️ 原生会话已终止或不可定位，历史上下文已脱离。请先核实目标工作区无遗留孤儿进程，确认工作区空闲后方可重新发起交接或重试。'
                          : '⚠️ Native session was terminated or unlocatable. Please confirm workspace has no orphaned processes before re-dispatching.'}
                      </div>
                    )}
                  </div>

                  {/* Safety Check Status */}
                  <div style={{ marginBottom: 14 }}>
                    {isProcessActive ? (
                      <div
                        style={{
                          padding: '10px 12px',
                          borderRadius: 6,
                          background: '#ef444418',
                          border: '1px solid #ef444440',
                          color: '#f87171',
                          fontSize: 12,
                          display: 'flex',
                          alignItems: 'center',
                          gap: 8,
                        }}
                      >
                        <span>🚫</span>
                        <span>
                          {zh
                            ? `工作区冲突警报: 目标工作区当前有活跃执行进程（${recoveryInfo?.safety_check?.active_task_id || '当前任务'}）。为防止并发执行与冲突，禁止重复派发。`
                            : `Workspace Conflict: An active execution is running on this workspace. Duplicate dispatch is disabled.`}
                        </span>
                      </div>
                    ) : (
                      <div
                        style={{
                          fontSize: 12,
                          color: '#4ade80',
                          display: 'flex',
                          alignItems: 'center',
                          gap: 6,
                        }}
                      >
                        <span>✅</span>
                        <span>
                          {zh
                            ? '工作区安全核查: 目标工作区处于空闲状态，无运行中冲突进程。'
                            : 'Workspace Safety: Target workspace is clear with no conflicting executions.'}
                        </span>
                      </div>
                    )}
                  </div>

                  {/* Concurrency Guard & Explicit Confirmation before Re-running */}
                  {safeToRetry && !isProcessActive && (
                    <div
                      style={{
                        paddingTop: 10,
                        borderTop: '1px solid rgba(255,255,255,0.08)',
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                        flexWrap: 'wrap',
                        gap: 10,
                      }}
                    >
                      {!confirmRetryOpen ? (
                        <button
                          type="button"
                          className="btn small primary"
                          disabled={busy || retrying}
                          onClick={() => setConfirmRetryOpen(true)}
                          style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
                        >
                          <span>🔄</span>
                          <span>
                            {hasNativeSession
                              ? (zh ? '恢复任务执行' : 'Resume Task')
                              : (zh ? '重新发起派发 / 重试' : 'Retry / Re-dispatch')}
                          </span>
                        </button>
                      ) : (
                        <div
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            gap: 10,
                            background: 'rgba(0,0,0,0.3)',
                            padding: '8px 12px',
                            borderRadius: 6,
                            border: '1px dashed #eab30880',
                            flexWrap: 'wrap',
                          }}
                        >
                          <span style={{ fontSize: 12, color: '#facc15' }}>
                            {zh
                              ? '⚠️ 确认目标工作区无冲突进程，继续重新派发？'
                              : '⚠️ Confirm workspace is clear and proceed with re-dispatch?'}
                          </span>
                          <button
                            type="button"
                            className="btn small primary"
                            disabled={busy || retrying}
                            onClick={() =>
                              handleExecuteRetry(
                                selected.id,
                                recoveryInfo?.runtime_id || latestStage?.runtime_id,
                                recoveryInfo?.agent_id || latestStage?.agent_id,
                                workingDir,
                                hasNativeSession ? nativeSessionId : null
                              )
                            }
                            style={{ fontSize: 11 }}
                          >
                            {retrying
                              ? (zh ? '派发中...' : 'Dispatching...')
                              : (zh ? '✓ 确认重新执行' : '✓ Confirm & Re-run')}
                          </button>
                          <button
                            type="button"
                            className="btn small"
                            disabled={busy || retrying}
                            onClick={() => setConfirmRetryOpen(false)}
                            style={{ fontSize: 11 }}
                          >
                            {zh ? '取消' : 'Cancel'}
                          </button>
                        </div>
                      )}

                      <button
                        type="button"
                        className="btn small"
                        disabled={busy || retrying}
                        onClick={() => handleResetToTodo(selected.id)}
                        title={zh ? '将任务状态重置为待办' : 'Reset task status to To Do'}
                      >
                        {zh ? '重置为待办' : 'Reset to Todo'}
                      </button>
                    </div>
                  )}
                </div>
              )
            })()}
            <form
              onSubmit={(event) => {
                event.preventDefault()
                void run(async () => {
                  if (selected) await taskApi.update(selected.id, draft)
                  else {
                    const task = await taskApi.create(draft)
                    setSelectedId(task.id)
                  }
                }, zh ? '任务已保存' : 'Task saved')
              }}
            >
              <label>
                {zh ? '标题' : 'Title'}
                <input
                  required
                  maxLength={200}
                  value={draft.title}
                  onChange={(event) => setDraft({ ...draft, title: event.target.value })}
                />
              </label>
              <label>
                {zh ? '任务目标' : 'Goal'}
                <textarea
                  maxLength={20000}
                  rows={3}
                  value={draft.goal}
                  onChange={(event) => setDraft({ ...draft, goal: event.target.value })}
                />
              </label>
              <div className="task-fields">
                <label>
                  {zh ? '项目目录' : 'Project Path'}
                  <input
                    value={draft.project_path}
                    onChange={(event) => setDraft({ ...draft, project_path: event.target.value })}
                  />
                </label>
                <label>
                  {zh ? '任务状态' : 'Status'}
                  <select
                    value={draft.status}
                    onChange={(event) => setDraft({ ...draft, status: event.target.value as TaskStatus })}
                  >
                    {localColumns.map((column) => (
                      <option key={column.id} value={column.id}>
                        {t(column.labelKey)}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              <button className="btn primary" disabled={busy || !draft.title.trim()} type="submit">
                {zh ? '保存任务' : 'Save Task'}
              </button>
            </form>

            {/* 阶段历史与执行记录 */}
            {selected && selected.stages && selected.stages.length > 0 && (
              <div style={{ marginTop: 20, borderTop: '1px solid var(--border, #2b3546)', paddingTop: 14 }}>
                <details>
                  <summary style={{ cursor: 'pointer', fontSize: 12, color: 'var(--muted, #8793a8)', marginBottom: 10 }}>
                    {zh ? `📋 查看历史执行阶段 (${selected.stages.length})` : `📋 View Execution Stages (${selected.stages.length})`}
                  </summary>
                  <div style={{ maxHeight: 220, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 8 }}>
                    {selected.stages.map((stage) => (
                      <div key={stage.id} className="task-stage" style={{ padding: '8px 10px', margin: 0 }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, marginBottom: 4 }}>
                          <strong>{stage.host_name} · {stage.agent_id}</strong>
                          <span className="muted">{getExecutionLabel(stage.status, t, zh)} · {new Date(stage.updated_at).toLocaleString()}</span>
                        </div>
                        {stage.native_session_id && (
                          <div style={{ fontSize: 11, color: 'var(--muted, #8793a8)' }}>
                            Session: <code className="mono">{stage.native_session_id}</code>
                          </div>
                        )}
                        {stage.error && (
                          <div style={{ color: '#f87171', fontSize: 11, marginTop: 4, fontFamily: 'monospace' }}>
                            {stage.error}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </details>
              </div>
            )}
          </section>
        </div>
      )}
    </div>
  )
}
