import { useCallback, useEffect, useState } from 'react'
import { api, Runtime, SessionSummary } from '../api'
import { FleetTask, HandoffDraft, HandoffPreview, TaskDraft, TaskStatus, taskApi } from '../taskApi'
import './taskBoard.css'

const columns: { id: TaskStatus; label: string }[] = [
  { id: 'todo', label: '待办' }, { id: 'in_progress', label: '进行中' },
  { id: 'waiting', label: '等待我' }, { id: 'review', label: '待验收' }, { id: 'done', label: '已完成' }
]
const executionLabel: Record<string, string> = {
  unknown: '活动未知', starting: '正在连接', running: '执行中', completed: '本轮已结束',
  failed: '执行失败', interrupted: '执行已中断', blocked: '需要处理权限或输入'
}
const empty: TaskDraft = { title: '', goal: '', project_path: '', status: 'todo' }
const initialHandoff: HandoffDraft = { runtime_id: '', agent_id: 'codex', project_path: '', notes: '', session_mode: 'auto' }

export default function TaskBoardView({ refreshSignal = 0, setToast }: {
  refreshSignal?: number; setToast?: (message: string) => void
}) {
  const [tasks, setTasks] = useState<FleetTask[]>([])
  const [runtimes, setRuntimes] = useState<Runtime[]>([])
  const [sessions, setSessions] = useState<SessionSummary[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [draft, setDraft] = useState<TaskDraft | null>(null)
  const [handoff, setHandoff] = useState<HandoffDraft>(initialHandoff)
  const [preview, setPreview] = useState<HandoffPreview | null>(null)
  const [sourceId, setSourceId] = useState('')
  const [sessionQuery, setSessionQuery] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const selected = tasks.find(task => task.id === selectedId)
  const active = selected?.stages.some(stage => stage.status === 'starting' || stage.status === 'running')
  const reload = useCallback(async () => {
    const result = await taskApi.list()
    setTasks(result.tasks)
  }, [])
  useEffect(() => {
    let alive = true
    Promise.all([taskApi.list(), api.runtimes(true), api.listSessions({ limit: 2000 })])
      .then(([work, machines, history]) => {
        if (!alive) return
        setTasks(work.tasks); setRuntimes(machines.runtimes); setSessions(history.items)
      }).catch(err => { if (alive) setError(String(err.message ?? err)) })
    return () => { alive = false }
  }, [refreshSignal])
  useEffect(() => {
    const timer = window.setInterval(() => { reload().catch(() => {}) }, 4000)
    return () => window.clearInterval(timer)
  }, [reload])
  const run = async (action: () => Promise<unknown>, message?: string) => {
    setBusy(true); setError('')
    try { await action(); await reload(); if (message) setToast?.(message) }
    catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setBusy(false) }
  }
  const open = (task: FleetTask) => {
    setSelectedId(task.id); setDraft({ title: task.title, goal: task.goal, status: task.status, project_path: task.project_path })
    const latest = task.stages[task.stages.length - 1]
    setHandoff({ ...initialHandoff, runtime_id: latest?.runtime_id ?? runtimes[0]?.id ?? '', project_path: latest?.project_path || task.project_path })
    setPreview(null); setSourceId(''); setError('')
  }
  const changeHandoff = (patch: Partial<HandoffDraft>) => { setHandoff(previous => ({ ...previous, ...patch })); setPreview(null) }
  const matchingSessions = sessions.filter(session => `${session.title} ${session.project_path} ${session.host_name} ${session.agent_id}`.toLowerCase().includes(sessionQuery.toLowerCase()))
  return <div className="task-board">
    <div className="task-board-heading"><div><h2>任务看板</h2><p>一项任务关联多次 Agent 执行。任务进度由你管理，执行状态来自连接事件。</p></div>
      <button className="btn primary" onClick={() => { setSelectedId(null); setDraft({ ...empty }); setPreview(null); setError('') }}>＋ 新建任务</button>
    </div>
    {error && <div className="task-error" role="alert">{error}</div>}
    <div className="task-columns">{columns.map(column => <section className="task-column" key={column.id} onDragOver={event => event.preventDefault()} onDrop={event => {
      event.preventDefault(); const id = event.dataTransfer.getData('text/task-id')
      if (id && tasks.some(task => task.id === id)) void run(() => taskApi.update(id, { status: column.id }))
    }}>
      <h3>{column.label}<span>{tasks.filter(task => task.status === column.id).length}</span></h3>
      {tasks.filter(task => task.status === column.id).map(task => {
        const latest = task.stages[task.stages.length - 1]
        return <button className={'task-card' + (selectedId === task.id ? ' selected' : '')} key={task.id} draggable={!busy}
          onDragStart={event => event.dataTransfer.setData('text/task-id', task.id)} onClick={() => open(task)}>
          <strong>{task.title}</strong><p>{task.goal || '尚未填写任务目标'}</p>
          {task.project_path && <small title={task.project_path}>{task.project_path}</small>}
          {latest ? <><div className="task-agent">{latest.host_name} · {latest.agent_id}</div><small>{executionLabel[latest.status] ?? latest.status} · {new Date(latest.updated_at).toLocaleString()}</small></> : <small>尚未关联 Agent 会话</small>}
        </button>
      })}
      {!tasks.some(task => task.status === column.id) && <div className="task-empty">拖动卡片到这里</div>}
    </section>)}</div>
    {draft && <div className="task-modal-backdrop" onClick={() => { if (!busy) setDraft(null) }}><section className="task-modal" role="dialog" aria-modal="true" aria-label={selected ? '任务详情' : '创建任务'} onClick={event => event.stopPropagation()}>
      <div className="task-board-heading"><h2>{selected ? '任务详情' : '新建任务'}</h2><button className="btn" disabled={busy} onClick={() => setDraft(null)}>关闭</button></div>
      {error && <div className="task-error" role="alert">{error}</div>}
      <form onSubmit={event => { event.preventDefault(); setPreview(null); void run(async () => {
        if (selected) await taskApi.update(selected.id, draft)
        else { const task = await taskApi.create(draft); setSelectedId(task.id); setHandoff({ ...initialHandoff, project_path: draft.project_path, runtime_id: runtimes[0]?.id ?? '' }) }
      }, '任务已保存') }}>
        <label>标题<input required maxLength={200} value={draft.title} onChange={event => setDraft({ ...draft, title: event.target.value })}/></label>
        <label>任务目标<textarea maxLength={20000} rows={3} value={draft.goal} onChange={event => setDraft({ ...draft, goal: event.target.value })}/></label>
        <div className="task-fields"><label>项目目录<input value={draft.project_path} onChange={event => setDraft({ ...draft, project_path: event.target.value })}/></label>
          <label>任务状态<select value={draft.status} onChange={event => setDraft({ ...draft, status: event.target.value as TaskStatus })}>{columns.map(column => <option key={column.id} value={column.id}>{column.label}</option>)}</select></label></div>
        <button className="btn primary" disabled={busy || !draft.title.trim()} type="submit">保存任务</button>
      </form>
      {selected && <>
        <hr/><h3>关联已有会话</h3>
        <div className="task-fields"><input aria-label="搜索会话" placeholder="搜索标题、机器或项目" value={sessionQuery} onChange={event => setSessionQuery(event.target.value)}/>
          <select aria-label="已有会话" value={sourceId} onChange={event => setSourceId(event.target.value)}><option value="">选择历史会话（活动状态未知）</option>{matchingSessions.slice(0, 200).map(session => <option key={session.id} value={session.id}>{session.host_name} · {session.agent_id} · {session.title}</option>)}</select>
          <button className="btn" disabled={busy || active || !sourceId} onClick={() => void run(async () => { await taskApi.link(selected.id, sourceId); setSourceId(''); setPreview(null) }, '会话已关联')}>关联</button></div>
        <hr/><h3>移交或继续执行</h3>
        <p className="task-help">从已保存目标和关联会话生成交接。目标目录须已准备好；请先确认外部 Agent 已停止写入。</p>
        <div className="task-fields"><label>目标机器<select value={handoff.runtime_id} onChange={event => changeHandoff({ runtime_id: event.target.value })}><option value="">选择运行环境</option>{runtimes.map(runtime => <option key={runtime.id} value={runtime.id} disabled={!runtime.enabled || !['local', 'ssh'].includes(runtime.transport) || runtime.kind === 'windows'}>{runtime.host} / {runtime.label || runtime.key}</option>)}</select></label>
          <label>目标 Agent<select value={handoff.agent_id} onChange={event => changeHandoff({ agent_id: event.target.value as 'claude' | 'codex' })}><option value="codex">Codex</option><option value="claude">Claude</option></select></label>
          <label>会话<select value={handoff.session_mode} onChange={event => changeHandoff({ session_mode: event.target.value as 'auto' | 'new' })}><option value="auto">优先恢复此前关联会话</option><option value="new">新建会话</option></select></label></div>
        <label>目标工作目录<input value={handoff.project_path} placeholder="/absolute/path/to/project" onChange={event => changeHandoff({ project_path: event.target.value })}/></label>
        <label>交接补充说明<textarea rows={3} maxLength={20000} value={handoff.notes} onChange={event => changeHandoff({ notes: event.target.value })}/></label>
        <div className="task-actions"><button className="btn" disabled={busy || active || !handoff.runtime_id || !handoff.project_path} onClick={() => void run(async () => setPreview(await taskApi.preview(selected.id, handoff)))}>预览交接</button>
          {active && <button className="btn" disabled={busy} onClick={() => void run(() => taskApi.cancel(selected.id), '执行已中断，原会话记录保留')}>停止本次受管执行</button>}</div>
        {preview && <div className="task-preview"><ul>{preview.warnings.map(warning => <li key={warning}>{warning}</li>)}</ul><pre>{preview.prompt}</pre>
          <p className="task-help">启动后会使用目标 Agent 的已登录账户执行任务。Claude 保留默认权限；Codex 使用工作目录沙箱。回合结束后仍需验收任务。</p>
          <button className="btn primary" disabled={busy || active} onClick={() => void run(async () => { await taskApi.handoff(selected.id, handoff, preview.preview_hash); setPreview(null) }, '交接已排队，连接状态将在卡片中更新')}>确认外部 Agent 已空闲，启动接续</button></div>}
        <hr/><h3>执行记录</h3>
        {selected.stages.length === 0 && <p className="task-help">尚无执行记录</p>}
        {[...selected.stages].reverse().map(stage => <article className="task-stage" key={stage.id}><strong>{stage.host_name} · {stage.agent_id}</strong><span>{executionLabel[stage.status] ?? stage.status}</span>
          <small>{stage.kind === 'linked' ? '关联历史会话' : '受管接续'} · {new Date(stage.created_at).toLocaleString()} · {stage.project_path}</small>
          {stage.native_session_id && <small>Session: {stage.native_session_id}</small>}
          {stage.error && <p className="task-error">{stage.error}</p>}
          {stage.output && <details open={stage.status === 'running'}><summary>Agent 输出（最近 60,000 字符）</summary><pre>{stage.output}</pre></details>}
        </article>)}
      </>}
    </section></div>}
  </div>
}
