import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import MachinesView from './views/MachinesView'
import MachineView from './views/MachineView'
import AgentDetail from './components/AgentDetail'
import Matrix from './views/Matrix'
import Diff from './views/Diff'
import Overview from './views/Overview'

type Route =
  | { name: 'fleet' }
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
          setToast('Scan finished: ' + run.succeeded + ' ok / ' + run.failed + ' failed')
        }
      } catch {
        /* keep trying */
      }
    }
    poll()
    const t = setInterval(poll, 1500)
    return () => {
      alive = false
      clearInterval(t)
    }
  }, [runId])

  const scanRuntime = useCallback(async (id: string) => {
    setBusy(true)
    try {
      const res = await api.startScan([id])
      setRunId(res.run_id)
      setToast('Scanning 1 runtime...')
    } catch (e: any) {
      setToast(String(e.message || e))
    } finally {
      setBusy(false)
    }
  }, [])

  const scanAll = useCallback(async () => {
    setBusy(true)
    try {
      const res = await api.startScan(null)
      setRunId(res.run_id)
      setToast('Scanning ' + res.total + ' runtime(s)...')
    } catch (e: any) {
      setToast(String(e.message || e))
    } finally {
      setBusy(false)
    }
  }, [])

  const sync = useCallback(async () => {
    setBusy(true)
    try {
      const res = await api.syncInventory()
      setToast('Inventory synced: ' + (res.hosts_seen ?? 'ok') + ' host(s)')
      setRefreshKey((k) => k + 1)
    } catch (e: any) {
      setToast(String(e.message || e))
    } finally {
      setBusy(false)
    }
  }, [])

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
        'Exported ' + (data.counts?.runtimes ?? 0) + ' runtime(s), ' + (data.counts?.installations ?? 0) + ' installation(s)'
      )
    } catch (e: any) {
      setToast(String(e.message || e))
    }
  }, [])

  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => setToast(null), 4000)
    return () => clearTimeout(t)
  }, [toast])

  const crumbs: { label: string; route?: Route }[] = [{ label: 'Machines', route: { name: 'fleet' } }]
  if (route.name === 'machine' || route.name === 'agent') {
    crumbs.push({ label: 'Machine', route: { name: 'machine', hostId: (route as any).hostId } })
  }
  if (route.name === 'agent') {
    crumbs.push({ label: 'Agent' })
  }
  if (route.name === 'compare') crumbs.push({ label: 'Compare' })
  if (route.name === 'matrix') crumbs.push({ label: 'Matrix' })
  if (route.name === 'overview') crumbs.push({ label: 'Overview' })

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
                {progress.done}/{progress.total} · {progress.ok} ok · {progress.failed} failed
              </span>
            </>
          ) : null}
          <button className="btn" onClick={sync} disabled={busy}>
            Re-sync
          </button>
          <button className="btn primary" onClick={scanAll} disabled={busy || !!runId}>
            {runId ? 'Scanning...' : 'Scan all'}
          </button>
          <button className="btn" onClick={() => setShowSchedule((v) => !v)}>
            Schedule
          </button>
          <button className="btn" onClick={exportJson} disabled={busy}>
            Export JSON
          </button>
        </div>
        <div className="tabs">
          <button
            className={route.name === 'overview' ? 'active' : ''}
            onClick={() => setRoute({ name: 'overview' })}
          >
            Overview
          </button>
          <button
            className={route.name === 'matrix' ? 'active' : ''}
            onClick={() => setRoute({ name: 'matrix' })}
          >
            Matrix
          </button>
          <button
            className={route.name === 'compare' ? 'active' : ''}
            onClick={() => setRoute({ name: 'compare' })}
          >
            Compare
          </button>
        </div>
      </div>

      {showSchedule && schedule ? (
        <div className="container" style={{ paddingBottom: 0 }}>
          <div className="panel">
            <h2>Scheduled scanning</h2>
            <div className="body filters">
              <label className="small">
                <input
                  type="checkbox"
                  checked={schedule.enabled}
                  onChange={(e) => saveSchedule({ enabled: e.target.checked })}
                />{' '}
                scan the whole fleet every
              </label>
              <select
                value={schedule.interval_seconds}
                onChange={(e) => saveSchedule({ interval_seconds: Number(e.target.value) })}
              >
                <option value={600}>10 minutes</option>
                <option value={1800}>30 minutes</option>
                <option value={3600}>1 hour</option>
                <option value={21600}>6 hours</option>
                <option value={86400}>24 hours</option>
              </select>
              <span className="muted small">
                {schedule.running ? 'run in progress' : 'idle'}
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
  if (!detail) return <div className="muted">Loading...</div>
  const rt = detail.runtime || {}
  const runtimeName = (rt.host || '?') + '/' + (rt.key || '?')
  return <AgentDetail detail={detail} runtimeName={runtimeName} />
}
