import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import Overview from './views/Overview'
import Matrix from './views/Matrix'
import Inspector from './views/Inspector'

type Tab = 'overview' | 'matrix' | 'inspector'

export default function App() {
  const [tab, setTab] = useState<Tab>('overview')
  const [runtimeFilter, setRuntimeFilter] = useState<string | undefined>(undefined)
  const [openInstallationId, setOpenInstallationId] = useState<string | undefined>(undefined)
  const [runId, setRunId] = useState<string | null>(null)
  const [progress, setProgress] = useState<{ done: number; total: number; ok: number; failed: number; status: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [toast, setToast] = useState<string | null>(null)

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
        </div>
      </div>

      {toast ? (
        <div className="container" style={{ paddingBottom: 0 }}>
          <div className="err">{toast}</div>
        </div>
      ) : null}

      <div className="container">
        {tab === 'overview' ? <Overview onOpenRuntime={openRuntime} /> : null}
        {tab === 'matrix' ? <Matrix onOpenInstallation={openInstallation} /> : null}
        {tab === 'inspector' ? (
          <Inspector
            key={(runtimeFilter || 'all') + ':' + (openInstallationId || '')}
            initialRuntimeId={runtimeFilter}
            initialInstallationId={openInstallationId}
          />
        ) : null}
      </div>
    </div>
  )
}
