import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import MachinesView from './views/MachinesView'
import MachineView from './views/MachineView'
import AgentsView from './views/AgentsView'
import AgentDetail from './components/AgentDetail'
import Matrix from './views/Matrix'
import Diff from './views/Diff'
import Overview from './views/Overview'
import { useI18n } from './i18n'

type Route =
  | { name: 'fleet' }
  | { name: 'agents' }
  | { name: 'machine'; hostId: string }
  | { name: 'agent'; installationId: string }
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
          runtimes: data.counts?.runtimes ?? 0,
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

  const crumbs: { label: string; route?: Route }[] = [{ label: t('nav.machines'), route: { name: 'fleet' } }]
  if (route.name === 'machine' || route.name === 'agent') {
    crumbs.push({ label: t('nav.machine'), route: { name: 'machine', hostId: (route as any).hostId } })
  }
  if (route.name === 'agent') {
    crumbs.push({ label: t('nav.agent') })
  }
  if (route.name === 'agents') crumbs.push({ label: t('nav.agents') })
  if (route.name === 'compare') crumbs.push({ label: t('nav.compare') })
  if (route.name === 'matrix') crumbs.push({ label: t('nav.matrix') })
  if (route.name === 'overview') crumbs.push({ label: t('nav.overview') })

  return (
    <div>
      <div className="app-header">
        <div className="crumbs">
          {crumbs.map((c, i) => (
            <span key={i}>
              {i > 0 ? <span className="muted"> / </span> : null}
              <span
                className={'crumb' + (c.route ? ' link' : '')}
                onClick={c.route ? () => setRoute(c.route!) : undefined}
              >
                {c.label}
              </span>
            </span>
          ))}
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
            className={route.name === 'agents' ? 'active' : ''}
            onClick={() => setRoute({ name: 'agents' })}
          >
            {t('nav.agents')}
          </button>
          <button
            className={route.name === 'overview' ? 'active' : ''}
            onClick={() => setRoute({ name: 'overview' })}
          >
            {t('nav.overview')}
          </button>
          <button
            className={route.name === 'matrix' ? 'active' : ''}
            onClick={() => setRoute({ name: 'matrix' })}
          >
            {t('nav.matrix')}
          </button>
          <button
            className={route.name === 'compare' ? 'active' : ''}
            onClick={() => setRoute({ name: 'compare' })}
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
          <MachinesView onOpenMachine={(hostId) => setRoute({ name: 'machine', hostId })} />
        ) : null}
        {route.name === 'agents' ? <AgentsView /> : null}
        {route.name === 'machine' ? (
          <MachineView
            hostId={route.hostId}
            onOpenAgent={(id) => setRoute({ name: 'agent', installationId: id })}
          />
        ) : null}
        {route.name === 'agent' ? <AgentPage installationId={route.installationId} /> : null}
        {route.name === 'overview' ? (
          <Overview
            onOpenRuntime={(id) => setRoute({ name: 'machine', hostId: id })}
            onScanRuntime={scanRuntime}
          />
        ) : null}
        {route.name === 'matrix' ? (
          <Matrix onOpenInstallation={(id) => setRoute({ name: 'agent', installationId: id })} />
        ) : null}
        {route.name === 'compare' ? <Diff /> : null}
      </div>
    </div>
  )
}

function AgentPage({ installationId }: { installationId: string }) {
  const { t } = useI18n()
  const [detail, setDetail] = useState<any>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    api
      .installation(installationId)
      .then((d) => alive && setDetail(d))
      .catch((e: any) => alive && setError(String(e.message || e)))
    return () => {
      alive = false
    }
  }, [installationId])

  if (error) return <div className="err">{error}</div>
  if (!detail) return <div className="muted">{t('common.loading')}</div>
  const rt = detail.runtime || {}
  const runtimeName = (rt.host || '?') + '/' + (rt.key || '?')
  return <AgentDetail detail={detail} runtimeName={runtimeName} />
}
