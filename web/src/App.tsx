import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import Overview from './views/Overview'
import Matrix from './views/Matrix'
import Inspector from './views/Inspector'
import Diff from './views/Diff'

type Tab = 'overview' | 'matrix' | 'inspector' | 'diff'

type ScheduleState = {
  enabled: boolean
  interval_seconds: number
  running: boolean
  last_run_id: string | null
  last_run_at: string | null
  skipped_because_busy: number
  next_run_at: string | null
}

export default function App() {
  const [tab, setTab] = useState<Tab>('overview')
  const [runtimeFilter, setRuntimeFilter] = useState<string | undefined>(undefined)
  const [openInstallationId, setOpenInstallationId] = useState<string | undefined>(undefined)
  const [runId, setRunId] = useState<string | null>(null)
  const [progress, setProgress] = useState<{ done: number; total: number; ok: number; failed: number; status: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [toast, setToast] = useState<string | null>(null)
  const [schedule, setSchedule] = useState<ScheduleState | null>(null)
  const [showSchedule, setShowSchedule] = useState(false)

  useEffect(() => {
    api.activeScans().then((r) => {
      const active = r.runs[0]
      if (active) setRunId(active.run_id)
    })
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
          failed: run.live.failed ?? run.failed,
          status: run.status
        })
        if (run.status !== 'running') {
          setRunId(null)
          setProgress(null)
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

  const loadSchedule = useCallback(async () => {
    try {
      const s = (await fetch('/api/schedule').then((r) => r.json())) as ScheduleState
      setSchedule(s)
    } catch {
      /* scheduler unavailable */
    }
  }, [])

  const saveSchedule = useCallback(async (patch: Partial<ScheduleState>) => {
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
      a.download = 'agent-fleet-' + new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-') + '.json'
      a.click()
      URL.revokeObjectURL(url)
      setToast('Exported ' + (data.counts?.runtimes ?? 0) + ' runtime(s), ' + (data.counts?.installations ?? 0) + ' installation(s)')
    } catch (e: any) {
      setToast(String(e.message || e))
    }
  }, [])

  useEffect(() => {
    loadSchedule()
  }, [loadSchedule])

  const sync = useCallback(async () => {
    setBusy(true)
    try {
      const res = await api.syncInventory()
      setToast('Inventory synced: ' + (res.hosts_seen ?? 'ok') + ' host(s)')
    } catch (e: any) {
      setToast(String(e.message || e))
    } finally {
      setBusy(false)
    }
  }, [])

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

  const openRuntime = useCallback((id: string) => {
    setRuntimeFilter(id)
    setTab('inspector')
  }, [])

  const openInstallation = useCallback((id: string) => {
    setRuntimeFilter(undefined)
    setOpenInstallationId(id)
    setTab('inspector')
  }, [])

  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => setToast(null), 4000)
    return () => clearTimeout(t)
  }, [toast])

  return (
    <div>
      <div className="app-header">
        <h1>AGENT FLEET EXPLORER</h1>
        <span className="sub">read-only multi-machine agent configuration browser</span>
        <div className="scan-bar" style={{ marginLeft: 12 }}>
          {progress ? (
            <>
              <div className="bar">
                <div
                  style={{
                    width:
                      Math.round((progress.done / Math.max(1, progress.total)) * 100) + '%'
                  }}
                />
              </div>
              <span>
                {progress.done}/{progress.total} · {progress.ok} ok · {progress.failed} failed
              </span>
            </>
          ) : null}
          <button className="btn" onClick={sync} disabled={busy}>
            Re-sync inventory
          </button>
          <button className="btn" onClick={() => setShowSchedule((v) => !v)}>
            {showSchedule ? 'Hide schedule' : 'Schedule'}
          </button>
          <button className="btn" onClick={exportJson} disabled={busy}>
            Export JSON
          </button>
          <button className="btn primary" onClick={scanAll} disabled={busy || !!runId}>
            {runId ? 'Scanning...' : 'Scan all'}
          </button>
        </div>
        <div className="tabs">
          <button className={tab === 'overview' ? 'active' : ''} onClick={() => setTab('overview')}>
            Fleet overview
          </button>
          <button className={tab === 'matrix' ? 'active' : ''} onClick={() => setTab('matrix')}>
            Matrix
          </button>
          <button
            className={tab === 'inspector' ? 'active' : ''}
            onClick={() => {
              setRuntimeFilter(undefined)
              setOpenInstallationId(undefined)
              setTab('inspector')
            }}
          >
            Inspector
          </button>
          <button className={tab === 'diff' ? 'active' : ''} onClick={() => setTab('diff')}>
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
                {schedule.last_run_at ? ' | last run ' + new Date(schedule.last_run_at).toLocaleString() : ''}
                {schedule.skipped_because_busy ? ' | skipped while busy: ' + schedule.skipped_because_busy : ''}
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

      <div className="container">
        {tab === 'overview' ? (
          <Overview onOpenRuntime={openRuntime} onScanRuntime={scanRuntime} />
        ) : null}
        {tab === 'matrix' ? <Matrix onOpenInstallation={openInstallation} /> : null}
        {tab === 'inspector' ? (
          <Inspector
            key={(runtimeFilter || 'all') + ':' + (openInstallationId || '')}
            initialRuntimeId={runtimeFilter}
            initialInstallationId={openInstallationId}
          />
        ) : null}
        {tab === 'diff' ? <Diff /> : null}
      </div>
    </div>
  )
}
