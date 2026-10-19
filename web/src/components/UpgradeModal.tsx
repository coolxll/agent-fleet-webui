import { useEffect, useRef, useState } from 'react'
import AgentIcon, { agentLabel } from './AgentIcon'
import { api, getUpdateCommand, UpgradeTask } from '../api'
import { useI18n } from '../i18n'

export type UpgradeTarget = {
  agent: string
  currentVersion?: string | null
  latestVersion?: string | null
  host?: string | null
  runtimeKey?: string | null
  osName?: string | null
  packageName?: string | null
  releaseUrl?: string | null
  installationId?: string | null
  runtimeId?: string | null
}

type Props = {
  target: UpgradeTarget
  onClose: () => void
  onUpgraded?: (result: UpgradeTask) => void
  onSubmitted?: (task: UpgradeTask) => void
}

export function UpgradeIcon({
  size = 14,
  title,
  upgrading = false,
  onClick
}: {
  size?: number
  title?: string
  upgrading?: boolean
  onClick?: (e: React.MouseEvent) => void
}) {
  return (
    <span
      className={'tailscale-upgrade-btn ' + (upgrading ? 'upgrading' : '')}
      title={title || (upgrading ? 'Upgrading in background...' : 'Update available')}
      onClick={onClick}
      role={onClick ? 'button' : undefined}
      tabIndex={onClick ? 0 : undefined}
    >
      {upgrading ? (
        <svg width={size} height={size} viewBox="0 0 16 16" fill="none" className="spin-icon">
          <circle
            cx="8"
            cy="8"
            r="6"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeDasharray="22 10"
            strokeLinecap="round"
          />
        </svg>
      ) : (
        <svg width={size} height={size} viewBox="0 0 16 16" fill="none">
          <circle cx="8" cy="8" r="7" stroke="currentColor" strokeWidth="1.2" />
          <path
            d="M8 11.5V4.5M8 4.5L5 7.5M8 4.5L11 7.5"
            stroke="currentColor"
            strokeWidth="1.3"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      )}
    </span>
  )
}

export default function UpgradeModal({ target, onClose, onUpgraded, onSubmitted }: Props) {
  const { t, language } = useI18n()
  const [copiedType, setCopiedType] = useState<'remote' | 'local' | null>(null)
  const [tab, setTab] = useState<'remote' | 'local'>('remote')

  // Background task execution states
  const [upgrading, setUpgrading] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [upgradeTask, setUpgradeTask] = useState<UpgradeTask | null>(null)
  const [upgradeError, setUpgradeError] = useState<string | null>(null)
  const [showLogs, setShowLogs] = useState(false)
  const pollTimerRef = useRef<any>(null)

  const stopPolling = () => {
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current)
      pollTimerRef.current = null
    }
  }

  const pollTask = (taskId: string) => {
    stopPolling()
    pollTimerRef.current = setInterval(async () => {
      try {
        const task = await api.getUpgradeTask(taskId)
        setUpgradeTask(task)
        if (task.status === 'completed' || task.status === 'failed') {
          stopPolling()
          setUpgrading(false)
          if (task.ok) {
            onUpgraded?.(task)
          } else {
            setShowLogs(true)
          }
        }
      } catch {
        stopPolling()
        setUpgrading(false)
      }
    }, 1000)
  }

  // Check if an upgrade is already running or recently completed for this target
  useEffect(() => {
    if (!target.installationId) return
    let alive = true

    api
      .getActiveUpgradeForInstallation(target.installationId)
      .then((res) => {
        if (!alive) return
        if (res.active_task) {
          setUpgradeTask(res.active_task)
          if (res.active_task.status === 'running' || res.active_task.status === 'pending') {
            setUpgrading(true)
            pollTask(res.active_task.task_id)
          }
        }
      })
      .catch(() => {})

    return () => {
      alive = false
      stopPolling()
    }
  }, [target.installationId])

  const commands = getUpdateCommand(target.agent, target.packageName, target.host)

  const handleCopy = (text: string, type: 'remote' | 'local') => {
    navigator.clipboard.writeText(text)
    setCopiedType(type)
    setTimeout(() => {
      setCopiedType(null)
    }, 2000)
  }

  // Submit and immediately run in background (auto-closes modal)
  const handleSubmitInBackground = async () => {
    setSubmitting(true)
    setUpgradeError(null)
    try {
      let task: UpgradeTask
      if (target.installationId) {
        task = await api.upgradeInstallation(target.installationId)
      } else if (target.runtimeId) {
        task = await api.upgradeRuntimeAgent(target.runtimeId, target.agent)
      } else {
        throw new Error('No target installation ID available to trigger upgrade')
      }
      onSubmitted?.(task)
      onClose()
    } catch (err: any) {
      setUpgradeError(err.message || 'Failed to start upgrade task')
      setSubmitting(false)
      setShowLogs(true)
    }
  }

  // Submit and stay in modal to watch live console output
  const handleExecuteUpgradeAndWatch = async () => {
    setUpgrading(true)
    setUpgradeError(null)
    setUpgradeTask(null)
    setShowLogs(true)

    try {
      let task: UpgradeTask
      if (target.installationId) {
        task = await api.upgradeInstallation(target.installationId)
      } else if (target.runtimeId) {
        task = await api.upgradeRuntimeAgent(target.runtimeId, target.agent)
      } else {
        throw new Error('No target installation ID available to trigger upgrade')
      }

      setUpgradeTask(task)
      onSubmitted?.(task)

      if (task.status === 'completed' || task.status === 'failed') {
        setUpgrading(false)
        if (task.ok) {
          onUpgraded?.(task)
        } else {
          setShowLogs(true)
        }
      } else {
        pollTask(task.task_id)
      }
    } catch (err: any) {
      setUpgradeError(err.message || 'Failed to start upgrade task')
      setUpgrading(false)
      setShowLogs(true)
    }
  }

  const activeCmd = tab === 'remote' ? commands.remote : commands.local
  const canDirectUpgrade = Boolean(target.installationId || target.runtimeId)

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-card" onClick={(e) => e.stopPropagation()} style={{ maxWidth: 580 }}>
        <div className="modal-head">
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <AgentIcon agent={target.agent} size={24} />
            <div>
              <div style={{ fontWeight: 600, fontSize: 15 }}>
                {t('upgrade.title', { agent: agentLabel(target.agent) })}
              </div>
              <div className="muted small">
                {target.host || 'local'}
                {target.runtimeKey ? ` (${target.runtimeKey})` : ''}
                {target.osName ? ` · ${target.osName}` : ''}
              </div>
            </div>
          </div>
          <button className="btn small" onClick={onClose} style={{ padding: '3px 9px' }}>
            ✕
          </button>
        </div>

        <div className="modal-body">
          <div className="upgrade-version-compare">
            <div className="version-pill current">
              <span className="muted small">{t('upgrade.currentVersion')}</span>
              <span className="mono bold">
                {upgradeTask?.ok && upgradeTask.new_version
                  ? `v${upgradeTask.new_version}`
                  : target.currentVersion
                    ? `v${target.currentVersion}`
                    : '—'}
              </span>
            </div>
            <div className="version-arrow">➔</div>
            <div className="version-pill latest">
              <span className="muted small">{t('upgrade.latestAvailable')}</span>
              <span className="mono bold" style={{ color: 'var(--ok)' }}>
                {target.latestVersion ? `v${target.latestVersion}` : '—'}
              </span>
            </div>
          </div>

          {/* Primary Action: Direct WebUI Remote Execution in Background */}
          {canDirectUpgrade && (
            <div
              style={{
                margin: '16px 0',
                padding: '14px',
                background: 'var(--bg-subtle, rgba(255,255,255,0.03))',
                borderRadius: 8,
                border: '1px solid var(--border)'
              }}
            >
              <div style={{ marginBottom: 12 }}>
                <div style={{ fontWeight: 600, fontSize: 13, marginBottom: 2 }}>
                  {t('upgrade.executeBtn')}
                </div>
                <div className="muted small">
                  {language === 'zh'
                    ? '通过后台受控队列自动注入环境变量升级与重测，可一键挂后台'
                    : 'Dispatches safe upgrade over background queue with automatic environment bootstrap.'}
                </div>
              </div>

              {/* Action Buttons Row */}
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
                {!upgrading && !upgradeTask?.ok && (
                  <>
                    <button
                      className="btn primary"
                      onClick={handleSubmitInBackground}
                      disabled={submitting}
                      title={language === 'zh' ? '提交任务后立即自动关闭弹窗并在后台执行' : 'Submit task and auto-close modal'}
                      style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
                    >
                      {submitting ? (
                        <>
                          <span className="spinner small" /> {language === 'zh' ? '正在提交...' : 'Submitting...'}
                        </>
                      ) : (
                        t('upgrade.submitBackground')
                      )}
                    </button>

                    <button
                      className="btn small"
                      onClick={handleExecuteUpgradeAndWatch}
                      disabled={submitting}
                      title={language === 'zh' ? '停留在弹窗内查看实时输出日志' : 'Watch live console logs'}
                    >
                      {t('upgrade.watchLive')}
                    </button>
                  </>
                )}

                {upgrading && (
                  <button
                    className="btn primary small"
                    onClick={onClose}
                    title={language === 'zh' ? '转入后台继续运行并关闭弹窗' : 'Minimize and close modal'}
                    style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
                  >
                    <span>⚡ {t('upgrade.minimizeAndClose')}</span>
                  </button>
                )}

                {upgradeTask?.ok && (
                  <div style={{ color: 'var(--ok)', fontWeight: 600, fontSize: 13 }}>
                    ✓ {language === 'zh' ? '当前已升级为最新版本' : 'Up to date'}
                  </div>
                )}
              </div>

              {/* Running Status Badge */}
              {upgrading && (
                <div
                  style={{
                    marginTop: 12,
                    padding: '8px 12px',
                    borderRadius: 6,
                    background: 'rgba(56, 139, 253, 0.15)',
                    border: '1px solid rgba(56, 139, 253, 0.4)',
                    color: '#58a6ff',
                    fontSize: 12,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    gap: 8
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span className="spinner small" />
                    <span>
                      {language === 'zh'
                        ? '远程升级正在后台执行中，您可以随时关闭窗口...'
                        : 'Remote upgrade running in background, you can close this anytime...'}
                    </span>
                  </div>
                  <button
                    className="btn small"
                    onClick={onClose}
                    style={{ padding: '2px 8px', fontSize: 11 }}
                  >
                    {language === 'zh' ? '关闭窗口' : 'Close'}
                  </button>
                </div>
              )}

              {/* Success Banner */}
              {upgradeTask?.ok && (
                <div
                  style={{
                    marginTop: 12,
                    padding: '8px 12px',
                    borderRadius: 6,
                    background: 'rgba(46, 160, 67, 0.15)',
                    border: '1px solid rgba(46, 160, 67, 0.4)',
                    color: 'var(--ok, #3fb950)',
                    fontSize: 13,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between'
                  }}
                >
                  <span>
                    ✓ {t('upgrade.success')}{' '}
                    {upgradeTask.new_version ? `(v${upgradeTask.new_version})` : ''} ·{' '}
                    {(upgradeTask.duration_ms / 1000).toFixed(1)}s
                  </span>
                  <button
                    className="btn small"
                    onClick={() => setShowLogs(!showLogs)}
                    style={{ padding: '2px 8px', fontSize: 11 }}
                  >
                    {showLogs ? t('upgrade.hideLogs') : t('upgrade.showLogs')}
                  </button>
                </div>
              )}

              {/* Failure Banner */}
              {(upgradeError || (upgradeTask && upgradeTask.ok === false)) && (
                <div
                  style={{
                    marginTop: 12,
                    padding: '8px 12px',
                    borderRadius: 6,
                    background: 'rgba(248, 81, 73, 0.15)',
                    border: '1px solid rgba(248, 81, 73, 0.4)',
                    color: 'var(--danger, #f85149)',
                    fontSize: 13,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between'
                  }}
                >
                  <span>
                    ✕ {t('upgrade.failed')}:{' '}
                    {upgradeError ||
                      upgradeTask?.error ||
                      `Exit code ${upgradeTask?.exit_code}`}
                  </span>
                  <button
                    className="btn small"
                    onClick={() => setShowLogs(!showLogs)}
                    style={{ padding: '2px 8px', fontSize: 11 }}
                  >
                    {showLogs ? t('upgrade.hideLogs') : t('upgrade.showLogs')}
                  </button>
                </div>
              )}

              {/* Console Logs */}
              {showLogs && (upgradeTask || upgradeError) && (
                <div style={{ marginTop: 10 }}>
                  <pre
                    className="mono cmd-text"
                    style={{
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
                    {upgradeTask?.stdout ||
                      upgradeTask?.stderr ||
                      upgradeError ||
                      '(No output)'}
                  </pre>
                </div>
              )}
            </div>
          )}

          {/* Secondary / Fallback: Manual CLI Commands */}
          <details style={{ marginTop: 14 }} open={!canDirectUpgrade}>
            <summary
              style={{
                cursor: 'pointer',
                fontSize: 12,
                color: 'var(--muted)',
                marginBottom: 8,
                userSelect: 'none'
              }}
            >
              {t('upgrade.manualSection')}
            </summary>

            <div style={{ margin: '8px 0', display: 'flex', gap: 6 }}>
              <button
                className={'btn small ' + (tab === 'remote' ? 'primary' : '')}
                onClick={() => setTab('remote')}
              >
                {t('upgrade.tabRemote')}
              </button>
              <button
                className={'btn small ' + (tab === 'local' ? 'primary' : '')}
                onClick={() => setTab('local')}
              >
                {t('upgrade.tabLocal')}
              </button>
            </div>

            <div className="cmd-box">
              <pre className="mono cmd-text">{activeCmd}</pre>
              <button
                className="btn small"
                onClick={() => handleCopy(activeCmd, tab)}
                style={{ minWidth: 70 }}
              >
                {copiedType === tab ? t('common.copied') : t('common.copy')}
              </button>
            </div>
          </details>

          {target.packageName && (
            <div className="muted small" style={{ marginTop: 12 }}>
              {t('upgrade.packageLabel')}:{' '}
              <a
                href={
                  target.releaseUrl ||
                  `https://www.npmjs.com/package/${target.packageName}`
                }
                target="_blank"
                rel="noreferrer"
                style={{ color: 'var(--accent)', textDecoration: 'none' }}
              >
                {target.packageName}
              </a>
            </div>
          )}
        </div>

        <div className="modal-foot">
          <button className="btn" onClick={onClose}>
            {t('common.close')}
          </button>
        </div>
      </div>
    </div>
  )
}
