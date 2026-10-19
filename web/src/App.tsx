import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import MachinesView from './views/MachinesView'
import MachineView from './views/MachineView'
import AgentsView from './views/AgentsView'
import AgentDetail from './components/AgentDetail'
import { agentLabel } from './components/AgentIcon'
import Matrix from './views/Matrix'
import Diff from './views/Diff'
import Overview from './views/Overview'
import { useI18n } from './i18n'

type Route =
  | { name: 'fleet' }
  | { name: 'agents'; agentId?: string }
  | { name: 'machine'; hostId: string; hostName?: string; from?: Route }
  | { name: 'agent'; installationId: string; agentName?: string; hostName?: string; from?: Route }
  | { name: 'compare' }
  | { name: 'matrix' }
  | { name: 'overview' }

type ScheduleState = {
  enabled: boolean
  interval_seconds: number
  running: boolean
  last_run_id: string | null
  last_run_at: string | null
  skipped_because_busy: number
}

export default function App() {
  const { t, language, toggleLanguage } = useI18n()
  const [route, setRoute] = useState<Route>({ name: 'fleet' })
  const [history, setHistory] = useState<Route[]>([])
  const [runId, setRunId] = useState<string | null>(null)
  const [progress, setProgress] = useState<{
    done: number
    total: number
    ok: number
    failed: number
  } | null>(null)
  const [busy, setBusy] = useState(false)
  const [toast, setToast] = useState<string | null>(null)
  const [schedule, setSchedule] = useState<ScheduleState | null>(null)
  const [showSchedule, setShowSchedule] = useState(false)

  // poll an in-flight scan and refresh when it lands
  const [refreshKey, setRefreshKey] = useState(0)

  const navigateTo = useCallback((nextRoute: Route) => {
    setHistory((prev) => [...prev, route])
    setRoute(nextRoute)
  }, [route])

  const switchTab = useCallback((nextRoute: Route) => {
    setHistory([])
    setRoute(nextRoute)
  }, [])

  const goBack = useCallback(() => {
    if (history.length > 0) {
      const prev = history[history.length - 1]
      setHistory((h) => h.slice(0, -1))
      setRoute(prev)
    } else {
      if (route.name === 'agent') {
        if (route.from) {
          setRoute(route.from)
        } else if (route.hostName || (route as any).hostId) {
          setRoute({ name: 'machine', hostId: (route as any).hostId || route.hostName || '' })
        } else {
          setRoute({ name: 'fleet' })
        }
      } else if (route.name === 'machine') {
        if (route.from) {
          setRoute(route.from)
        } else {
          setRoute({ name: 'fleet' })
        }
      } else if (route.name === 'agents' && route.agentId) {
        setRoute({ name: 'agents' })
      } else {
        setRoute({ name: 'fleet' })
      }
    }
  }, [history, route])

  useEffect(() => {
    fetch('/api/schedule')
      .then((r) => r.json())
      .then((s: ScheduleState) => setSchedule(s))
      .catch(() => undefined)
  }, [])

  useEffect(() => {
    if (!runId) {
      setProgress(null)
      return
    }
    let alive = true
    const poll = async () => {
      try {
        const run = await api.scan(runId)
        if (!alive) return
        setProgress({
          done: run.live.done ?? run.succeeded + run.failed,
          total: run.total,
          ok: run.live.ok ?? run.succeeded,
          failed: run.live.failed ?? run.failed
        })
        if (run.status !== 'running') {
          setRunId(null)
          setProgress(null)
          setRefreshKey((k) => k + 1)
          setToast(t('toast.scanFinished', { ok: run.succeeded, failed: run.failed }))
        }
      } catch {
        /* keep trying */
      }
    }
    poll()
    const tInterval = setInterval(poll, 1500)
    return () => {
      alive = false
      clearInterval(tInterval)
    }
  }, [runId, t])

  const scanRuntime = useCallback(async (id: string) => {
    setBusy(true)
    try {
      const res = await api.startScan([id])
      setRunId(res.run_id)
      setToast(t('toast.scanningOne'))
    } catch (e: any) {
      setToast(String(e.message || e))
    } finally {
      setBusy(false)
    }
  }, [t])

  const scanAll = useCallback(async () => {
    setBusy(true)
    try {
      const res = await api.startScan(null)
      setRunId(res.run_id)
      setToast(t('toast.scanningCount', { count: res.total }))
    } catch (e: any) {
      setToast(String(e.message || e))
    } finally {
      setBusy(false)
    }
  }, [t])

  const sync = useCallback(async () => {
    setBusy(true)
    try {
      const res = await api.syncInventory()
      setToast(t('toast.inventorySynced', { count: res.hosts_seen ?? 'ok' }))
      setRefreshKey((k) => k + 1)
    } catch (e: any) {
      setToast(String(e.message || e))
    } finally {
      setBusy(false)
    }
  }, [t])

  const saveSchedule = useCallback(async (patch: any) => {
    try {
      const res = await fetch('/api/schedule', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch)
      })
      setSchedule(await res.json())
    } catch (e: any) {
      setToast(String(e.message || e))
    }
  }, [])

  const exportJson = useCallback(async () => {
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
      setToast(
        t('toast.exported', {
          hosts: data.counts?.hosts ?? data.counts?.runtimes ?? 0,
          installations: data.counts?.installations ?? 0
        })
      )
    } catch (e: any) {
      setToast(String(e.message || e))
    }
  }, [t])

  useEffect(() => {
    if (!toast) return
    const timer = setTimeout(() => setToast(null), 4000)
    return () => clearTimeout(timer)
  }, [toast])

  const isDrilledDown =
    route.name === 'machine' ||
    route.name === 'agent' ||
    (route.name === 'agents' && !!route.agentId)
  const canGoBack = isDrilledDown || history.length > 0

  const crumbs: { label: string; onClick?: () => void; active?: boolean }[] = []

  if (route.name === 'fleet') {
    crumbs.push({ label: t('nav.machines'), active: true })
  } else if (route.name === 'machine') {
    crumbs.push({ label: t('nav.machines'), onClick: () => switchTab({ name: 'fleet' }) })
    crumbs.push({ label: route.hostName || route.hostId, active: true })
  } else if (route.name === 'agents') {
    if (route.agentId) {
      crumbs.push({ label: t('nav.agents'), onClick: () => navigateTo({ name: 'agents' }) })
      crumbs.push({ label: agentLabel(route.agentId), active: true })
    } else {
      crumbs.push({ label: t('nav.agents'), active: true })
    }
  } else if (route.name === 'agent') {
    if (route.from?.name === 'agents') {
      const fromAgent = route.from
      crumbs.push({ label: t('nav.agents'), onClick: () => switchTab({ name: 'agents' }) })
      if (fromAgent.agentId) {
        crumbs.push({
          label: agentLabel(fromAgent.agentId),
          onClick: () => navigateTo({ name: 'agents', agentId: fromAgent.agentId })
        })
      }
      crumbs.push({
        label: (route.agentName ? `${route.agentName} @ ` : '') + (route.hostName || t('nav.agent')),
        active: true
      })
    } else if (route.from?.name === 'matrix') {
      crumbs.push({ label: t('nav.matrix'), onClick: () => switchTab({ name: 'matrix' }) })
      crumbs.push({
        label: (route.agentName ? `${route.agentName} @ ` : '') + (route.hostName || t('nav.agent')),
        active: true
      })
    } else if (route.from?.name === 'machine') {
      const fromMachine = route.from
      crumbs.push({ label: t('nav.machines'), onClick: () => switchTab({ name: 'fleet' }) })
      crumbs.push({
        label: route.hostName || fromMachine.hostId,
        onClick: () =>
          navigateTo({
            name: 'machine',
            hostId: fromMachine.hostId,
            hostName: route.hostName
          })
      })
      crumbs.push({ label: route.agentName || t('nav.agent'), active: true })
    } else {
      crumbs.push({ label: t('nav.machines'), onClick: () => switchTab({ name: 'fleet' }) })
      crumbs.push({
        label: (route.agentName ? `${route.agentName} @ ` : '') + (route.hostName || t('nav.agent')),
        active: true
      })
    }
  } else if (route.name === 'overview') {
    crumbs.push({ label: t('nav.overview'), active: true })
  } else if (route.name === 'matrix') {
    crumbs.push({ label: t('nav.matrix'), active: true })
  } else if (route.name === 'compare') {
    crumbs.push({ label: t('nav.compare'), active: true })
  }

  return (
    <div>
      <div className="app-header">
        <div className="crumbs">
          {canGoBack && (
            <button className="back-btn" onClick={goBack} title={t('common.back')}>
              <span style={{ fontSize: 13, lineHeight: 1 }}>←</span>
              <span>{t('common.back')}</span>
            </button>
          )}
          <div className="crumb-trail">
            {crumbs.map((c, i) => (
              <span key={i} className="crumb-segment">
                {i > 0 && <span className="muted crumb-sep"> / </span>}
                <span
                  className={'crumb' + (c.onClick ? ' link' : '') + (c.active ? ' active' : '')}
                  onClick={c.onClick}
                >
                  {c.label}
                </span>
              </span>
            ))}
          </div>
        </div>
        <div className="scan-bar" style={{ marginLeft: 12 }}>
          {progress ? (
            <>
              <div className="bar">
                <div
                  style={{
                    width: Math.round((progress.done / Math.max(1, progress.total)) * 100) + '%'
                  }}
                />
              </div>
              <span>
                {t('scan.progress', {
                  done: progress.done,
                  total: progress.total,
                  ok: progress.ok,
                  failed: progress.failed
                })}
              </span>
            </>
          ) : null}
          <button className="btn" onClick={sync} disabled={busy}>
            {t('nav.resync')}
          </button>
          <button className="btn primary" onClick={scanAll} disabled={busy || !!runId}>
            {runId ? t('nav.scanning') : t('nav.scanAll')}
          </button>
          <button className="btn" onClick={() => setShowSchedule((v) => !v)}>
            {t('nav.schedule')}
          </button>
          <button className="btn" onClick={exportJson} disabled={busy}>
            {t('nav.exportJson')}
          </button>
        </div>
        <div className="tabs">
          <button
            className={route.name === 'fleet' || route.name === 'machine' ? 'active' : ''}
            onClick={() => switchTab({ name: 'fleet' })}
          >
            {t('nav.machines')}
          </button>
          <button
            className={route.name === 'agents' ? 'active' : ''}
            onClick={() => switchTab({ name: 'agents' })}
          >
            {t('nav.agents')}
          </button>
          <button
            className={route.name === 'overview' ? 'active' : ''}
            onClick={() => switchTab({ name: 'overview' })}
          >
            {t('nav.overview')}
          </button>
          <button
            className={route.name === 'matrix' ? 'active' : ''}
            onClick={() => switchTab({ name: 'matrix' })}
          >
            {t('nav.matrix')}
          </button>
          <button
            className={route.name === 'compare' ? 'active' : ''}
            onClick={() => switchTab({ name: 'compare' })}
          >
            {t('nav.compare')}
          </button>
          <button
            className="btn"
            style={{ marginLeft: 8, padding: '4px 8px', fontSize: 12 }}
            onClick={toggleLanguage}
            title={language === 'zh' ? 'Switch to English' : '切换为中文'}
          >
            🌐 {t('nav.langSwitch')}
          </button>
        </div>
      </div>

      {showSchedule && schedule ? (
        <div className="container" style={{ paddingBottom: 0 }}>
          <div className="panel">
            <h2>{t('schedule.title')}</h2>
            <div className="body filters">
              <label className="small">
                <input
                  type="checkbox"
                  checked={schedule.enabled}
                  onChange={(e) => saveSchedule({ enabled: e.target.checked })}
                />{' '}
                {t('schedule.every')}
              </label>
              <select
                value={schedule.interval_seconds}
                onChange={(e) => saveSchedule({ interval_seconds: Number(e.target.value) })}
              >
                <option value={600}>{t('schedule.10m')}</option>
                <option value={1800}>{t('schedule.30m')}</option>
                <option value={3600}>{t('schedule.1h')}</option>
                <option value={21600}>{t('schedule.6h')}</option>
                <option value={86400}>{t('schedule.24h')}</option>
              </select>
              <span className="muted small">
                {schedule.running ? t('schedule.running') : t('schedule.idle')}
              </span>
            </div>
          </div>
        </div>
      ) : null}

      {toast ? (
        <div className="container" style={{ paddingBottom: 0 }}>
          <div className="err">{toast}</div>
        </div>
      ) : null}

      <div className="container" key={refreshKey}>
        {route.name === 'fleet' ? (
          <MachinesView
            onOpenMachine={(hostId, hostName) =>
              navigateTo({ name: 'machine', hostId, hostName })
            }
          />
        ) : null}
        {route.name === 'agents' ? (
          <AgentsView
            selectedAgentId={route.agentId}
            onSelectAgent={(agentId) => {
              if (agentId) {
                navigateTo({ name: 'agents', agentId })
              } else {
                goBack()
              }
            }}
            onOpenInstallation={(instId, agentName, hostName) =>
              navigateTo({
                name: 'agent',
                installationId: instId,
                agentName,
                hostName,
                from: { name: 'agents', agentId: route.agentId }
              })
            }
          />
        ) : null}
        {route.name === 'machine' ? (
          <MachineView
            hostId={route.hostId}
            onOpenAgent={(id, agentName) =>
              navigateTo({
                name: 'agent',
                installationId: id,
                agentName,
                hostName: route.hostName,
                from: { name: 'machine', hostId: route.hostId, hostName: route.hostName }
              })
            }
          />
        ) : null}
        {route.name === 'agent' ? (
          <AgentPage
            installationId={route.installationId}
            onLoaded={(info) => {
              if (!route.agentName || !route.hostName) {
                setRoute((r) =>
                  r.name === 'agent'
                    ? {
                        ...r,
                        agentName: r.agentName || info.agentName,
                        hostName: r.hostName || info.hostName
                      }
                    : r
                )
              }
            }}
          />
        ) : null}
        {route.name === 'overview' ? (
          <Overview
            onOpenMachine={(hostId, hostName) =>
              navigateTo({ name: 'machine', hostId, hostName, from: { name: 'overview' } })
            }
            onScanMachine={scanRuntime}
          />
        ) : null}
        {route.name === 'matrix' ? (
          <Matrix
            onOpenInstallation={(id, agentName, hostName) =>
              navigateTo({
                name: 'agent',
                installationId: id,
                agentName,
                hostName,
                from: { name: 'matrix' }
              })
            }
          />
        ) : null}
        {route.name === 'compare' ? <Diff /> : null}
      </div>
    </div>
  )
}

function AgentPage({
  installationId,
  onLoaded
}: {
  installationId: string
  onLoaded?: (info: { agentName: string; hostName: string }) => void
}) {
  const { t } = useI18n()
  const [detail, setDetail] = useState<any>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    api
      .installation(installationId)
      .then((d) => {
        if (!alive) return
        setDetail(d)
        if (onLoaded && d) {
          onLoaded({
            agentName: agentLabel(d.agent),
            hostName: d.runtime?.host || (d as any).host || ''
          })
        }
      })
      .catch((e: any) => alive && setError(String(e.message || e)))
    return () => {
      alive = false
    }
  }, [installationId])

  if (error) return <div className="err">{error}</div>
  if (!detail) return <div className="muted">{t('common.loading')}</div>
  const rt = detail.runtime || {}
  const hostLabel = (rt.host || (detail as any).host || '?') + (rt.kind === 'wsl' ? ' [WSL]' : '')
  return <AgentDetail detail={detail} machineName={hostLabel} />
}
