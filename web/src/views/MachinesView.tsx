import { useEffect, useState } from 'react'
import { api } from '../api'
import AgentIcon, { agentColor } from '../components/AgentIcon'
import UpgradeModal, { UpgradeIcon, UpgradeTarget } from '../components/UpgradeModal'
import { useI18n } from '../i18n'

export default function MachinesView({ onOpenMachine }: { onOpenMachine: (id: string, name?: string) => void }) {
  const { t } = useI18n()
  const [hosts, setHosts] = useState<any[]>([])
  const [error, setError] = useState<string | null>(null)
  const [viewMode, setViewMode] = useState<'table' | 'cards'>(() => {
    return (localStorage.getItem('agent_fleet_machines_view') as 'table' | 'cards') || 'table'
  })
  const [upgradeTarget, setUpgradeTarget] = useState<UpgradeTarget | null>(null)
  const [rescanningHost, setRescanningHost] = useState<string | null>(null)
  const [copiedHost, setCopiedHost] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    const load = () =>
      api
        .hosts()
        .then((r) => alive && setHosts(r.hosts))
        .catch((e: any) => alive && setError(String(e.message || e)))
    load()
    const tInterval = setInterval(load, 15000)
    return () => {
      alive = false
      clearInterval(tInterval)
    }
  }, [])

  const handleViewChange = (mode: 'table' | 'cards') => {
    setViewMode(mode)
    localStorage.setItem('agent_fleet_machines_view', mode)
  }

  const handleRescan = async (e: React.MouseEvent, h: any) => {
    e.stopPropagation()
    const rts = h.runtimes || []
    const ids = rts.map((r: any) => r.id).filter(Boolean)
    if (!ids.length) return
    setRescanningHost(h.id)
    try {
      await api.startScan(ids)
      setTimeout(async () => {
        const res = await api.hosts()
        setHosts(res.hosts)
        setRescanningHost(null)
      }, 1500)
    } catch {
      setRescanningHost(null)
    }
  }

  const handleCopySsh = (e: React.MouseEvent, h: any) => {
    e.stopPropagation()
    const sshTarget = h.hostname || h.name
    navigator.clipboard.writeText(`ssh ${sshTarget}`)
    setCopiedHost(h.id)
    setTimeout(() => setCopiedHost(null), 2000)
  }

  if (error) return <div className="err">{error}</div>
  if (!hosts.length) return <div className="muted">{t('machines.noMachines')}</div>

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}>
        <div className="muted small">
          {hosts.length} {hosts.length === 1 ? 'machine' : 'machines'}
        </div>
        <div className="view-switch">
          <button
            type="button"
            className={viewMode === 'table' ? 'active' : ''}
            onClick={() => handleViewChange('table')}
          >
            ☰ {t('machines.viewMode.table')}
          </button>
          <button
            type="button"
            className={viewMode === 'cards' ? 'active' : ''}
            onClick={() => handleViewChange('cards')}
          >
            ⊞ {t('machines.viewMode.cards')}
          </button>
        </div>
      </div>

      {viewMode === 'table' ? (
        <div className="panel" style={{ overflow: 'hidden' }}>
          <div className="body" style={{ padding: 0 }}>
            <table className="ts-table">
              <thead>
                <tr>
                  <th style={{ minWidth: 200 }}>{t('machines.thMachine')}</th>
                  <th style={{ minWidth: 160 }}>{t('machines.thAddresses')}</th>
                  <th style={{ minWidth: 280 }}>{t('machines.thVersion')}</th>
                  <th style={{ textAlign: 'right', minWidth: 120 }}>{t('machines.thActions')}</th>
                </tr>
              </thead>
              <tbody>
                {hosts.map((h) => {
                  const rts = h.runtimes || []
                  const online = rts.some((r: any) => r.reachable === true)
                  const offline = rts.length > 0 && rts.every((r: any) => r.reachable === false)
                  const stale = rts.some((r: any) => r.stale)

                  // Collect all agent details across runtimes
                  const agentDetails: any[] = []
                  const seenAgents = new Set<string>()
                  for (const rt of rts) {
                    for (const ad of rt.agent_details || []) {
                      if (!seenAgents.has(ad.agent)) {
                        seenAgents.add(ad.agent)
                        agentDetails.push({ ...ad, host: h.name, rtKey: rt.key, osName: rt.os_name })
                      }
                    }
                  }

                  // Also check agents list if agent_details is sparse
                  const allAgentIds: string[] = Array.from(
                    new Set(rts.flatMap((r: any) => (r.agents as string[]) || []))
                  )

                  const osStrings = Array.from(
                    new Set(
                      rts
                        .map((r: any) => {
                          const parts = [r.os_name || r.kind]
                          if (r.arch) parts.push(r.arch)
                          return parts.filter(Boolean).join(' ')
                        })
                        .filter(Boolean)
                    )
                  ).join(' · ')

                  const userString = Array.from(
                    new Set(rts.map((r: any) => r.collect_user).filter(Boolean))
                  ).join(', ')

                  return (
                    <tr key={h.id}>
                      <td>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                          <span className={'dot ' + (online ? 'on' : offline ? 'off' : 'unk')} />
                          <span
                            className="ts-machine-name"
                            onClick={() => onOpenMachine(h.id, h.name)}
                            title={h.name}
                          >
                            {h.name}
                          </span>
                        </div>
                        <div className="ts-tag-row">
                          {userString && <span className="ts-tag">@{userString}</span>}
                          {rts.some((r: any) => r.transport === 'ssh') && (
                            <span className="ts-tag blue">SSH</span>
                          )}
                          {rts.some((r: any) => r.wsl_distro) && (
                            <span className="ts-tag">WSL</span>
                          )}
                          {stale && <span className="tag warn">{t('common.stale')}</span>}
                          {!h.enabled && <span className="tag unknown">{t('common.excluded')}</span>}
                        </div>
                      </td>

                      <td>
                        <div className="mono small" style={{ color: 'var(--text)' }}>
                          {h.hostname || h.name}
                        </div>
                        <div className="muted small" style={{ marginTop: 4 }}>
                          {rts.map((r: any) => r.ssh_target || r.key).filter(Boolean).join(', ')}
                        </div>
                      </td>

                      <td>
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, alignItems: 'center' }}>
                          {agentDetails.length > 0 ? (
                            agentDetails.map((ad: any) => {
                              const isBehind = ad.version_state === 'behind'
                              return (
                                <div
                                  key={ad.agent}
                                  className={'ts-agent-item ' + (isBehind ? 'behind' : '')}
                                >
                                  <AgentIcon agent={ad.agent} size={15} />
                                  <span className="mono small" style={{ fontSize: 11.5 }}>
                                    {ad.version ? `v${ad.version}` : '—'}
                                  </span>
                                  {isBehind && (
                                    <UpgradeIcon
                                      size={12}
                                      title={t('version.behind', { version: ad.latest_available || '' })}
                                      onClick={(e) => {
                                        e.stopPropagation()
                                        setUpgradeTarget({
                                          agent: ad.agent,
                                          currentVersion: ad.version,
                                          latestVersion: ad.latest_available,
                                          host: ad.host,
                                          runtimeKey: ad.rtKey,
                                          osName: ad.osName,
                                          packageName: ad.package,
                                          releaseUrl: ad.release_url
                                        })
                                      }}
                                    />
                                  )}
                                </div>
                              )
                            })
                          ) : allAgentIds.length > 0 ? (
                            allAgentIds.map((a: string) => (
                              <span
                                key={a}
                                className="machine-agent-dot"
                                style={{ color: agentColor(a) }}
                                title={a}
                              >
                                <AgentIcon agent={a} size={18} />
                              </span>
                            ))
                          ) : (
                            <span className="muted small">{t('machines.noAgents')}</span>
                          )}
                        </div>
                        <div className="ts-os-line">{osStrings || '—'}</div>
                      </td>

                      <td style={{ textAlign: 'right' }}>
                        <div style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
                          <button
                            className="btn small"
                            onClick={(e) => handleRescan(e, h)}
                            disabled={rescanningHost === h.id}
                            title={t('machines.action.rescan')}
                          >
                            {rescanningHost === h.id ? '...' : t('common.scan')}
                          </button>
                          <button
                            className="btn small"
                            onClick={(e) => handleCopySsh(e, h)}
                            title={t('machines.action.copySsh')}
                          >
                            {copiedHost === h.id ? t('common.copied') : 'SSH'}
                          </button>
                          <button
                            className="btn small"
                            onClick={() => onOpenMachine(h.id, h.name)}
                            title={t('machines.action.view')}
                          >
                            ➔
                          </button>
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      ) : (
        <div className="machine-grid">
          {hosts.map((h) => {
            const rts = h.runtimes || []
            const online = rts.some((r: any) => r.reachable === true)
            const offline = rts.length > 0 && rts.every((r: any) => r.reachable === false)
            const agents: string[] = Array.from(
              new Set(rts.flatMap((r: any) => (r.agents as string[]) || []))
            )
            const total = rts.reduce((n: number, r: any) => n + (r.agents_installed || 0), 0)
            const stale = rts.some((r: any) => r.stale)
            return (
              <div className="machine-card" key={h.id} onClick={() => onOpenMachine(h.id, h.name)}>
                <div className="machine-card-head">
                  <span className={'dot ' + (online ? 'on' : offline ? 'off' : 'unk')} />
                  <span className="machine-name">{h.name}</span>
                  {stale ? <span className="tag warn">{t('common.stale')}</span> : null}
                  {!h.enabled ? <span className="tag unknown">{t('common.excluded')}</span> : null}
                </div>
                <div className="machine-os muted small">
                  {rts.map((r: any) => r.os_name || r.kind).filter(Boolean).join(' · ') || h.hostname || '—'}
                </div>
                <div className="machine-agents">
                  {agents.map((a: string) => (
                    <span
                      key={a}
                      className="machine-agent-dot"
                      style={{ color: agentColor(a) }}
                      title={a}
                    >
                      <AgentIcon agent={a} size={20} />
                    </span>
                  ))}
                  {agents.length === 0 ? <span className="muted small">{t('machines.noAgents')}</span> : null}
                </div>
                <div className="machine-foot muted small">
                  {t('machines.footer', {
                    instTotal: total,
                    instPlural: total === 1 ? '' : 's'
                  })}
                </div>
              </div>
            )
          })}
        </div>
      )}

      {upgradeTarget && (
        <UpgradeModal target={upgradeTarget} onClose={() => setUpgradeTarget(null)} />
      )}
    </div>
  )
}
