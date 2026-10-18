import { useCallback, useEffect, useState } from 'react'
import { api, type Overview } from '../api'
import AgentIcon, { agentLabel } from '../components/AgentIcon'


type RuntimeRow = {
  id: string
  host: string
  key: string
  kind: string
  os_name: string | null
  collect_user: string | null
  transport: string
  reachable: boolean | null
  stale: boolean | null
  enabled: boolean
  last_success_at: string | null
  agents_installed: number
}

function ago(iso: string | null): string {
  if (!iso) return 'never'
  const then = new Date(iso).getTime()
  const secs = Math.max(0, Math.floor((Date.now() - then) / 1000))
  if (secs < 60) return secs + 's ago'
  if (secs < 3600) return Math.floor(secs / 60) + 'm ago'
  if (secs < 86400) return Math.floor(secs / 3600) + 'h ago'
  return Math.floor(secs / 86400) + 'd ago'
}

export default function Overview({
  onOpenRuntime,
  onScanRuntime
}: {
  onOpenRuntime: (id: string) => void
  onScanRuntime: (id: string) => void
}) {
  const [data, setData] = useState<Overview | null>(null)
  const [runtimes, setRuntimes] = useState<RuntimeRow[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    const load = async () => {
      try {
        const [ov, rts] = await Promise.all([api.overview(), api.runtimes()])
        if (!alive) return
        setData(ov)
        setRuntimes(rts.runtimes)
        setError(null)
      } catch (e: any) {
        if (alive) setError(String(e.message || e))
      }
    }
    load()
    const t = setInterval(load, 10000)
    return () => {
      alive = false
      clearInterval(t)
    }
  }, [])

  const [hosts, setHosts] = useState<any[]>([])
  const [showDisabled, setShowDisabled] = useState(false)

  useEffect(() => {
    api
      .hosts(showDisabled)
      .then((r) => setHosts(r.hosts))
      .catch(() => undefined)
  }, [showDisabled])

  const toggleHost = useCallback(async (host: any) => {
    setHosts((prev) => prev.map((h) => (h.id === host.id ? { ...h, enabled: !host.enabled } : h)))
    try {
      await api.setHostEnabled(host.id, !host.enabled)
      const r = await api.runtimes()
      setRuntimes(r.runtimes)
    } catch {
      setHosts((prev) => prev.map((h) => (h.id === host.id ? { ...h, enabled: host.enabled } : h)))
    }
  }, [])

  const remove = useCallback(
    async (r: RuntimeRow) => {
      if (!window.confirm('Remove ' + r.host + '/' + r.key + ' and everything collected from it?')) {
        return
      }
      try {
        await api.deleteRuntime(r.id)
        const fresh = await api.runtimes()
        setRuntimes(fresh.runtimes)
      } catch (e: any) {
        setError(String(e.message || e))
      }
    },
    []
  )

  const toggle = useCallback(async (r: RuntimeRow) => {
    setRuntimes((prev) =>
      prev.map((x) => (x.id === r.id ? { ...x, enabled: !r.enabled } : x))
    )
    try {
      await api.setRuntimeEnabled(r.id, !r.enabled)
    } catch {
      setRuntimes((prev) => prev.map((x) => (x.id === r.id ? { ...x, enabled: r.enabled } : x)))
    }
  }, [])

  if (error) return <div className="err">{error}</div>
  if (!data) return <div className="muted">Loading...</div>

  const t = data.totals
  return (
    <div>
      <div className="cards">
        <div className="card">
          <div className="label">Runtimes</div>
          <div className="value">{t.runtimes}</div>
          <div className="hint">{t.hosts} hosts</div>
        </div>
        <div className="card">
          <div className="label">Reachable</div>
          <div className="value" style={{ color: 'var(--ok)' }}>
            {t.online}
          </div>
          <div className="hint">of {t.runtimes} scanned</div>
        </div>
        <div className="card">
          <div className="label">Unreachable</div>
          <div className="value" style={{ color: t.offline ? 'var(--bad)' : undefined }}>
            {t.offline}
          </div>
          <div className="hint">{t.unknown} not scanned yet</div>
        </div>
        <div className="card">
          <div className="label">Agent installs</div>
          <div className="value">{t.installations}</div>
          <div className="hint">detected across fleet</div>
        </div>
        <div className="card">
          <div className="label">Last success</div>
          <div className="value" style={{ fontSize: 16 }}>
            {ago(data.last_successful_scan)}
          </div>
          <div className="hint">stale after {Math.round(data.stale_after_s / 3600)}h</div>
        </div>
      </div>

      <div className="grid2">
        <div className="panel">
          <h2>Agent distribution</h2>
          <div className="body">
            <table>
              <thead>
                <tr>
                  <th>Agent</th>
                  <th>Installations</th>
                </tr>
              </thead>
              <tbody>
                {data.agents.map((a) => (
                  <tr key={a.id}>
                    <td>
                      <span className="agent-cell">
                        <AgentIcon agent={a.id} size={18} />
                        {agentLabel(a.id)}
                      </span>
                    </td>
                    <td>{a.installations}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div className="panel">
          <h2>Recent scan runs</h2>
          <div className="body">
            {data.recent_runs.length === 0 ? (
              <div className="muted small">No scans yet. Use "Scan all".</div>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>Started</th>
                    <th>Status</th>
                    <th>Result</th>
                  </tr>
                </thead>
                <tbody>
                  {data.recent_runs.map((r) => (
                    <tr key={r.id}>
                      <td>{ago(r.started)}</td>
                      <td>
                        <span
                          className={
                            'tag ' +
                            (r.status === 'completed'
                              ? 'ok'
                              : r.status === 'failed'
                                ? 'bad'
                                : r.status === 'partial'
                                  ? 'warn'
                                  : '')
                          }
                        >
                          {r.status}
                        </span>
                      </td>
                      <td className="small">
                        {r.succeeded} ok / {r.failed} failed / {r.total} total
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      </div>

      <div className="panel">
        <h2>
          Runtimes
          <label className="small muted" style={{ textTransform: 'none', letterSpacing: 0 }}>
            <input
              type="checkbox"
              checked={showDisabled}
              onChange={(e) => setShowDisabled(e.target.checked)}
            />{' '}
            show excluded hosts
          </label>
        </h2>
        <div className="body">
          <table>
            <thead>
              <tr>
                <th>Host</th>
                <th style={{ width: 90 }}>Host</th>
                <th>Runtime</th>
                <th>Kind</th>
                <th>OS</th>
                <th>User</th>
                <th>Transport</th>
                <th>State</th>
                <th>Agents</th>
                <th>Last success</th>
                <th>Scan</th>
                <th>Enabled</th>
                <th style={{ width: 70 }}>Remove</th>
              </tr>
            </thead>
            <tbody>
              {runtimes.map((r) => (
                <tr key={r.id} onClick={() => onOpenRuntime(r.id)} style={{ cursor: 'pointer' }}>
                  <td className="mono">{r.host}</td>
                  <td className="mono">{r.key}</td>
                  <td>
                    {(() => {
                      const h = hosts.find((x) => x.name === r.host)
                      if (!h) return null
                      return (
                        <button
                          className={'tag ' + (h.enabled ? 'ok' : 'unknown')}
                          title={h.enabled ? 'Exclude this host from the fleet' : 'Include this host'}
                          onClick={(e) => {
                            e.stopPropagation()
                            toggleHost(h)
                          }}
                        >
                          {h.enabled ? 'on' : 'off'}
                        </button>
                      )
                    })()}
                  </td>
                  <td>
                    <span className="tag">{r.kind}</span>
                  </td>
                  <td className="small">{r.os_name || <span className="muted">unknown</span>}</td>
                  <td className="small">{r.collect_user || '-'}</td>
                  <td className="small muted">{r.transport}</td>
                  <td>
                    <span
                      className={
                        'tag ' +
                        (r.reachable === true ? 'ok' : r.reachable === false ? 'bad' : 'unknown')
                      }
                    >
                      {r.reachable === true ? 'up' : r.reachable === false ? 'down' : 'unscanned'}
                    </span>
                    {r.stale ? <span className="tag warn">stale</span> : null}
                  </td>
                  <td>{r.agents_installed}</td>
                  <td className="small muted">{ago(r.last_success_at)}</td>
                  <td>
                    <button
                      className="btn"
                      disabled={!r.enabled}
                      onClick={(e) => {
                        e.stopPropagation()
                        onScanRuntime(r.id)
                      }}
                    >
                      Scan
                    </button>
                  </td>
                  <td>
                    <button
                      className={'tag ' + (r.enabled ? 'ok' : 'unknown')}
                      onClick={(e) => {
                        e.stopPropagation()
                        toggle(r)
                      }}
                      title={r.enabled ? 'Disable scanning' : 'Enable scanning'}
                    >
                      {r.enabled ? 'on' : 'off'}
                    </button>
                  </td>
                  <td>
                    <button
                      className="tag"
                      title="Remove this runtime and its collected data"
                      onClick={(e) => {
                        e.stopPropagation()
                        remove(r)
                      }}
                    >
                      del
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
