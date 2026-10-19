import { useEffect, useState } from 'react'
import { api, type InstallationDiff, type DiffChange } from '../api'
import AgentIcon, { agentLabel } from '../components/AgentIcon'
import InstallationDrawer from '../components/InstallationDrawer'
import UpgradeModal, { UpgradeIcon, UpgradeTarget } from '../components/UpgradeModal'
import { useI18n } from '../i18n'
import { useUpgrades } from '../context/UpgradesContext'

const ALL_AGENT_IDS = [
  'claude',
  'codex',
  'agy',
  'opencode',
  'pi',
  'hermes',
  'codebuddy',
  'qoder'
]

export default function AgentsView({
  selectedAgentId,
  refreshSignal,
  onSelectAgent
}: {
  selectedAgentId?: string
  refreshSignal?: number
  onSelectAgent?: (id: string | null) => void
} = {}) {
  const { t, language } = useI18n()
  const {
    isUpgrading,
    triggerUpgrade,
    triggerBatchUpgrade,
    triggerAgentUpgradeAll,
    openTasksDrawer
  } = useUpgrades()
  const [activeAgent, setActiveAgent] = useState<string>(() => {
    return selectedAgentId || localStorage.getItem('agent_fleet_active_agent') || 'claude'
  })
  const [agentSummaries, setAgentSummaries] = useState<
    Record<string, { count: number; behind: number; variants: number }>
  >({})
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [upgradeTarget, setUpgradeTarget] = useState<UpgradeTarget | null>(null)
  const [drawerInstId, setDrawerInstId] = useState<string | null>(null)
  const [customBaselineIdx, setCustomBaselineIdx] = useState<number>(0)
  const [refreshCount, setRefreshCount] = useState(0)

  // Compare state: selected installation IDs (up to 2)
  const [selectedInsts, setSelectedInsts] = useState<string[]>([])
  const [diffResult, setDiffResult] = useState<InstallationDiff | null>(null)
  const [diffLoading, setDiffLoading] = useState(false)

  useEffect(() => {
    if (refreshSignal === undefined || refreshSignal === 0) return
    setRefreshCount((c) => c + 1)
  }, [refreshSignal])

  // Load summary counts and upgrade status across all agents immediately
  useEffect(() => {
    let alive = true
    api.runtimes().then(async (rtsRes) => {
      if (!alive) return
      const map: Record<string, { count: number; behind: number; variants: number }> = {}
      for (const aid of ALL_AGENT_IDS) {
        map[aid] = { count: 0, behind: 0, variants: 1 }
      }
      for (const rt of rtsRes.runtimes) {
        for (const ad of rt.agent_details || []) {
          if (map[ad.agent]) {
            map[ad.agent].count++
            if (ad.version_state === 'behind') {
              map[ad.agent].behind++
            }
          }
        }
      }
      setAgentSummaries(map)

      // Background pass to fetch variant counts for detected agents
      const detectedAids = Object.keys(map).filter((aid) => map[aid].count > 0)
      if (detectedAids.length > 0) {
        try {
          const views = await Promise.all(
            detectedAids.map((aid) => api.agentView(aid).catch(() => null))
          )
          if (!alive) return
          setAgentSummaries((prev) => {
            const updated = { ...prev }
            for (const v of views) {
              if (v && updated[v.agent]) {
                updated[v.agent] = {
                  count: v.detected_count,
                  behind: v.version?.behind ?? updated[v.agent].behind,
                  variants: v.config_groups?.length || 1
                }
              }
            }
            return updated
          })
        } catch {
          // Keep initial counts from runtimes
        }
      }
    }).catch(() => undefined)

    return () => {
      alive = false
    }
  }, [refreshCount])

  // Clear comparison when activeAgent changes
  useEffect(() => {
    setSelectedInsts([])
    setDiffResult(null)
  }, [activeAgent])

  // When active agent changes or refresh occurs, load its agent-view data
  useEffect(() => {
    if (!activeAgent) return
    let alive = true
    setLoading(true)
    setError(null)

    api
      .agentView(activeAgent)
      .then((d) => {
        if (!alive) return
        setData(d)
        setLoading(false)
        // Update summary for active agent
        const behindCount = d.version?.behind || 0
        const variantCount = d.config_groups?.length || 1
        setAgentSummaries((prev) => ({
          ...prev,
          [activeAgent]: {
            count: d.detected_count,
            behind: behindCount,
            variants: variantCount
          }
        }))
      })
      .catch((e: any) => {
        if (!alive) return
        setError(String(e.message || e))
        setLoading(false)
      })

    return () => {
      alive = false
    }
  }, [activeAgent, refreshCount])

  const handleSelectAgent = (aid: string) => {
    setActiveAgent(aid)
    localStorage.setItem('agent_fleet_active_agent', aid)
    onSelectAgent?.(aid)
  }

  // Toggle selection for comparison
  const handleToggleSelectInst = async (instId: string) => {
    let next: string[]
    if (selectedInsts.includes(instId)) {
      next = selectedInsts.filter((id) => id !== instId)
    } else {
      if (selectedInsts.length >= 2) {
        next = [selectedInsts[1], instId]
      } else {
        next = [...selectedInsts, instId]
      }
    }
    setSelectedInsts(next)

    if (next.length === 2) {
      triggerDiff(next[0], next[1])
    } else {
      setDiffResult(null)
    }
  }

  const triggerDiff = async (idA: string, idB: string) => {
    setDiffLoading(true)
    try {
      const res = await api.diffInstallations(idA, idB)
      setDiffResult(res)
    } catch {
      setDiffResult(null)
    } finally {
      setDiffLoading(false)
    }
  }

  const handleCompareGroupToBaseline = (groupMachines: string[], targetBaselineIdx = customBaselineIdx) => {
    if (!data || !data.config_groups || data.config_groups.length < 2) return
    const baselineGroup = data.config_groups[targetBaselineIdx] || data.config_groups[0]
    const baselineMachines = baselineGroup.machines
    // find one installation id from baseline and one from this variant
    const instA = data.installations.find((i: any) =>
      baselineMachines.includes(`${i.host}/${i.runtime_key}`)
    )
    const instB = data.installations.find((i: any) =>
      groupMachines.includes(`${i.host}/${i.runtime_key}`)
    )
    if (instA && instB) {
      setSelectedInsts([instA.id, instB.id])
      triggerDiff(instA.id, instB.id)
    }
  }

  const v = data?.version

  return (
    <div className="workbench-layout">
      {/* Left Sidebar: Agents List */}
      <div className="workbench-sidebar">
        <div className="workbench-sidebar-header">
          <span style={{ fontWeight: 600, fontSize: 13, textTransform: 'uppercase', letterSpacing: 0.5, color: 'var(--muted)' }}>
            {language === 'zh' ? '已纳管 AGENT' : 'AGENTS'}
          </span>
        </div>
        <div className="workbench-nav-list">
          {ALL_AGENT_IDS.map((aid) => {
            const summary = agentSummaries[aid]
            const isSelected = activeAgent === aid
            const count = summary?.count ?? 0
            const behind = summary?.behind ?? 0
            const variants = summary?.variants ?? 1

            return (
              <div
                key={aid}
                className={'workbench-nav-item ' + (isSelected ? 'active' : '')}
                onClick={() => handleSelectAgent(aid)}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
                  <AgentIcon agent={aid} size={18} />
                  <span className="bold" style={{ fontSize: 13 }}>
                    {agentLabel(aid)}
                  </span>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                  {behind > 0 && (
                    <span className="tag warn" style={{ fontSize: 10, padding: '0 5px' }} title={`${behind} machine(s) behind`}>
                      ⬆️ {behind}
                    </span>
                  )}
                  {variants > 1 && (
                    <span className="tag" style={{ fontSize: 10, padding: '0 5px', color: 'var(--warn)' }} title={`${variants} config variants`}>
                      ⚠️ {variants}
                    </span>
                  )}
                  <span className="ts-tag" style={{ fontSize: 11 }}>
                    {count}
                  </span>
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {/* Right Content Pane */}
      <div className="workbench-content">
        {loading && <div className="muted" style={{ padding: 24 }}>{t('common.loading')}</div>}
        {error && <div className="err" style={{ margin: 16 }}>{error}</div>}

        {data && !loading && (
          <div>
            {/* Header cards */}
            <div className="cards" style={{ marginBottom: 16 }}>
              <div className="card">
                <div className="label">{t('agents.agent')}</div>
                <div className="value" style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 19 }}>
                  <AgentIcon agent={data.agent} size={22} />
                  <span>{data.name}</span>
                </div>
                <div className="hint" style={{ marginTop: 4 }}>
                  {data.detected_count} {language === 'zh' ? '台机器已安装' : 'machines installed'}
                </div>
              </div>

              <div className="card">
                <div className="label">
                  {v?.unknown_source
                    ? t('agents.latestVersion')
                    : v?.source === 'github'
                      ? t('agents.latestOnGithub')
                      : t('agents.latestOnNpm')}
                </div>
                <div className="value mono" style={{ fontSize: 18 }}>
                  {v?.latest_available || <span className="muted">{t('common.unknown')}</span>}
                </div>
                <div className="hint">
                  {v?.package ? (
                    <a
                      href={data.release_url || `https://www.npmjs.com/package/${v.package}`}
                      target="_blank"
                      rel="noreferrer"
                      style={{ color: 'var(--accent)', textDecoration: 'none' }}
                    >
                      {v.package}
                    </a>
                  ) : (
                    t('agents.noRegistry')
                  )}
                </div>
              </div>

              <div className="card">
                <div className="label">{t('agents.upToDate')}</div>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                  <div className="value" style={{ color: 'var(--ok)', fontSize: 20 }}>
                    {v?.up_to_date || 0}
                  </div>
                  {v?.behind > 0 && (
                    <button
                      className="btn small"
                      onClick={() => triggerAgentUpgradeAll(data.agent)}
                      style={{
                        borderColor: 'rgba(210, 153, 34, 0.5)',
                        color: '#e3b341',
                        fontSize: 11,
                        padding: '2px 8px',
                        display: 'inline-flex',
                        alignItems: 'center',
                        gap: 4
                      }}
                      title={language === 'zh' ? `一键升级全机队所有落后的 ${data.name}` : `Upgrade all outdated ${data.name} across fleet`}
                    >
                      <span>⚡</span>
                      <span>
                        {language === 'zh'
                          ? `一键更新全机队 (${v.behind})`
                          : `Upgrade All (${v.behind})`}
                      </span>
                    </button>
                  )}
                </div>
                <div className="hint" style={{ marginTop: 4 }}>
                  {v?.behind ? (
                    <span style={{ color: 'var(--warn)' }}>{t('agents.behind', { count: v.behind })}</span>
                  ) : (
                    t('version.upToDate')
                  )}
                </div>
              </div>

              <div className="card">
                <div className="label">{t('agents.config')}</div>
                <div className="value" style={{ fontSize: 18 }}>
                  {data.consistent_configs ? (
                    <span style={{ color: 'var(--ok)' }}>{t('agents.consistent')}</span>
                  ) : (
                    <span style={{ color: 'var(--warn)' }}>{t('agents.variants', { count: data.config_groups.length })}</span>
                  )}
                </div>
                <div className="hint">
                  {data.consistent_configs ? t('agents.consistentHint') : t('agents.differHint')}
                </div>
              </div>
            </div>

            {/* Config Consistency & Variants */}
            <div className="panel">
              <h2>
                <span>{language === 'zh' ? '配置一致性基准与变体' : 'Configuration Variants & Baseline'}</span>
                {data.consistent_configs ? (
                  <span className="tag ok">{language === 'zh' ? '全机队完全一致' : 'Fleet Consistent'}</span>
                ) : (
                  <span className="tag warn">{language === 'zh' ? `${data.config_groups.length} 种变体` : `${data.config_groups.length} variants`}</span>
                )}
              </h2>
              <div className="body">
                {data.config_groups.length === 0 ? (
                  <div className="muted small">{t('machines.noAgents')}</div>
                ) : data.consistent_configs ? (
                  <div className="muted small" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ color: 'var(--ok)', fontSize: 16 }}>✓</span>
                    <span>{t('agents.everyMachineSame')}</span>
                    <span className="mono small tag" style={{ marginLeft: 8 }}>
                      {data.config_groups[0]?.signature}
                    </span>
                  </div>
                ) : (
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 12 }}>
                    {data.config_groups.map((g: any, idx: number) => {
                      const isBaseline = idx === customBaselineIdx
                      const parts = g.signature.split(' / ')
                      return (
                        <div
                          key={g.signature}
                          className="config-variant-card"
                          style={{
                            border: isBaseline
                              ? '1px solid rgba(63, 185, 80, 0.45)'
                              : '1px solid var(--border)',
                            background: isBaseline ? 'rgba(63, 185, 80, 0.05)' : 'var(--panel-2)'
                          }}
                        >
                          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                            <span className={'tag ' + (isBaseline ? 'ok' : 'warn')}>
                              {isBaseline
                                ? language === 'zh'
                                  ? g.count > 1 ? `主流基线 (${g.count} 台机器)` : `基准配置 (${g.machines[0] || '1 台'})`
                                  : `Baseline (${g.count} machines)`
                                : language === 'zh'
                                  ? `变体 ${idx + 1} (${g.count} 台机器)`
                                  : `Variant ${idx + 1} (${g.count} machines)`}
                            </span>
                            <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                              {!isBaseline && (
                                <>
                                  <button
                                    className="btn small"
                                    style={{ padding: '2px 6px', fontSize: 11 }}
                                    onClick={() => setCustomBaselineIdx(idx)}
                                    title={language === 'zh' ? '将此配置设为对比基准' : 'Set as Baseline'}
                                  >
                                    {language === 'zh' ? '设为基准' : 'Set Baseline'}
                                  </button>
                                  <button
                                    className="btn small"
                                    style={{ padding: '2px 8px', fontSize: 11 }}
                                    onClick={() => handleCompareGroupToBaseline(g.machines, customBaselineIdx)}
                                  >
                                    ⚡ {language === 'zh' ? '与基准对比' : 'Diff vs Baseline'}
                                  </button>
                                </>
                              )}
                            </div>
                          </div>
                          <div className="small mono" style={{ lineHeight: 1.45 }}>
                            <div><span className="muted">Model: </span>{parts[0] || '—'}</div>
                            <div><span className="muted">Provider: </span>{parts[1] || '—'}</div>
                            <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                              <span className="muted">Endpoint: </span>{parts[2] || '—'}
                            </div>
                          </div>
                          <div className="small muted" style={{ marginTop: 6, fontSize: 11 }}>
                            {g.machines.slice(0, 3).join(', ')}
                            {g.machines.length > 3 ? ` +${g.machines.length - 3}` : ''}
                          </div>
                        </div>
                      )
                    })}
                  </div>
                )}
              </div>
            </div>

            {/* Installations Table */}
            <div className="panel">
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 14px', borderBottom: '1px solid var(--border)' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <span style={{ fontWeight: 600, fontSize: 13, textTransform: 'uppercase', color: 'var(--muted)', letterSpacing: 0.5 }}>
                    {t('agents.tableTitle')}
                  </span>
                  {selectedInsts.length > 0 && (
                    <button
                      className="btn small primary"
                      onClick={() => triggerBatchUpgrade(selectedInsts)}
                      style={{ fontSize: 11.5, padding: '2px 8px', display: 'inline-flex', alignItems: 'center', gap: 4 }}
                    >
                      <span>⚡</span>
                      <span>
                        {language === 'zh'
                          ? `批量更新选中项 (${selectedInsts.length})`
                          : `Upgrade Selected (${selectedInsts.length})`}
                      </span>
                    </button>
                  )}
                  {v?.behind > 1 && selectedInsts.length === 0 && (
                    <button
                      className="btn small"
                      onClick={() => {
                        const behindIds = data.installations
                          .filter((i: any) => i.version_state === 'behind')
                          .map((i: any) => i.id)
                        triggerBatchUpgrade(behindIds)
                      }}
                      style={{
                        borderColor: 'rgba(210, 153, 34, 0.45)',
                        color: '#e3b341',
                        fontSize: 11.5,
                        padding: '2px 8px',
                        display: 'inline-flex',
                        alignItems: 'center',
                        gap: 4
                      }}
                    >
                      <span>⚡</span>
                      <span>
                        {language === 'zh'
                          ? `批量更新落后项 (${v.behind})`
                          : `Upgrade All Behind (${v.behind})`}
                      </span>
                    </button>
                  )}
                </div>
                <span className="small muted">
                  {language === 'zh'
                    ? '勾选 2 台主机即可就地对比字段级配置差异'
                    : 'Check 2 machines to view inline differences'}
                </span>
              </div>
              <div className="body" style={{ padding: 0 }}>
                <table className="ts-table">
                  <thead>
                    <tr>
                      <th style={{ width: 44, textAlign: 'center' }}>
                        {language === 'zh' ? '对比' : 'Diff'}
                      </th>
                      <th>{t('agents.thMachine')}</th>
                      <th>{t('agents.thVersion')}</th>
                      <th>{t('agents.thModel')}</th>
                      <th>{t('agents.thProvider')}</th>
                      <th style={{ textAlign: 'right' }}>{t('machines.thActions')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.installations
                      .slice()
                      .sort((a: any, b: any) =>
                        (a.host + a.runtime_key).localeCompare(b.host + b.runtime_key)
                      )
                      .map((i: any) => {
                        const isChecked = selectedInsts.includes(i.id)
                        const cs = i.config_summary || {}
                        const isBehind = i.version_state === 'behind'

                        return (
                          <tr
                            key={i.id}
                            style={{ background: isChecked ? 'rgba(76, 154, 255, 0.08)' : undefined }}
                          >
                            <td style={{ textAlign: 'center', verticalAlign: 'middle' }}>
                              <input
                                type="checkbox"
                                checked={isChecked}
                                onChange={() => handleToggleSelectInst(i.id)}
                                title={language === 'zh' ? '选择加入对比' : 'Select for comparison'}
                                style={{ cursor: 'pointer' }}
                              />
                            </td>
                            <td>
                              <div className="mono bold" style={{ fontSize: 13.5 }}>
                                {i.host}
                                {i.runtime_key && i.runtime_key !== 'native' && (
                                  <span className="ts-tag" style={{ marginLeft: 6 }}>{i.runtime_key}</span>
                                )}
                                {i.collect_user && (
                                  <span className="muted small"> @{i.collect_user}</span>
                                )}
                              </div>
                              <div className="small muted" style={{ marginTop: 2 }}>
                                {i.os_name || i.kind} {i.arch ? `· ${i.arch}` : ''}
                              </div>
                            </td>
                            <td>
                              <div className="ts-version-line">
                                {isBehind && (
                                  <UpgradeIcon
                                    upgrading={isUpgrading(i.id)}
                                    title={
                                      isUpgrading(i.id)
                                        ? (language === 'zh' ? '正在后台升级中... (点击查看任务中心)' : 'Upgrading in background... (click to view tasks)')
                                        : (language === 'zh'
                                            ? `${t('version.behind', { version: v?.latest_available || '' })} (点击直接升级，Alt+点击查看命令)`
                                            : `${t('version.behind', { version: v?.latest_available || '' })} (Click to upgrade directly, Alt+click for manual command)`)
                                    }
                                    onClick={(e) => {
                                      e.stopPropagation()
                                      if (isUpgrading(i.id)) {
                                        openTasksDrawer()
                                        return
                                      }
                                      if (e.altKey || e.shiftKey) {
                                        setUpgradeTarget({
                                          agent: data.agent,
                                          currentVersion: i.version,
                                          latestVersion: v?.latest_available,
                                          host: i.host,
                                          runtimeKey: i.runtime_key,
                                          osName: i.os_name,
                                          packageName: v?.package,
                                          releaseUrl: data.release_url,
                                          installationId: i.id,
                                          runtimeId: i.runtime_id
                                        })
                                        return
                                      }
                                      triggerUpgrade(i.id, {
                                        agent: data.agent,
                                        host: i.host,
                                        currentVersion: i.version,
                                        latestVersion: v?.latest_available,
                                        runtimeKey: i.runtime_key,
                                        packageName: v?.package,
                                        releaseUrl: data.release_url,
                                        installationId: i.id,
                                        runtimeId: i.runtime_id
                                      })
                                    }}
                                  />
                                )}
                                <span className="mono bold">{i.version ? `v${i.version}` : '—'}</span>
                                {i.version_state === 'latest' && (
                                  <span className="tag ok" style={{ marginLeft: 6, fontSize: 10, padding: '0 5px' }}>
                                    {t('version.upToDate')}
                                  </span>
                                )}
                              </div>
                              <div className="small muted" style={{ marginTop: 2 }}>
                                {i.install_method || '—'}
                              </div>
                            </td>
                            <td className="mono small">
                              {cs.model || <span className="muted">—</span>}
                            </td>
                            <td className="small">
                              {cs.provider || '—'}
                              {cs.base_url ? (
                                <div className="muted mono" style={{ fontSize: 11, maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                  {cs.base_url}
                                </div>
                              ) : null}
                            </td>
                            <td style={{ textAlign: 'right' }}>
                              <div style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
                                {isBehind && (
                                  <button
                                    className="btn small"
                                    onClick={(e) => {
                                      if (isUpgrading(i.id)) {
                                        openTasksDrawer()
                                        return
                                      }
                                      if (e.altKey || e.shiftKey) {
                                        setUpgradeTarget({
                                          agent: data.agent,
                                          currentVersion: i.version,
                                          latestVersion: v?.latest_available,
                                          host: i.host,
                                          runtimeKey: i.runtime_key,
                                          osName: i.os_name,
                                          packageName: v?.package,
                                          releaseUrl: data.release_url,
                                          installationId: i.id,
                                          runtimeId: i.runtime_id
                                        })
                                        return
                                      }
                                      triggerUpgrade(i.id, {
                                        agent: data.agent,
                                        host: i.host,
                                        currentVersion: i.version,
                                        latestVersion: v?.latest_available,
                                        runtimeKey: i.runtime_key,
                                        packageName: v?.package,
                                        releaseUrl: data.release_url,
                                        installationId: i.id,
                                        runtimeId: i.runtime_id
                                      })
                                    }}
                                    style={{
                                      display: 'inline-flex',
                                      alignItems: 'center',
                                      gap: 4,
                                      borderColor: isUpgrading(i.id) ? 'rgba(76, 154, 255, 0.45)' : 'rgba(210, 153, 34, 0.45)',
                                      color: isUpgrading(i.id) ? 'var(--accent)' : '#e3b341'
                                    }}
                                  >
                                    <UpgradeIcon size={12} upgrading={isUpgrading(i.id)} />
                                    <span>
                                      {isUpgrading(i.id)
                                        ? (language === 'zh' ? '更新中...' : 'Updating...')
                                        : (language === 'zh' ? '升级' : 'Upgrade')}
                                    </span>
                                  </button>
                                )}
                                <button
                                  className="btn small"
                                  onClick={() => setDrawerInstId(i.id)}
                                >
                                  {language === 'zh' ? '配置详情' : 'Detail'}
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

            {/* Inline Diff Section */}
            {selectedInsts.length > 0 && (
              <div className="panel" style={{ border: '1px solid var(--accent)' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 14px', borderBottom: '1px solid var(--border)' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ fontSize: 16 }}>⚖️</span>
                    <span style={{ fontWeight: 600, fontSize: 14 }}>
                      {language === 'zh' ? '就地字段级配置对比' : 'Inline Field-Level Diff'}
                    </span>
                    {selectedInsts.length === 1 && (
                      <span className="muted small">
                        ({language === 'zh' ? '请勾选第 2 台机器进行对比' : 'select 1 more machine to compare'})
                      </span>
                    )}
                  </div>
                  <button
                    className="btn small"
                    onClick={() => {
                      setSelectedInsts([])
                      setDiffResult(null)
                    }}
                  >
                    ✕ {t('common.close')}
                  </button>
                </div>
                <div className="body">
                  {diffLoading && <div className="muted small">{t('common.loading')}</div>}
                  {diffResult && (
                    <InlineDiffTable diff={diffResult} />
                  )}
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      <InstallationDrawer
        installationId={drawerInstId}
        onClose={() => setDrawerInstId(null)}
      />

      {upgradeTarget && (
        <UpgradeModal
          target={upgradeTarget}
          onClose={() => setUpgradeTarget(null)}
          onUpgraded={() => {
            setRefreshCount((c) => c + 1)
          }}
        />
      )}
    </div>
  )
}

function InlineDiffTable({ diff }: { diff: InstallationDiff }) {
  const { t } = useI18n()
  if (diff.identical) {
    return (
      <div className="muted small" style={{ padding: '8px 0', color: 'var(--ok)' }}>
        ✓ {t('diff.identicallyConfigured', {
          hostA: diff.a.runtime?.host || 'A',
          hostB: diff.b.runtime?.host || 'B'
        })}
      </div>
    )
  }

  const label = (side: any) =>
    side.runtime
      ? side.runtime.host +
        (side.runtime.key && side.runtime.key !== 'native' ? ` [${side.runtime.key}]` : '')
      : side.id

  return (
    <div>
      <div style={{ marginBottom: 10, display: 'flex', gap: 8 }}>
        <span className="tag">{t('diff.changesCount', { count: diff.changes.length })}</span>
      </div>
      <table className="ts-table" style={{ fontSize: 12 }}>
        <thead>
          <tr>
            <th style={{ width: '30%' }}>{t('diff.field')}</th>
            <th style={{ width: '35%' }}>A: {label(diff.a)}</th>
            <th style={{ width: '35%' }}>B: {label(diff.b)}</th>
          </tr>
        </thead>
        <tbody>
          {diff.changes.map((c: DiffChange, i: number) => (
            <tr key={i}>
              <td className="mono bold">{c.path}</td>
              <td className="mono" style={{ color: c.kind === 'removed' ? 'var(--bad)' : undefined }}>
                {c.a !== null && c.a !== undefined ? String(typeof c.a === 'object' ? JSON.stringify(c.a) : c.a) : <span className="muted">null</span>}
              </td>
              <td className="mono" style={{ color: c.kind === 'added' ? 'var(--ok)' : undefined }}>
                {c.b !== null && c.b !== undefined ? String(typeof c.b === 'object' ? JSON.stringify(c.b) : c.b) : <span className="muted">null</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
