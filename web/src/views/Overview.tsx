import { useCallback, useEffect, useState } from 'react'
import { api, type Overview } from '../api'
import AgentIcon, { agentLabel } from '../components/AgentIcon'
import { useI18n } from '../i18n'

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

function ago(iso: string | null, t: any): string {
  if (!iso) return t('common.never')
  const then = new Date(iso).getTime()
  const secs = Math.max(0, Math.floor((Date.now() - then) / 1000))
  if (secs < 60) return t('time.ago.s', { n: secs })
  if (secs < 3600) return t('time.ago.m', { n: Math.floor(secs / 60) })
  if (secs < 86400) return t('time.ago.h', { n: Math.floor(secs / 3600) })
  return t('time.ago.d', { n: Math.floor(secs / 86400) })
}

export default function Overview({
  onOpenRuntime,
  onScanRuntime
}: {
  onOpenRuntime: (id: string) => void
  onScanRuntime: (id: string) => void
}) {
  const { t } = useI18n()
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
    const timer = setInterval(load, 10000)
    return () => {
      alive = false
      clearInterval(timer)
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
      if (!window.confirm(t('overview.confirmRemove', { host: r.host, key: r.key }))) {
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
    [t]
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
  if (!data) return <div className="muted">{t('common.loading')}</div>

  const totals = data.totals
  return (
    <div>
      <div className="cards">
        <div className="card">
          <div className="label">{t('overview.runtimes')}</div>
          <div className="value">{totals.runtimes}</div>
          <div className="hint">{t('overview.hostsHint', { count: totals.hosts })}</div>
        </div>
        <div className="card">
          <div className="label">{t('overview.reachable')}</div>
          <div className="value" style={{ color: 'var(--ok)' }}>
            {totals.online}
          </div>
          <div className="hint">{t('overview.ofScanned', { total: totals.runtimes })}</div>
        </div>
        <div className="card">
          <div className="label">{t('overview.unreachable')}</div>
          <div className="value" style={{ color: totals.offline ? 'var(--bad)' : undefined }}>
            {totals.offline}
          </div>
          <div className="hint">{t('overview.notScannedYet', { count: totals.unknown })}</div>
        </div>
        <div className="card">
          <div className="label">{t('overview.agentInstalls')}</div>
          <div className="value">{totals.installations}</div>
          <div className="hint">{t('overview.detectedAcrossFleet')}</div>
        </div>
        <div className="card">
          <div className="label">{t('overview.lastSuccess')}</div>
          <div className="value" style={{ fontSize: 16 }}>
            {ago(data.last_successful_scan, t)}
          </div>
          <div className="hint">{t('overview.staleAfter', { hours: Math.round(data.stale_after_s / 3600) })}</div>
        </div>
      </div>

      <div className="grid2">
        <div className="panel">
          <h2>{t('overview.distribution')}</h2>
          <div className="body">
            <table>
              <thead>
                <tr>
                  <th>{t('overview.thAgent')}</th>
                  <th>{t('overview.thInstallations')}</th>
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
          <h2>{t('overview.recentRuns')}</h2>
          <div className="body">
            {data.recent_runs.length === 0 ? (
              <div className="muted small">{t('overview.noScans')}</div>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>{t('overview.thStarted')}</th>
                    <th>{t('overview.thStatus')}</th>
                    <th>{t('overview.thResult')}</th>
                  </tr>
                </thead>
                <tbody>
                  {data.recent_runs.map((r) => (
                    <tr key={r.id}>
                      <td>{ago(r.started, t)}</td>
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
                          {r.status === 'completed'
                            ? t('common.completed')
                            : r.status === 'failed'
                              ? t('common.failed')
                              : r.status === 'partial'
                                ? t('common.partial')
                                : r.status}
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
        </div>
      </div>

      <div className="panel">
        <h2>
          {t('overview.runtimesTable')}
          <label className="small muted" style={{ textTransform: 'none', letterSpacing: 0 }}>
            <input
              type="checkbox"
              checked={showDisabled}
              onChange={(e) => setShowDisabled(e.target.checked)}
            />{' '}
            {t('overview.showExcluded')}
          </label>
        </h2>
        <div className="body">
          <table>
            <thead>
              <tr>
                <th>{t('overview.thHost')}</th>
                <th>{t('overview.thRuntime')}</th>
                <th style={{ width: 90 }}>{t('overview.thHost')}</th>
                <th>{t('overview.thKind')}</th>
                <th>{t('overview.thOs')}</th>
                <th>{t('overview.thUser')}</th>
                <th>{t('overview.thTransport')}</th>
                <th>{t('overview.thState')}</th>
                <th>{t('overview.thAgents')}</th>
                <th>{t('overview.thLastSuccess')}</th>
                <th>{t('overview.thScan')}</th>
                <th>{t('overview.thEnabled')}</th>
                <th style={{ width: 70 }}>{t('overview.thRemove')}</th>
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
                          title={h.enabled ? t('overview.excludeHost') : t('overview.includeHost')}
                          onClick={(e) => {
                            e.stopPropagation()
                            toggleHost(h)
                          }}
                        >
                          {h.enabled ? t('common.on') : t('common.off')}
                        </button>
                      )
                    })()}
                  </td>
                  <td>
                    <span className="tag">{r.kind}</span>
                  </td>
                  <td className="small">{r.os_name || <span className="muted">{t('common.unknown')}</span>}</td>
                  <td className="small">{r.collect_user || '-'}</td>
                  <td className="small muted">{r.transport}</td>
                  <td>
                    <span
                      className={
                        'tag ' +
                        (r.reachable === true ? 'ok' : r.reachable === false ? 'bad' : 'unknown')
                      }
                    >
                      {r.reachable === true
                        ? t('common.up')
                        : r.reachable === false
                          ? t('common.down')
                          : t('common.unscanned')}
                    </span>
                    {r.stale ? <span className="tag warn">{t('common.stale')}</span> : null}
                  </td>
                  <td>{r.agents_installed}</td>
                  <td className="small muted">{ago(r.last_success_at, t)}</td>
                  <td>
                    <button
                      className="btn"
                      disabled={!r.enabled}
                      onClick={(e) => {
                        e.stopPropagation()
                        onScanRuntime(r.id)
                      }}
                    >
                      {t('common.scan')}
                    </button>
                  </td>
                  <td>
                    <button
                      className={'tag ' + (r.enabled ? 'ok' : 'unknown')}
                      onClick={(e) => {
                        e.stopPropagation()
                        toggle(r)
                      }}
                      title={r.enabled ? t('overview.disableScan') : t('overview.enableScan')}
                    >
                      {r.enabled ? t('common.on') : t('common.off')}
                    </button>
                  </td>
                  <td>
                    <button
                      className="tag"
                      title={t('overview.removeTitle')}
                      onClick={(e) => {
                        e.stopPropagation()
                        remove(r)
                      }}
                    >
                      {t('common.remove')}
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
