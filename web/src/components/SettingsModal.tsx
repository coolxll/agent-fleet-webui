import { useCallback, useEffect, useState } from 'react'
import {
  api,
  type Overview,
  type Runtime,
  type SystemVersion,
  getApiToken,
  setApiToken,
  clearApiToken,
  verifyApiToken,
  AUTH_REQUIRED_EVENT,
} from '../api'
import { useI18n } from '../i18n'
import InventoryManager from './InventoryManager'

type Props = {
  isOpen: boolean
  onClose: () => void
  onDataChanged?: () => void
}

type TabKey = 'inventory' | 'schedule' | 'data'

export default function SettingsModal({ isOpen, onClose, onDataChanged }: Props) {
  const { t, language, toggleLanguage } = useI18n()
  const [activeTab, setActiveTab] = useState<TabKey>('inventory')
  const [runtimes, setRuntimes] = useState<Runtime[]>([])
  const [overview, setOverview] = useState<Overview | null>(null)
  const [schedule, setSchedule] = useState<any>(null)
  const [exporting, setExporting] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [authRequired, setAuthRequired] = useState(false)

  // API Token credentials state
  const [tokenInput, setTokenInput] = useState(() => getApiToken())
  const [showToken, setShowToken] = useState(false)
  const [tokenTesting, setTokenTesting] = useState(false)
  const [tokenStatusMsg, setTokenStatusMsg] = useState<{ type: 'ok' | 'err'; text: string } | null>(null)

  // Version info state
  const [sysVersion, setSysVersion] = useState<SystemVersion | null>(null)
  const [copiedCommit, setCopiedCommit] = useState(false)

  useEffect(() => {
    const handleAuthReq = () => {
      setAuthRequired(true)
    }
    window.addEventListener(AUTH_REQUIRED_EVENT, handleAuthReq)
    return () => window.removeEventListener(AUTH_REQUIRED_EVENT, handleAuthReq)
  }, [])

  const loadData = useCallback(async () => {
    try {
      const [rRes, ovRes, schRes, verRes] = await Promise.all([
        api.runtimes(false, true).catch((e: any) => {
          const errMsg = String(e.message || e)
          if (errMsg.includes('401') || errMsg.toLowerCase().includes('unauthorized')) {
            setAuthRequired(true)
          }
          throw e
        }),
        api.overview().catch(() => null),
        api.getSchedule().catch(() => null),
        api.getVersion().catch(() => null)
      ])
      setRuntimes(rRes.runtimes)
      if (ovRes) setOverview(ovRes)
      if (schRes) setSchedule(schRes)
      if (verRes) setSysVersion(verRes)
      setAuthRequired(false)
      setError(null)
    } catch (e: any) {
      const errMsg = String(e.message || e)
      if (errMsg.includes('401') || errMsg.toLowerCase().includes('unauthorized')) {
        setAuthRequired(true)
      }
      setError(errMsg)
    }
  }, [])

  useEffect(() => {
    if (isOpen) {
      setTokenInput(getApiToken())
      setTokenStatusMsg(null)
      loadData()
      setMsg(null)
      setError(null)
    }
  }, [isOpen, loadData])

  const handleToggleRuntime = async (rt: Runtime) => {
    try {
      await api.setRuntimeEnabled(rt.id, !rt.enabled)
      setRuntimes((prev) =>
        prev.map((r) => (r.id === rt.id ? { ...r, enabled: !rt.enabled } : r))
      )
      onDataChanged?.()
    } catch (e: any) {
      setError(String(e.message || e))
    }
  }

  const handleDeleteRuntime = async (rt: Runtime) => {
    if (!window.confirm(t('overview.confirmRemove', { host: rt.host }))) return
    try {
      await api.deleteRuntime(rt.id)
      setRuntimes((prev) => prev.filter((r) => r.id !== rt.id))
      onDataChanged?.()
    } catch (e: any) {
      setError(String(e.message || e))
    }
  }

  const handleSaveSchedule = async (patch: any) => {
    try {
      const data = await api.updateSchedule(patch)
      setSchedule(data)
      setMsg(t('schedule.title') + ': ' + t('common.completed'))
    } catch (e: any) {
      setError(String(e.message || e))
    }
  }

  const handleExportJson = async () => {
    setExporting(true)
    try {
      const data = await api.exportData()
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download =
        'agent-fleet-' + new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-') + '.json'
      a.click()
      URL.revokeObjectURL(url)
      setMsg(
        t('toast.exported', {
          hosts: data.counts?.hosts ?? data.counts?.runtimes ?? 0,
          installations: data.counts?.installations ?? 0
        })
      )
    } catch (e: any) {
      setError(String(e.message || e))
    } finally {
      setExporting(false)
    }
  }

  const handleSaveToken = async () => {
    const trimmed = tokenInput.trim()
    setApiToken(trimmed)
    setTokenInput(trimmed)
    setTokenStatusMsg(null)
    setMsg(t('auth.saved'))
    try {
      const res = await verifyApiToken(trimmed)
      if (res.valid) {
        setTokenStatusMsg({
          type: 'ok',
          text: res.authRequired ? t('auth.valid') : t('auth.notRequired')
        })
        setAuthRequired(false)
        setError(null)
        await loadData()
        onDataChanged?.()
      } else {
        setTokenStatusMsg({
          type: 'err',
          text: t('auth.invalid') + (res.message ? ` (${res.message})` : '')
        })
      }
    } catch (err: any) {
      setTokenStatusMsg({ type: 'err', text: String(err.message || err) })
    }
  }

  const handleTestToken = async () => {
    setTokenTesting(true)
    setTokenStatusMsg(null)
    try {
      const res = await verifyApiToken(tokenInput.trim())
      if (res.valid) {
        setTokenStatusMsg({
          type: 'ok',
          text: res.authRequired ? t('auth.valid') : t('auth.notRequired')
        })
        if (getApiToken() === tokenInput.trim()) {
          setAuthRequired(false)
          setError(null)
          await loadData()
          onDataChanged?.()
        }
      } else {
        setTokenStatusMsg({
          type: 'err',
          text: t('auth.invalid') + (res.message ? ` (${res.message})` : '')
        })
      }
    } catch (err: any) {
      setTokenStatusMsg({ type: 'err', text: String(err.message || err) })
    } finally {
      setTokenTesting(false)
    }
  }

  const handleClearToken = async () => {
    clearApiToken()
    setTokenInput('')
    setTokenStatusMsg({ type: 'ok', text: t('auth.cleared') })
    setMsg(t('auth.cleared'))
    try {
      await loadData()
      onDataChanged?.()
    } catch {
      // If auth is required, loadData will fail and set authRequired flag
    }
  }

  const handleCopyCommit = async (sha: string) => {
    try {
      await navigator.clipboard.writeText(sha)
      setCopiedCommit(true)
      setTimeout(() => setCopiedCommit(false), 2000)
    } catch {
      // ignore clipboard error
    }
  }

  if (!isOpen) return null

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal-card settings-modal-card"
        onClick={(e) => e.stopPropagation()}
        style={{
          maxWidth: activeTab === 'inventory' ? 980 : 680,
          width: '95vw',
          maxHeight: '88vh',
          display: 'flex',
          flexDirection: 'column',
          transition: 'max-width 0.2s ease',
        }}
      >
        <div className="modal-head">
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: 18 }}>⚙️</span>
            <span style={{ fontWeight: 600, fontSize: 16 }}>
              {language === 'zh' ? '管理设置' : 'Fleet Settings'}
            </span>
          </div>
          <button className="btn small" onClick={onClose} style={{ padding: '3px 9px' }}>
            ✕
          </button>
        </div>

        <div className="settings-nav-tabs">
          <button
            className={activeTab === 'inventory' ? 'active' : ''}
            onClick={() => setActiveTab('inventory')}
          >
            {language === 'zh' ? '主机资产纳管' : 'Inventory & Hosts'}
          </button>
          <button
            className={activeTab === 'schedule' ? 'active' : ''}
            onClick={() => setActiveTab('schedule')}
          >
            {t('schedule.title')}
          </button>
          <button
            className={activeTab === 'data' ? 'active' : ''}
            onClick={() => setActiveTab('data')}
          >
            {language === 'zh' ? '数据与偏好' : 'Data & Preferences'}
          </button>
        </div>

        <div className="modal-body" style={{ flex: 1, overflowY: 'auto' }}>
          {authRequired && (
            <div
              className="err"
              style={{
                marginBottom: 12,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                gap: 8,
                flexWrap: 'wrap',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span>🔒</span>
                <span>{t('auth.requiredNotice')}</span>
              </div>
              {activeTab !== 'data' && (
                <button
                  className="btn small primary"
                  onClick={() => setActiveTab('data')}
                  style={{ fontSize: 11, padding: '2px 8px' }}
                >
                  {t('auth.switchTabHint')} →
                </button>
              )}
            </div>
          )}
          {msg && (
            <div className="tag ok" style={{ display: 'block', padding: '6px 10px', marginBottom: 12 }}>
              {msg}
            </div>
          )}
          {error && !authRequired && <div className="err" style={{ marginBottom: 12 }}>{error}</div>}

          {activeTab === 'inventory' && (
            <div>
              <InventoryManager onDataChanged={onDataChanged} />

              <div className="settings-sub-panel" style={{ marginTop: 18 }}>
                <div className="settings-sub-title">
                  {language === 'zh' ? '独立环境与扫描开关' : 'Runtimes Control'}
                </div>
                <table className="ts-table" style={{ fontSize: 12 }}>
                  <thead>
                    <tr>
                      <th>{t('overview.thHost')}</th>
                      <th>{t('overview.thKind')}</th>
                      <th>{t('overview.thTransport')}</th>
                      <th style={{ textAlign: 'right' }}>{t('overview.thEnabled')}</th>
                      <th style={{ textAlign: 'right', width: 60 }}>{t('overview.thRemove')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {runtimes.map((r) => (
                      <tr key={r.id}>
                        <td className="mono">
                          {r.host}
                          {r.key && r.key !== 'native' && (
                            <span className="ts-tag" style={{ marginLeft: 6 }}>{r.key}</span>
                          )}
                        </td>
                        <td><span className="tag">{r.kind}</span></td>
                        <td className="small muted">{r.transport}</td>
                        <td style={{ textAlign: 'right' }}>
                          <button
                            className={'tag ' + (r.enabled ? 'ok' : 'unknown')}
                            style={{ cursor: 'pointer' }}
                            onClick={() => handleToggleRuntime(r)}
                          >
                            {r.enabled ? t('common.on') : t('common.off')}
                          </button>
                        </td>
                        <td style={{ textAlign: 'right' }}>
                          <button
                            className="tag bad"
                            style={{ cursor: 'pointer' }}
                            onClick={() => handleDeleteRuntime(r)}
                            title={t('overview.removeTitle')}
                          >
                            ✕
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {activeTab === 'schedule' && (
            <div>
              <div className="settings-sub-panel">
                <div className="settings-sub-title">{t('schedule.title')}</div>
                {schedule ? (
                  <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap', padding: '6px 0' }}>
                    <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
                      <input
                        type="checkbox"
                        checked={schedule.enabled}
                        onChange={(e) => handleSaveSchedule({ enabled: e.target.checked })}
                      />
                      <span>{t('schedule.every')}</span>
                    </label>
                    <select
                      value={schedule.interval_seconds}
                      onChange={(e) => handleSaveSchedule({ interval_seconds: Number(e.target.value) })}
                    >
                      <option value={600}>{t('schedule.10m')}</option>
                      <option value={1800}>{t('schedule.30m')}</option>
                      <option value={3600}>{t('schedule.1h')}</option>
                      <option value={21600}>{t('schedule.6h')}</option>
                      <option value={86400}>{t('schedule.24h')}</option>
                    </select>
                    <span className="muted small">
                      {schedule.running ? `● ${t('schedule.running')}` : `○ ${t('schedule.idle')}`}
                    </span>
                  </div>
                ) : (
                  <div className="muted small">{t('common.loading')}</div>
                )}
              </div>

              {overview && overview.recent_runs && (
                <div className="settings-sub-panel" style={{ marginTop: 14 }}>
                  <div className="settings-sub-title">{t('overview.recentRuns')}</div>
                  {overview.recent_runs.length === 0 ? (
                    <div className="muted small">{t('overview.noScans')}</div>
                  ) : (
                    <table className="ts-table" style={{ fontSize: 12 }}>
                      <thead>
                        <tr>
                          <th>{t('overview.thStarted')}</th>
                          <th>{t('overview.thStatus')}</th>
                          <th>{t('overview.thResult')}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {overview.recent_runs.map((r) => (
                          <tr key={r.id}>
                            <td className="small">{r.started ? r.started.slice(5, 19).replace('T', ' ') : '—'}</td>
                            <td>
                              <span
                                className={
                                  'tag ' +
                                  (r.status === 'completed'
                                    ? 'ok'
                                    : r.status === 'failed'
                                      ? 'bad'
                                      : 'warn')
                                }
                              >
                                {r.status}
                              </span>
                            </td>
                            <td className="small">
                              {t('overview.runResult', {
                                succeeded: r.succeeded,
                                failed: r.failed,
                                total: r.total
                              })}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </div>
              )}
            </div>
          )}

          {activeTab === 'data' && (
            <div>
              {/* API Access Token Section */}
              <div className="settings-sub-panel">
                <div
                  className="settings-sub-title"
                  style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}
                >
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                    <span>🔒</span>
                    <span>{t('auth.title')}</span>
                  </span>
                  <span
                    className={'tag ' + (getApiToken() ? 'ok' : 'unknown')}
                    style={{ fontSize: 11, fontWeight: 500 }}
                  >
                    {getApiToken() ? t('auth.statusConfigured') : t('auth.statusNone')}
                  </span>
                </div>
                <p className="muted small" style={{ margin: '6px 0 12px', lineHeight: 1.5 }}>
                  {t('auth.desc')}
                </p>

                <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: 8 }}>
                  <div style={{ position: 'relative', flex: 1, minWidth: 240 }}>
                    <input
                      type={showToken ? 'text' : 'password'}
                      value={tokenInput}
                      onChange={(e) => setTokenInput(e.target.value)}
                      placeholder={t('auth.placeholder')}
                      style={{
                        width: '100%',
                        paddingRight: 64,
                        fontFamily: 'monospace',
                        fontSize: 13,
                        boxSizing: 'border-box',
                      }}
                    />
                    <button
                      type="button"
                      className="btn small"
                      onClick={() => setShowToken(!showToken)}
                      style={{
                        position: 'absolute',
                        right: 4,
                        top: '50%',
                        transform: 'translateY(-50%)',
                        padding: '2px 8px',
                        fontSize: 11,
                        height: 24,
                      }}
                      title={showToken ? t('auth.hide') : t('auth.show')}
                    >
                      {showToken ? t('auth.hide') : t('auth.show')}
                    </button>
                  </div>

                  <button
                    className="btn primary small"
                    onClick={handleSaveToken}
                    title={t('auth.save')}
                  >
                    💾 {t('auth.save')}
                  </button>

                  <button
                    className="btn small"
                    onClick={handleTestToken}
                    disabled={tokenTesting}
                    title={t('auth.test')}
                  >
                    {tokenTesting ? t('auth.testing') : `🔍 ${t('auth.test')}`}
                  </button>

                  {getApiToken() && (
                    <button
                      className="btn small"
                      onClick={handleClearToken}
                      title={t('auth.clear')}
                      style={{ color: 'var(--err, #f85149)' }}
                    >
                      ✕ {t('auth.clear')}
                    </button>
                  )}
                </div>

                {tokenStatusMsg && (
                  <div
                    className={tokenStatusMsg.type === 'ok' ? 'tag ok' : 'err'}
                    style={{ display: 'block', padding: '6px 10px', marginTop: 8 }}
                  >
                    {tokenStatusMsg.text}
                  </div>
                )}
              </div>

              <div className="settings-sub-panel" style={{ marginTop: 14 }}>
                <div className="settings-sub-title">
                  {language === 'zh' ? '数据导出' : 'Data Export'}
                </div>
                <p className="muted small" style={{ margin: '4px 0 10px' }}>
                  {language === 'zh'
                    ? '导出经过严格安全脱敏（不包含任何 Token、密钥或私有凭证）的 JSON 全机队数据快照。'
                    : 'Download a sanitized JSON export of the entire fleet.'}
                </p>
                <button className="btn primary small" onClick={handleExportJson} disabled={exporting}>
                  ⬇️ {exporting ? (language === 'zh' ? '正在导出...' : 'Exporting...') : t('nav.exportJson')}
                </button>
              </div>

              <div className="settings-sub-panel" style={{ marginTop: 14 }}>
                <div className="settings-sub-title">
                  {language === 'zh' ? '语言偏好' : 'Language'}
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 8 }}>
                  <span className="small">{language === 'zh' ? '当前语言: 简体中文' : 'Current: English'}</span>
                  <button className="btn small" onClick={toggleLanguage}>
                    🌐 {t('nav.langSwitch')}
                  </button>
                </div>
              </div>

              {/* Application Version & Build Metadata Section */}
              <div className="settings-sub-panel" style={{ marginTop: 14 }}>
                <div
                  className="settings-sub-title"
                  style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}
                >
                  <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                    <span>🏷️</span>
                    <span>{t('version.title')}</span>
                  </span>
                  {sysVersion && (
                    <span
                      className={'tag ' + (sysVersion.is_release ? 'ok' : 'unknown')}
                      style={{ fontSize: 11, fontWeight: 500 }}
                    >
                      {sysVersion.is_release ? t('version.release') : t('version.development')}
                    </span>
                  )}
                </div>

                {sysVersion ? (
                  <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 8 }}>
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                      <span className="muted small">{t('version.systemVersion')}</span>
                      <span style={{ fontFamily: 'monospace', fontWeight: 600, fontSize: 13 }}>
                        v{sysVersion.version}
                      </span>
                    </div>

                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                      <span className="muted small">{t('version.commit')}</span>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        <code
                          style={{
                            fontSize: 12,
                            padding: '2px 6px',
                            background: 'var(--bg-subtle, rgba(255,255,255,0.06))',
                            borderRadius: 4,
                          }}
                          title={sysVersion.commit}
                        >
                          {sysVersion.commit_short}
                        </code>
                        {sysVersion.commit && sysVersion.commit !== 'dev' && (
                          <button
                            type="button"
                            className="btn small"
                            onClick={() => handleCopyCommit(sysVersion.commit)}
                            style={{ padding: '2px 8px', fontSize: 11, height: 22 }}
                            title={t('version.copyCommit')}
                          >
                            {copiedCommit ? `✓ ${t('version.commitCopied')}` : `📋 ${t('version.copyCommit')}`}
                          </button>
                        )}
                      </div>
                    </div>

                    {sysVersion.build_time && (
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                        <span className="muted small">{t('version.buildTime')}</span>
                        <span className="small muted" style={{ fontFamily: 'monospace' }}>
                          {sysVersion.build_time}
                        </span>
                      </div>
                    )}
                  </div>
                ) : (
                  <p className="muted small" style={{ margin: '6px 0 0' }}>
                    {t('common.loading')}
                  </p>
                )}
              </div>
            </div>
          )}
        </div>

        <div className="modal-foot" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <div className="small muted" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            {sysVersion && (
              <>
                <span>Agent Fleet v{sysVersion.version}</span>
                <span>•</span>
                <span style={{ fontFamily: 'monospace' }}>{sysVersion.commit_short}</span>
              </>
            )}
          </div>
          <button className="btn" onClick={onClose}>
            {t('common.close')}
          </button>
        </div>
      </div>
    </div>
  )
}
