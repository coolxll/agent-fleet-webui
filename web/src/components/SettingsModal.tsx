import { useCallback, useEffect, useState } from 'react'
import { api, type Overview, type Runtime } from '../api'
import { useI18n } from '../i18n'

type Props = {
  isOpen: boolean
  onClose: () => void
  onDataChanged?: () => void
}

type TabKey = 'inventory' | 'schedule' | 'data'

export default function SettingsModal({ isOpen, onClose, onDataChanged }: Props) {
  const { t, language, toggleLanguage } = useI18n()
  const [activeTab, setActiveTab] = useState<TabKey>('inventory')
  const [hosts, setHosts] = useState<any[]>([])
  const [runtimes, setRuntimes] = useState<Runtime[]>([])
  const [overview, setOverview] = useState<Overview | null>(null)
  const [schedule, setSchedule] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const loadData = useCallback(async () => {
    try {
      const [hRes, rRes, ovRes, schRes] = await Promise.all([
        api.hosts(true),
        api.runtimes(false, true),
        api.overview().catch(() => null),
        fetch('/api/schedule').then((r) => r.json()).catch(() => null)
      ])
      setHosts(hRes.hosts)
      setRuntimes(rRes.runtimes)
      if (ovRes) setOverview(ovRes)
      if (schRes) setSchedule(schRes)
    } catch (e: any) {
      setError(String(e.message || e))
    }
  }, [])

  useEffect(() => {
    if (isOpen) {
      loadData()
      setMsg(null)
      setError(null)
    }
  }, [isOpen, loadData])

  const handleSyncInventory = async () => {
    setBusy(true)
    setMsg(null)
    setError(null)
    try {
      const res = await api.syncInventory()
      setMsg(t('toast.inventorySynced', { count: res.hosts_seen ?? 'ok' }))
      await loadData()
      onDataChanged?.()
    } catch (e: any) {
      setError(String(e.message || e))
    } finally {
      setBusy(false)
    }
  }

  const handleToggleHost = async (host: any) => {
    try {
      await api.setHostEnabled(host.id, !host.enabled)
      setHosts((prev) =>
        prev.map((h) => (h.id === host.id ? { ...h, enabled: !host.enabled } : h))
      )
      onDataChanged?.()
    } catch (e: any) {
      setError(String(e.message || e))
    }
  }

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
      const res = await fetch('/api/schedule', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch)
      })
      setSchedule(await res.json())
      setMsg(t('schedule.title') + ': ' + t('common.completed'))
    } catch (e: any) {
      setError(String(e.message || e))
    }
  }

  const handleExportJson = async () => {
    try {
      const res = await fetch('/api/export')
      const data = await res.json()
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
    }
  }

  if (!isOpen) return null

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal-card settings-modal-card"
        onClick={(e) => e.stopPropagation()}
        style={{ maxWidth: 680, maxHeight: '85vh', display: 'flex', flexDirection: 'column' }}
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
          {msg && (
            <div className="tag ok" style={{ display: 'block', padding: '6px 10px', marginBottom: 12 }}>
              {msg}
            </div>
          )}
          {error && <div className="err" style={{ marginBottom: 12 }}>{error}</div>}

          {activeTab === 'inventory' && (
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}>
                <div className="small muted">
                  {language === 'zh'
                    ? '同步并配置被纳管的主机与运行环境。已排除的主机不会参与全量扫描。'
                    : 'Manage machines and runtimes. Excluded hosts will not be scanned.'}
                </div>
                <button className="btn primary small" onClick={handleSyncInventory} disabled={busy}>
                  {busy ? t('common.loading') : `🔄 ${t('nav.resync')}`}
                </button>
              </div>

              <div className="settings-sub-panel">
                <div className="settings-sub-title">
                  {language === 'zh' ? '主机启用状态' : 'Hosts Inclusion'}
                </div>
                <table className="ts-table" style={{ fontSize: 12 }}>
                  <thead>
                    <tr>
                      <th>{t('overview.thHost')}</th>
                      <th>{language === 'zh' ? '来源' : 'Source'}</th>
                      <th>{language === 'zh' ? '环境数' : 'Runtimes'}</th>
                      <th style={{ textAlign: 'right' }}>{t('overview.thEnabled')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {hosts.map((h) => {
                      const hostRts = runtimes.filter((r) => r.host_id === h.id || r.host === h.name)
                      return (
                        <tr key={h.id}>
                          <td className="bold mono">{h.name}</td>
                          <td className="small muted">{h.source}</td>
                          <td className="small">{hostRts.length}</td>
                          <td style={{ textAlign: 'right' }}>
                            <button
                              className={'tag ' + (h.enabled ? 'ok' : 'unknown')}
                              style={{ cursor: 'pointer' }}
                              onClick={() => handleToggleHost(h)}
                            >
                              {h.enabled ? t('common.on') : t('common.off')}
                            </button>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>

              <div className="settings-sub-panel" style={{ marginTop: 14 }}>
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
              <div className="settings-sub-panel">
                <div className="settings-sub-title">
                  {language === 'zh' ? '数据导出' : 'Data Export'}
                </div>
                <p className="muted small" style={{ margin: '4px 0 10px' }}>
                  {language === 'zh'
                    ? '导出经过严格安全脱敏（不包含任何 Token、密钥或私有凭证）的 JSON 全机队数据快照。'
                    : 'Download a sanitized JSON export of the entire fleet.'}
                </p>
                <button className="btn primary small" onClick={handleExportJson} disabled={busy}>
                  ⬇️ {t('nav.exportJson')}
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
