import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, type InstallationDetail } from '../api'
import AgentIcon, { agentLabel, agentColor } from '../components/AgentIcon'
import AgentDetail from '../components/AgentDetail'
import UpgradeModal, { UpgradeIcon, UpgradeTarget } from '../components/UpgradeModal'
import { useI18n } from '../i18n'

function ago(iso: string | null, t: any): string {
  if (!iso) return ''
  const then = new Date(iso).getTime()
  const secs = Math.max(0, Math.floor((Date.now() - then) / 1000))
  if (secs < 60) return t('time.ago.s', { n: secs })
  if (secs < 3600) return t('time.ago.m', { n: Math.floor(secs / 60) })
  if (secs < 86400) return t('time.ago.h', { n: Math.floor(secs / 3600) })
  return t('time.ago.d', { n: Math.floor(secs / 86400) })
}

export default function MachinesView({
  initialHostId,
  refreshSignal
}: {
  initialHostId?: string
  refreshSignal?: number
  onOpenMachine?: (id: string, name?: string) => void
} = {}) {
  const { t, language } = useI18n()
  const [hosts, setHosts] = useState<any[]>([])
  const [selectedHostId, setSelectedHostId] = useState<string | null>(() => {
    return initialHostId || localStorage.getItem('agent_fleet_selected_host') || null
  })
  const [hostDetail, setHostDetail] = useState<any>(null)
  const [activeRuntimeKey, setActiveRuntimeKey] = useState<string>('all')
  const [activeInstId, setActiveInstId] = useState<string | null>(null)
  const [instDetail, setInstDetail] = useState<InstallationDetail | null>(null)
  const [instLoading, setInstLoading] = useState(false)
  const [search, setSearch] = useState('')
  const [filterMode, setFilterMode] = useState<'all' | 'online' | 'behind'>('all')
  const [viewMode, setViewMode] = useState<'workbench' | 'table'>(() => {
    return (localStorage.getItem('agent_fleet_machines_layout') as 'workbench' | 'table') || 'workbench'
  })

  const [upgradeTarget, setUpgradeTarget] = useState<UpgradeTarget | null>(null)
  const [rescanningHost, setRescanningHost] = useState<string | null>(null)
  const [copiedHost, setCopiedHost] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const selectHost = useCallback((id: string | null) => {
    setSelectedHostId(id)
    if (id) {
      localStorage.setItem('agent_fleet_selected_host', id)
    }
  }, [])

  // Load hosts list while strictly preserving currently selected host
  const loadHosts = useCallback(() =>
    api
      .hosts()
      .then((r) => {
        setHosts(r.hosts)
        setSelectedHostId((prev) => {
          if (prev && r.hosts.some((h: any) => h.id === prev)) {
            return prev
          }
          const saved = localStorage.getItem('agent_fleet_selected_host')
          if (saved && r.hosts.some((h: any) => h.id === saved)) {
            return saved
          }
          const local = r.hosts.find(
            (h: any) =>
              h.name.includes('local') ||
              h.runtimes?.some((rt: any) => rt.transport === 'local')
          )
          const chosen = local ? local.id : r.hosts[0]?.id || null
          if (chosen) {
            localStorage.setItem('agent_fleet_selected_host', chosen)
          }
          return chosen
        })
      })
      .catch((e: any) => setError(String(e.message || e))),
  [])

  useEffect(() => {
    loadHosts()
    const timer = setInterval(loadHosts, 15000)
    return () => {
      clearInterval(timer)
    }
  }, [loadHosts])

  // Load machine details preserving active runtime & active installation if still valid
  const loadHostDetail = useCallback((hostId: string) => {
    return api
      .machine(hostId)
      .then((d) => {
        setHostDetail(d)
        setActiveRuntimeKey((prev) => {
          if (prev === 'all') return 'all'
          const exists = d.runtimes?.some((r: any) => r.key === prev)
          return exists ? prev : 'all'
        })
        const allInsts = (d.runtimes || []).flatMap((r: any) => r.installations || [])
        const firstDetected = allInsts.find((i: any) => i.detected) || allInsts[0]
        setActiveInstId((prev) => {
          if (prev && allInsts.some((i: any) => i.id === prev)) {
            return prev
          }
          return firstDetected ? firstDetected.id : null
        })
      })
      .catch((e: any) => setError(String(e.message || e)))
  }, [])

  useEffect(() => {
    if (!selectedHostId) {
      setHostDetail(null)
      return
    }
    loadHostDetail(selectedHostId)
  }, [selectedHostId, loadHostDetail])

  // Load single installation detail when active installation or refresh signal changes
  useEffect(() => {
    if (!activeInstId) {
      setInstDetail(null)
      return
    }
    let alive = true
    setInstLoading(true)
    api
      .installation(activeInstId)
      .then((d) => {
        if (!alive) return
        setInstDetail(d)
        setInstLoading(false)
      })
      .catch(() => {
        if (!alive) return
        setInstDetail(null)
        setInstLoading(false)
      })
    return () => {
      alive = false
    }
  }, [activeInstId, refreshSignal])

  // Trigger in-place non-destructive reload when refreshSignal fires
  useEffect(() => {
    if (refreshSignal === undefined || refreshSignal === 0) return
    loadHosts()
    if (selectedHostId) {
      loadHostDetail(selectedHostId)
    }
  }, [refreshSignal, selectedHostId, loadHosts, loadHostDetail])

  const handleRescanHost = async (e: React.MouseEvent, h: any) => {
    e.stopPropagation()
    const rts = h.runtimes || []
    const ids = rts.map((r: any) => r.id).filter(Boolean)
    if (!ids.length) return
    setRescanningHost(h.id)
    try {
      await api.startScan(ids)
      setTimeout(async () => {
        await loadHosts()
        if (selectedHostId === h.id) {
          await loadHostDetail(h.id)
        }
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

  const handleLayoutChange = (mode: 'workbench' | 'table') => {
    setViewMode(mode)
    localStorage.setItem('agent_fleet_machines_layout', mode)
  }

  // Filter hosts
  const filteredHosts = useMemo(() => {
    let list = [...hosts]
    // Pinned local host to top
    list.sort((a, b) => {
      const aIsLocal =
        a.name.includes('local') ||
        a.runtimes?.some((rt: any) => rt.transport === 'local')
      const bIsLocal =
        b.name.includes('local') ||
        b.runtimes?.some((rt: any) => rt.transport === 'local')
      if (aIsLocal && !bIsLocal) return -1
      if (!aIsLocal && bIsLocal) return 1
      return a.name.localeCompare(b.name)
    })

    if (search.trim()) {
      const q = search.toLowerCase()
      list = list.filter(
        (h) =>
          h.name.toLowerCase().includes(q) ||
          (h.hostname && h.hostname.toLowerCase().includes(q))
      )
    }

    if (filterMode === 'online') {
      list = list.filter((h) => h.runtimes?.some((r: any) => r.reachable === true))
    } else if (filterMode === 'behind') {
      list = list.filter((h) =>
        h.runtimes?.some((r: any) =>
          r.agent_details?.some((ad: any) => ad.version_state === 'behind')
        )
      )
    }
    return list
  }, [hosts, search, filterMode])

  // Get installations of the currently selected host filtered by runtime
  const currentInstallations = useMemo(() => {
    if (!hostDetail) return []
    let rts = hostDetail.runtimes || []
    if (activeRuntimeKey !== 'all') {
      rts = rts.filter((r: any) => r.key === activeRuntimeKey)
    }
    return rts.flatMap((r: any) =>
      (r.installations || []).map((i: any) => ({ ...i, runtimeKey: r.key, runtimeKind: r.kind, runtimeId: r.id }))
    )
  }, [hostDetail, activeRuntimeKey])

  const installedAgents = currentInstallations.filter((i: any) => i.detected)
  const uninstalledAgents = currentInstallations.filter((i: any) => !i.detected)

  if (error) return <div className="err">{error}</div>

  return (
    <div>
      {/* Top action bar: filter & view toggle */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <div className="view-switch">
            <button
              type="button"
              className={viewMode === 'workbench' ? 'active' : ''}
              onClick={() => handleLayoutChange('workbench')}
            >
              ⊞ {language === 'zh' ? '工作台模式' : 'Workbench'}
            </button>
            <button
              type="button"
              className={viewMode === 'table' ? 'active' : ''}
              onClick={() => handleLayoutChange('table')}
            >
              ☰ {language === 'zh' ? '全宽总览表' : 'Full Table'}
            </button>
          </div>
          <span className="muted small">
            {hosts.length} {hosts.length === 1 ? 'machine' : 'machines'}
          </span>
        </div>

        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <input
            type="text"
            placeholder={language === 'zh' ? '搜索机器...' : 'Filter machines...'}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{ width: 160 }}
          />
          <div className="view-switch">
            <button
              className={filterMode === 'all' ? 'active' : ''}
              onClick={() => setFilterMode('all')}
            >
              {language === 'zh' ? '全部' : 'All'}
            </button>
            <button
              className={filterMode === 'online' ? 'active' : ''}
              onClick={() => setFilterMode('online')}
            >
              {language === 'zh' ? '在线' : 'Online'}
            </button>
            <button
              className={filterMode === 'behind' ? 'active' : ''}
              onClick={() => setFilterMode('behind')}
            >
              {language === 'zh' ? '待升级' : 'Behind'}
            </button>
          </div>
        </div>
      </div>

      {viewMode === 'workbench' ? (
        // Mode 1: Workbench Layout (Left Sidebar + Right Machine Control Plane)
        <div className="workbench-layout">
          {/* Left Machine Sidebar */}
          <div className="workbench-sidebar" style={{ width: 280 }}>
            <div className="workbench-sidebar-header">
              <span style={{ fontWeight: 600, fontSize: 13, textTransform: 'uppercase', letterSpacing: 0.5, color: 'var(--muted)' }}>
                {language === 'zh' ? '机器列表' : 'MACHINES'}
              </span>
            </div>
            <div className="workbench-nav-list">
              {filteredHosts.map((h) => {
                const rts = h.runtimes || []
                const online = rts.some((r: any) => r.reachable === true)
                const offline = rts.length > 0 && rts.every((r: any) => r.reachable === false)
                const isSelected = selectedHostId === h.id
                const isLocal =
                  h.name.includes('local') ||
                  rts.some((r: any) => r.transport === 'local')

                const hasBehind = rts.some((r: any) =>
                  r.agent_details?.some((ad: any) => ad.version_state === 'behind')
                )

                // Last success time
                const lastSuccess = rts
                  .map((r: any) => r.last_success_at)
                  .filter(Boolean)
                  .sort()
                  .reverse()[0]

                const agentIds: string[] = Array.from(
                  new Set(rts.flatMap((r: any) => (r.agents as string[]) || []))
                )

                return (
                  <div
                    key={h.id}
                    className={'workbench-nav-item ' + (isSelected ? 'active' : '')}
                    onClick={() => selectHost(h.id)}
                    style={{ flexDirection: 'column', alignItems: 'stretch', gap: 6, padding: '10px 12px' }}
                  >
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 7, minWidth: 0 }}>
                        <span className={'dot ' + (online ? 'on' : offline ? 'off' : 'unk')} />
                        <span className="mono bold" style={{ fontSize: 13.5, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {isLocal ? `★ ${h.name}` : h.name}
                        </span>
                        {isLocal && (
                          <span className="tag ok" style={{ fontSize: 9, padding: '0 4px' }}>
                            {language === 'zh' ? '本机' : 'local'}
                          </span>
                        )}
                      </div>
                      {hasBehind && (
                        <span className="tag warn" style={{ fontSize: 10, padding: '0 5px' }}>
                          ⬆️
                        </span>
                      )}
                    </div>

                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 2 }}>
                      <div style={{ display: 'flex', gap: 5, alignItems: 'center' }}>
                        {agentIds.slice(0, 5).map((a) => (
                          <span key={a} style={{ display: 'inline-flex' }}>
                            <AgentIcon agent={a} size={15} />
                          </span>
                        ))}
                        {agentIds.length > 5 && (
                          <span className="muted small" style={{ fontSize: 11 }}>+{agentIds.length - 5}</span>
                        )}
                        {agentIds.length === 0 && (
                          <span className="muted small" style={{ fontSize: 11 }}>{t('machines.noAgents')}</span>
                        )}
                      </div>
                      {lastSuccess && (
                        <span className="muted small" style={{ fontSize: 11 }}>
                          {ago(lastSuccess, t)}
                        </span>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          </div>

          {/* Right Machine Control Plane */}
          <div className="workbench-content">
            {!hostDetail ? (
              <div className="muted" style={{ padding: 24 }}>{t('common.loading')}</div>
            ) : (
              <div>
                {/* Machine Header */}
                <div className="panel" style={{ marginBottom: 14 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '12px 16px' }}>
                    <div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <span
                          className={
                            'dot ' +
                            (hostDetail.runtimes.some((r: any) => r.reachable === true)
                              ? 'on'
                              : hostDetail.runtimes.some((r: any) => r.reachable === false)
                                ? 'off'
                                : 'unk')
                          }
                        />
                        <span className="mono bold" style={{ fontSize: 18 }}>
                          {hostDetail.name}
                        </span>
                        {hostDetail.hostname && hostDetail.hostname !== hostDetail.name && (
                          <span className="muted small mono">({hostDetail.hostname})</span>
                        )}
                      </div>
                      <div className="muted small" style={{ marginTop: 4 }}>
                        {hostDetail.runtimes.map((r: any) => r.os_name || r.kind).filter(Boolean).join(' · ') || '—'}
                        {hostDetail.runtimes.some((r: any) => r.collect_user) && (
                          <span> · @{hostDetail.runtimes.map((r: any) => r.collect_user).filter(Boolean).join(', ')}</span>
                        )}
                      </div>
                    </div>

                    <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                      <button
                        className="btn small"
                        onClick={(e) => handleCopySsh(e, hostDetail)}
                      >
                        {copiedHost === hostDetail.id ? t('common.copied') : '📋 ' + t('machines.action.copySsh')}
                      </button>
                      <button
                        className="btn primary small"
                        onClick={(e) => handleRescanHost(e, hostDetail)}
                        disabled={rescanningHost === hostDetail.id}
                      >
                        {rescanningHost === hostDetail.id ? '...' : `🔄 ${t('common.scan')}`}
                      </button>
                    </div>
                  </div>

                  {/* Runtime Filter Pills if multiple runtimes (e.g. Native + WSL) */}
                  {hostDetail.runtimes.length > 1 && (
                    <div style={{ display: 'flex', gap: 6, padding: '0 16px 12px', alignItems: 'center' }}>
                      <span className="muted small">{language === 'zh' ? '环境:' : 'Runtime:'}</span>
                      <button
                        className={'btn small ' + (activeRuntimeKey === 'all' ? 'primary' : '')}
                        onClick={() => setActiveRuntimeKey('all')}
                        style={{ padding: '2px 8px', fontSize: 12 }}
                      >
                        {language === 'zh' ? '全部环境' : 'All Runtimes'}
                      </button>
                      {hostDetail.runtimes.map((r: any) => (
                        <button
                          key={r.id}
                          className={'btn small ' + (activeRuntimeKey === r.key ? 'primary' : '')}
                          onClick={() => setActiveRuntimeKey(r.key)}
                          style={{ padding: '2px 8px', fontSize: 12 }}
                        >
                          {r.key === 'native' ? t('machine.nativeEnvironment') : `WSL: ${r.key}`}
                          {r.reachable === false && <span style={{ color: 'var(--bad)', marginLeft: 4 }}>●</span>}
                        </button>
                      ))}
                    </div>
                  )}
                </div>

                {/* Horizontal Agent Switcher Strip */}
                <div style={{ marginBottom: 16 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                    <span style={{ fontWeight: 600, fontSize: 13, textTransform: 'uppercase', color: 'var(--muted)', letterSpacing: 0.5 }}>
                      {language === 'zh' ? '已安装 AGENT (点击即刻切换下方配置)' : 'INSTALLED AGENTS'}
                    </span>
                    <span className="small muted">
                      {installedAgents.length} {language === 'zh' ? '个已检出' : 'installed'}
                    </span>
                  </div>

                  {installedAgents.length === 0 ? (
                    <div className="panel" style={{ padding: 16, textAlign: 'center' }}>
                      <span className="muted small">{t('machine.noAgentsOnMachine')}</span>
                    </div>
                  ) : (
                    <div className="agent-switcher-strip">
                      {installedAgents.map((i: any) => {
                        const isSelected = activeInstId === i.id
                        const isBehind = i.version_state === 'behind'
                        const cs = i.config_summary || {}

                        return (
                          <div
                            key={i.id}
                            className={'agent-switcher-card ' + (isSelected ? 'active ' : '') + (isBehind ? 'behind' : '')}
                            onClick={() => setActiveInstId(i.id)}
                          >
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                <AgentIcon agent={i.agent} size={20} />
                                <span className="bold" style={{ fontSize: 13.5 }}>
                                  {agentLabel(i.agent)}
                                </span>
                              </div>
                              {isBehind && (
                                <UpgradeIcon
                                  size={13}
                                  title={t('version.behind', { version: i.latest_available || '' })}
                                  onClick={(e) => {
                                    e.stopPropagation()
                                    setUpgradeTarget({
                                      agent: i.agent,
                                      currentVersion: i.version,
                                      latestVersion: i.latest_available,
                                      host: hostDetail.name,
                                      runtimeKey: i.runtimeKey,
                                      packageName: i.package,
                                      releaseUrl: i.release_url,
                                      installationId: i.id,
                                      runtimeId: i.runtimeId
                                    })
                                  }}
                                />
                              )}
                            </div>

                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 4 }}>
                              <span className="mono bold" style={{ fontSize: 12 }}>
                                {i.version ? `v${i.version}` : '—'}
                              </span>
                              {i.runtimeKey !== 'native' && (
                                <span className="ts-tag" style={{ fontSize: 10 }}>{i.runtimeKey}</span>
                              )}
                            </div>

                            <div className="mono small muted" style={{ marginTop: 4, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontSize: 11 }}>
                              {cs.model || <span style={{ opacity: 0.6 }}>{t('machine.noModelSet')}</span>}
                            </div>
                          </div>
                        )
                      })}
                    </div>
                  )}

                  {uninstalledAgents.length > 0 && (
                    <div className="muted small" style={{ marginTop: 8, fontSize: 12 }}>
                      <span>+ {uninstalledAgents.length} {language === 'zh' ? '个未安装' : 'not installed'}: </span>
                      {uninstalledAgents.map((a: any) => agentLabel(a.agent)).join(', ')}
                    </div>
                  )}
                </div>

                {/* Active Agent Configuration Details (Inline Workbench Panel) */}
                {instLoading ? (
                  <div className="panel" style={{ padding: 24, textAlign: 'center' }}>
                    <span className="muted small">{t('common.loading')}</span>
                  </div>
                ) : instDetail ? (
                  <div>
                    <AgentDetail
                      detail={instDetail}
                      machineName={`${hostDetail.name}${instDetail.runtime?.key && instDetail.runtime.key !== 'native' ? ` [${instDetail.runtime.key}]` : ''}`}
                    />
                  </div>
                ) : null}
              </div>
            )}
          </div>
        </div>
      ) : (
        // Mode 2: Full-Width Table View
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
                {filteredHosts.map((h) => {
                  const rts = h.runtimes || []
                  const online = rts.some((r: any) => r.reachable === true)
                  const offline = rts.length > 0 && rts.every((r: any) => r.reachable === false)

                  const agentDetails: any[] = []
                  const seenAgents = new Set<string>()
                  for (const rt of rts) {
                    for (const ad of rt.agent_details || []) {
                      if (!seenAgents.has(ad.agent)) {
                        seenAgents.add(ad.agent)
                        agentDetails.push({ ...ad, host: h.name, rtKey: rt.key, osName: rt.os_name, instId: ad.id, rtId: rt.id })
                      }
                    }
                  }

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

                  const lastSuccess = rts
                    .map((r: any) => r.last_success_at)
                    .filter(Boolean)
                    .sort()
                    .reverse()[0]

                  return (
                    <tr key={h.id}>
                      <td>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                          <span className={'dot ' + (online ? 'on' : offline ? 'off' : 'unk')} />
                          <span
                            className="ts-machine-name"
                            onClick={() => {
                              selectHost(h.id)
                              setViewMode('workbench')
                            }}
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
                          {lastSuccess && (
                            <span className="muted small" style={{ fontSize: 11 }}>
                              {ago(lastSuccess, t)}
                            </span>
                          )}
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
                                          releaseUrl: ad.release_url,
                                          installationId: ad.instId,
                                          runtimeId: ad.rtId
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
                            onClick={(e) => handleRescanHost(e, h)}
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
                            onClick={() => {
                              selectHost(h.id)
                              setViewMode('workbench')
                            }}
                            title={language === 'zh' ? '在工作台中打开' : 'Open in Workbench'}
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
      )}

      {upgradeTarget && (
        <UpgradeModal
          target={upgradeTarget}
          onClose={() => setUpgradeTarget(null)}
          onUpgraded={() => {
            loadHosts()
            if (selectedHostId) {
              loadHostDetail(selectedHostId)
            }
          }}
        />
      )}
    </div>
  )
}
