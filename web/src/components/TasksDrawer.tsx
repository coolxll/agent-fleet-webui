import { useEffect, useState } from 'react'
import { api, type UpgradeTask } from '../api'
import AgentIcon, { agentLabel } from './AgentIcon'
import { useI18n } from '../i18n'

type Props = {
  isOpen: boolean
  onClose: () => void
  onDataChanged?: () => void
}

function formatAgo(timestampSec: number, t: any): string {
  if (!timestampSec) return ''
  const secs = Math.max(0, Math.floor(Date.now() / 1000 - timestampSec))
  if (secs < 60) return t('time.ago.s', { n: secs })
  if (secs < 3600) return t('time.ago.m', { n: Math.floor(secs / 60) })
  if (secs < 86400) return t('time.ago.h', { n: Math.floor(secs / 3600) })
  return t('time.ago.d', { n: Math.floor(secs / 86400) })
}

export default function TasksDrawer({ isOpen, onClose, onDataChanged }: Props) {
  const { t, language } = useI18n()
  const [tab, setTab] = useState<'all' | 'upgrades' | 'scans'>('all')
  const [upgrades, setUpgrades] = useState<UpgradeTask[]>([])
  const [scans, setScans] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [expandedLogs, setExpandedLogs] = useState<Record<string, boolean>>({})

  const loadData = async () => {
    try {
      const [uRes, sRes] = await Promise.all([
        api.listUpgradeTasks().catch(() => ({ tasks: [] })),
        api.scans().catch(() => ({ runs: [] }))
      ])
      setUpgrades(uRes.tasks || [])
      setScans(sRes.runs || [])
    } catch {
      /* ignore */
    }
  }

  useEffect(() => {
    if (!isOpen) return
    setLoading(true)
    loadData().finally(() => setLoading(false))

    // Polling every 2s while drawer is open
    const timer = setInterval(loadData, 2000)
    return () => clearInterval(timer)
  }, [isOpen])

  if (!isOpen) return null

  const activeUpgrades = upgrades.filter((u) => u.status === 'running' || u.status === 'pending')
  const activeScans = scans.filter((s) => s.status === 'running')
  const totalActive = activeUpgrades.length + activeScans.length

  const toggleLog = (id: string) => {
    setExpandedLogs((prev) => ({ ...prev, [id]: !prev[id] }))
  }

  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <div className="drawer-panel" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 680 }}>
        {/* Drawer Header */}
        <div className="drawer-head">
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ fontSize: 18 }}>📋</span>
            <div>
              <div style={{ fontWeight: 600, fontSize: 15, display: 'flex', alignItems: 'center', gap: 8 }}>
                <span>{language === 'zh' ? '后台任务中心' : 'Task Center'}</span>
                {totalActive > 0 && (
                  <span
                    className="tag blue"
                    style={{ fontSize: 11, padding: '1px 8px', display: 'inline-flex', alignItems: 'center', gap: 4 }}
                  >
                    <span className="spinner small" />
                    <span>{totalActive} {language === 'zh' ? '个运行中' : 'running'}</span>
                  </span>
                )}
              </div>
              <div className="muted small">
                {language === 'zh'
                  ? '查看远程升级、资产探测等后台异步任务进度与执行日志'
                  : 'Track remote upgrades, inventory scans, and execution logs'}
              </div>
            </div>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <button
              className="btn small"
              disabled={loading}
              onClick={() => {
                setLoading(true)
                loadData().finally(() => setLoading(false))
                onDataChanged?.()
              }}
              title={language === 'zh' ? '刷新' : 'Refresh'}
            >
              {loading ? '⏳' : '🔄'}
            </button>
            <button className="btn small" onClick={onClose} style={{ padding: '3px 9px' }}>
              ✕
            </button>
          </div>
        </div>

        {/* Tab Filters */}
        <div style={{ padding: '10px 18px 0', borderBottom: '1px solid var(--border)', background: 'var(--panel)', display: 'flex', gap: 6 }}>
          <button
            className={'btn small ' + (tab === 'all' ? 'primary' : '')}
            onClick={() => setTab('all')}
            style={{ borderRadius: '6px 6px 0 0', borderBottom: 'none' }}
          >
            {language === 'zh' ? '全部任务' : 'All Tasks'} ({upgrades.length + scans.length})
          </button>
          <button
            className={'btn small ' + (tab === 'upgrades' ? 'primary' : '')}
            onClick={() => setTab('upgrades')}
            style={{ borderRadius: '6px 6px 0 0', borderBottom: 'none' }}
          >
            ⚡ {language === 'zh' ? '升级任务' : 'Upgrades'} ({upgrades.length})
          </button>
          <button
            className={'btn small ' + (tab === 'scans' ? 'primary' : '')}
            onClick={() => setTab('scans')}
            style={{ borderRadius: '6px 6px 0 0', borderBottom: 'none' }}
          >
            🔄 {language === 'zh' ? '扫描任务' : 'Scans'} ({scans.length})
          </button>
        </div>

        {/* Drawer Body */}
        <div className="drawer-body" style={{ padding: 18, overflowY: 'auto' }}>
          {/* Upgrade Tasks List */}
          {(tab === 'all' || tab === 'upgrades') && upgrades.length > 0 && (
            <div style={{ marginBottom: 20 }}>
              <div style={{ fontWeight: 600, fontSize: 13, textTransform: 'uppercase', color: 'var(--muted)', marginBottom: 10, letterSpacing: 0.5 }}>
                {language === 'zh' ? '远程升级任务' : 'UPGRADE TASKS'}
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                {upgrades.map((u) => {
                  const isRunning = u.status === 'running' || u.status === 'pending'
                  const isSuccess = u.status === 'completed' && u.ok
                  const isFailed = u.status === 'failed' || (u.status === 'completed' && u.ok === false)
                  const hasLog = Boolean(u.stdout || u.stderr || u.error)
                  const isLogOpen = expandedLogs[u.task_id]

                  return (
                    <div
                      key={u.task_id}
                      style={{
                        padding: 12,
                        borderRadius: 8,
                        background: 'var(--panel)',
                        border: '1px solid ' + (isRunning ? 'rgba(56, 139, 253, 0.4)' : isSuccess ? 'rgba(46, 160, 67, 0.3)' : isFailed ? 'rgba(248, 81, 73, 0.3)' : 'var(--border)'),
                        boxShadow: isRunning ? '0 0 10px rgba(56, 139, 253, 0.1)' : undefined
                      }}
                    >
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                          <AgentIcon agent={u.agent} size={22} />
                          <div>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                              <span style={{ fontWeight: 600, fontSize: 14 }}>{agentLabel(u.agent)}</span>
                              <span className="mono bold" style={{ fontSize: 12 }}>
                                {u.old_version ? `v${u.old_version}` : ''}
                                {u.new_version && u.new_version !== u.old_version ? ` ➔ v${u.new_version}` : ''}
                              </span>
                            </div>
                            <div className="muted small">
                              🖥️ {u.host} · {formatAgo(u.started_at, t)}
                              {u.duration_ms ? ` · ${(u.duration_ms / 1000).toFixed(1)}s` : ''}
                            </div>
                          </div>
                        </div>

                        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                          {isRunning && (
                            <span className="tag blue" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                              <span className="spinner small" />
                              <span>{language === 'zh' ? '正在执行' : 'Running'}</span>
                            </span>
                          )}
                          {isSuccess && (
                            <span className="tag ok">
                              ✓ {language === 'zh' ? '升级成功' : 'Success'}
                            </span>
                          )}
                          {isFailed && (
                            <span className="tag error">
                              ✕ {language === 'zh' ? '失败' : 'Failed'} {u.exit_code !== null ? `(code ${u.exit_code})` : ''}
                            </span>
                          )}
                        </div>
                      </div>

                      {/* Error Preview */}
                      {isFailed && u.error && (
                        <div style={{ marginTop: 8, fontSize: 12, color: 'var(--danger)', background: 'rgba(248, 81, 73, 0.1)', padding: '6px 10px', borderRadius: 4 }}>
                          {u.error}
                        </div>
                      )}

                      {/* Log Toggle Button */}
                      {hasLog && (
                        <div style={{ marginTop: 8, display: 'flex', justifyContent: 'flex-end' }}>
                          <button
                            className="btn small"
                            onClick={() => toggleLog(u.task_id)}
                            style={{ padding: '2px 8px', fontSize: 11 }}
                          >
                            {isLogOpen ? (language === 'zh' ? '收起控制台日志' : 'Hide Logs') : (language === 'zh' ? '查看控制台日志' : 'View Logs')}
                          </button>
                        </div>
                      )}

                      {/* Log Output Box */}
                      {isLogOpen && (
                        <pre
                          className="mono cmd-text"
                          style={{
                            marginTop: 8,
                            maxHeight: 180,
                            overflowY: 'auto',
                            fontSize: 11,
                            lineHeight: 1.4,
                            background: '#0d1117',
                            color: '#c9d1d9',
                            padding: 8,
                            borderRadius: 4
                          }}
                        >
                          {u.stdout || u.stderr || u.error || '(No output)'}
                        </pre>
                      )}
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {/* Scan Runs List */}
          {(tab === 'all' || tab === 'scans') && scans.length > 0 && (
            <div>
              <div style={{ fontWeight: 600, fontSize: 13, textTransform: 'uppercase', color: 'var(--muted)', marginBottom: 10, letterSpacing: 0.5 }}>
                {language === 'zh' ? '资产探测扫描任务' : 'FLEET SCANS'}
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                {scans.slice(0, 10).map((s) => {
                  const isRunning = s.status === 'running'
                  const total = s.total || 0
                  const ok = s.succeeded ?? s.ok ?? 0
                  const failed = s.failed ?? 0

                  return (
                    <div
                      key={s.id}
                      style={{
                        padding: 12,
                        borderRadius: 8,
                        background: 'var(--panel)',
                        border: '1px solid var(--border)'
                      }}
                    >
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <div>
                          <div style={{ fontWeight: 600, fontSize: 13.5 }}>
                            🔄 {language === 'zh' ? '集群扫描' : 'Fleet Scan'} ({s.triggered_by || 'manual'})
                          </div>
                          <div className="muted small">
                            {formatAgo(new Date(s.started_at).getTime() / 1000, t)} · 目标: {total} 台
                          </div>
                        </div>

                        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                          {isRunning ? (
                            <span className="tag blue" style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                              <span className="spinner small" />
                              <span>{language === 'zh' ? '扫描中' : 'Scanning'}</span>
                            </span>
                          ) : failed > 0 ? (
                            <span className="tag unknown">
                              {ok} 成功 / {failed} 失败
                            </span>
                          ) : (
                            <span className="tag ok">
                              ✓ {ok} 台成功
                            </span>
                          )}
                        </div>
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {/* Empty State */}
          {upgrades.length === 0 && scans.length === 0 && (
            <div style={{ textAlign: 'center', padding: '40px 20px', color: 'var(--muted)' }}>
              <div style={{ fontSize: 24, marginBottom: 8 }}>📭</div>
              <div>{language === 'zh' ? '暂无后台任务记录' : 'No background tasks'}</div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
