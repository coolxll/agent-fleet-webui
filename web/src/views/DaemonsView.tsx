import React, { useEffect, useMemo, useState } from 'react'
import {
  getDaemons,
  refreshDaemons,
  refreshPaseo,
  refreshHerdr,
  type PaseoObservation,
  type HerdrObservation,
} from '../daemonApi'
import UpgradeModal, { UpgradeIcon, UpgradeTarget } from '../components/UpgradeModal'
import { useI18n } from '../i18n'
import './daemons.css'

type Props = {
  refreshSignal?: number
  setToast?: (message: string) => void
  runtimeId?: string
  compact?: boolean
  serviceFilterDefault?: 'all' | 'paseo' | 'herdr'
}

type StatusFilter = 'all' | 'running' | 'stopped' | 'not_installed' | 'error' | 'upgrades'
type GroupByDimension = 'machine' | 'daemon'

type UnifiedDaemonRow = {
  key: string
  service: 'paseo' | 'herdr'
  serviceLabel: string
  runtime_id: string
  host_id: string
  host: string
  runtime_key: string
  enabled: boolean
  status: string
  installed: boolean | null
  version: string | null
  cli_version: string | null
  latest_version?: string | null
  upgrade_available?: boolean
  package?: string | null
  release_url?: string | null
  checked_at: string | null
  last_success_at: string | null
  stale: boolean
  error_code: string | null
  error: string | null
  endpoint: string | null
  workspaces: any[]
  sessions: any[]
  sessions_status: string
  isWin: boolean
}

type MachineDaemonGroup = {
  key: string
  host_id: string
  host: string
  runtime_id: string
  runtime_key: string
  isWin: boolean
  herdr?: HerdrObservation
  paseo?: PaseoObservation
}

export function RuntimeDaemonsSummary({
  runtimeId,
  refreshSignal,
  setToast,
}: {
  runtimeId: string
  refreshSignal?: number
  setToast?: (msg: string) => void
}) {
  return <DaemonsView runtimeId={runtimeId} refreshSignal={refreshSignal} setToast={setToast} compact />
}

export default function DaemonsView({
  refreshSignal,
  setToast,
  runtimeId,
  compact = false,
  serviceFilterDefault = 'all',
}: Props) {
  const { language } = useI18n()
  const zh = language === 'zh'
  const [groupBy, setGroupBy] = useState<GroupByDimension>(() => {
    return (localStorage.getItem('fleet_daemons_group_by') as GroupByDimension) || 'machine'
  })
  const [excludeWin, setExcludeWin] = useState<boolean>(() => {
    const saved = localStorage.getItem('fleet_daemons_exclude_win')
    return saved === null ? true : saved !== 'false'
  })
  const [filter, setFilter] = useState<'all' | 'paseo' | 'herdr'>(serviceFilterDefault)
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')
  const [search, setSearch] = useState('')
  const [viewMode, setViewMode] = useState<'table' | 'cards'>(() => {
    return (localStorage.getItem('fleet_daemons_view_mode') as 'table' | 'cards') || 'table'
  })
  const [paseoItems, setPaseoItems] = useState<PaseoObservation[]>([])
  const [herdrItems, setHerdrItems] = useState<HerdrObservation[]>([])
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [expandedKeys, setExpandedKeys] = useState<Set<string>>(new Set())
  const [upgradeTarget, setUpgradeTarget] = useState<UpgradeTarget | null>(null)

  const label = (s: string) =>
    ({
      unknown: zh ? '尚未检查' : 'Not checked',
      running: zh ? '运行中' : 'Running',
      not_installed: zh ? '未安装 / 缺失' : 'Not installed',
      stopped: zh ? '未启动' : 'Stopped',
      not_ready: zh ? '尚未就绪' : 'Not ready',
      connection_failed: zh ? '连接失败' : 'Connection failed',
      authentication_failed: zh ? '认证失败' : 'Auth failed',
      unsupported: zh ? '接口不兼容' : 'Unsupported',
      timeout: zh ? '检查超时' : 'Timed out',
      internal_error: zh ? '检查失败' : 'Failed',
      ok: zh ? '正常' : 'Available',
      unavailable: zh ? '不可用' : 'Unavailable',
    }[s] || s)

  const date = (value: string | null) => (value ? new Date(value).toLocaleString() : '—')

  const toggleExpand = (key: string) => {
    setExpandedKeys(prev => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  const handleSetViewMode = (mode: 'table' | 'cards') => {
    setViewMode(mode)
    localStorage.setItem('fleet_daemons_view_mode', mode)
  }

  const handleSetGroupBy = (dim: GroupByDimension) => {
    setGroupBy(dim)
    localStorage.setItem('fleet_daemons_group_by', dim)
  }

  const handleToggleExcludeWin = (checked: boolean) => {
    setExcludeWin(checked)
    localStorage.setItem('fleet_daemons_exclude_win', checked ? 'true' : 'false')
  }

  useEffect(() => {
    let active = true
    setLoading(true)
    getDaemons(runtimeId)
      .then(res => {
        if (!active) return
        setPaseoItems(res.paseo || [])
        setHerdrItems(res.herdr || [])
        setError(null)
      })
      .catch(e => {
        if (active) setError(e.message)
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [runtimeId, refreshSignal])

  async function handleRefreshAll() {
    setBusy('all')
    setError(null)
    try {
      const res = await refreshDaemons(runtimeId ? [runtimeId] : undefined)
      setPaseoItems(res.paseo || [])
      setHerdrItems(res.herdr || [])
      setToast?.(zh ? '所有守护服务检查完成' : 'All daemon checks completed')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  async function handleRefreshOne(service: 'paseo' | 'herdr', targetRuntimeId: string) {
    const key = `${service}:${targetRuntimeId}`
    setBusy(key)
    setError(null)
    try {
      if (service === 'paseo') {
        const res = await refreshPaseo([targetRuntimeId])
        setPaseoItems(old =>
          old.map(row => res.items.find(x => x.runtime_id === row.runtime_id) || row)
        )
      } else {
        const res = await refreshHerdr([targetRuntimeId])
        setHerdrItems(old =>
          old.map(row => res.items.find(x => x.runtime_id === row.runtime_id) || row)
        )
      }
      setToast?.(
        zh
          ? `${service === 'paseo' ? 'Paseo' : 'Herdr'} 状态检查完成`
          : `${service === 'paseo' ? 'Paseo' : 'Herdr'} check completed`
      )
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  async function handleRefreshMachine(targetRuntimeId: string) {
    const key = `machine:${targetRuntimeId}`
    setBusy(key)
    setError(null)
    try {
      const res = await refreshDaemons([targetRuntimeId])
      setPaseoItems(old =>
        old.map(row => res.paseo.find(x => x.runtime_id === row.runtime_id) || row)
      )
      setHerdrItems(old =>
        old.map(row => res.herdr.find(x => x.runtime_id === row.runtime_id) || row)
      )
      setToast?.(zh ? '机器守护服务刷新完成' : 'Machine daemons refreshed')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  const checkIsWin = (host: string, transport?: string) => {
    return host.endsWith('-win') || transport === 'windows'
  }

  // Unified list of individual daemon rows
  const allDaemonRows: UnifiedDaemonRow[] = useMemo(() => {
    const list: UnifiedDaemonRow[] = []

    if (filter === 'all' || filter === 'herdr') {
      for (const item of herdrItems) {
        const isWin = checkIsWin(item.host, item.connection.transport)
        if (excludeWin && isWin) continue

        list.push({
          key: `herdr:${item.runtime_id}`,
          service: 'herdr',
          serviceLabel: 'Herdr',
          runtime_id: item.runtime_id,
          host_id: item.host_id,
          host: item.host,
          runtime_key: item.runtime_key,
          enabled: item.enabled,
          status: item.status,
          installed: item.installed,
          version: item.version,
          cli_version: item.cli_version,
          latest_version: item.latest_version,
          upgrade_available: item.upgrade_available,
          package: item.package,
          release_url: item.release_url,
          checked_at: item.checked_at,
          last_success_at: item.last_success_at,
          stale: item.stale,
          error_code: item.error_code,
          error: item.error,
          endpoint: item.connection.socket,
          workspaces: item.workspaces || [],
          sessions: item.sessions || [],
          sessions_status: item.sessions_status,
          isWin,
        })
      }
    }

    if (filter === 'all' || filter === 'paseo') {
      for (const item of paseoItems) {
        const isWin = checkIsWin(item.host, item.connection.transport)
        if (excludeWin && isWin) continue

        list.push({
          key: `paseo:${item.runtime_id}`,
          service: 'paseo',
          serviceLabel: 'Paseo',
          runtime_id: item.runtime_id,
          host_id: item.host_id,
          host: item.host,
          runtime_key: item.runtime_key,
          enabled: item.enabled,
          status: item.status,
          installed: item.installed,
          version: item.version,
          cli_version: item.cli_version,
          latest_version: item.latest_version,
          upgrade_available: item.upgrade_available,
          package: item.package,
          release_url: item.release_url,
          checked_at: item.checked_at,
          last_success_at: item.last_success_at,
          stale: item.stale,
          error_code: item.error_code,
          error: item.error,
          endpoint: item.connection.endpoint,
          workspaces: [],
          sessions: item.sessions || [],
          sessions_status: item.sessions_status,
          isWin,
        })
      }
    }

    // Sort by Host name, then runtime_key, then service
    list.sort((a, b) => {
      if (a.host !== b.host) return a.host.localeCompare(b.host)
      if (a.runtime_key !== b.runtime_key) return a.runtime_key.localeCompare(b.runtime_key)
      return a.service.localeCompare(b.service)
    })

    if (!search.trim()) return list
    const q = search.toLowerCase()
    return list.filter(
      r =>
        r.host.toLowerCase().includes(q) ||
        r.runtime_key.toLowerCase().includes(q) ||
        r.serviceLabel.toLowerCase().includes(q) ||
        (r.endpoint && r.endpoint.toLowerCase().includes(q))
    )
  }, [excludeWin, filter, herdrItems, paseoItems, search])

  // Machine-grouped data
  const machineGroups: MachineDaemonGroup[] = useMemo(() => {
    const map = new Map<string, MachineDaemonGroup>()

    for (const h of herdrItems) {
      const isWin = checkIsWin(h.host, h.connection.transport)
      if (excludeWin && isWin) continue
      const key = h.runtime_id || h.host
      if (!map.has(key)) {
        map.set(key, {
          key,
          host_id: h.host_id,
          host: h.host,
          runtime_id: h.runtime_id,
          runtime_key: h.runtime_key,
          isWin,
          herdr: h,
        })
      } else {
        map.get(key)!.herdr = h
      }
    }

    for (const p of paseoItems) {
      const isWin = checkIsWin(p.host, p.connection.transport)
      if (excludeWin && isWin) continue
      const key = p.runtime_id || p.host
      if (!map.has(key)) {
        map.set(key, {
          key,
          host_id: p.host_id,
          host: p.host,
          runtime_id: p.runtime_id,
          runtime_key: p.runtime_key,
          isWin,
          paseo: p,
        })
      } else {
        map.get(key)!.paseo = p
      }
    }

    const list = Array.from(map.values())
    list.sort((a, b) => {
      if (a.host !== b.host) return a.host.localeCompare(b.host)
      return a.runtime_key.localeCompare(b.runtime_key)
    })

    if (!search.trim()) return list
    const q = search.toLowerCase()
    return list.filter(
      m =>
        m.host.toLowerCase().includes(q) ||
        m.runtime_key.toLowerCase().includes(q) ||
        (m.herdr?.connection.socket && m.herdr.connection.socket.toLowerCase().includes(q)) ||
        (m.paseo?.connection.endpoint && m.paseo.connection.endpoint.toLowerCase().includes(q))
    )
  }, [excludeWin, herdrItems, paseoItems, search])

  // Summary counts computed over active dataset
  const stats = useMemo(() => {
    let running = 0
    let stopped = 0
    let notInstalled = 0
    let error = 0
    let upgrades = 0
    for (const r of allDaemonRows) {
      if (r.status === 'running') running++
      else if (r.status === 'stopped' || r.status === 'not_ready') stopped++
      else if (r.status === 'not_installed' || r.installed === false) notInstalled++
      else error++

      if (r.upgrade_available && r.latest_version) upgrades++
    }
    return {
      running,
      stopped,
      notInstalled,
      error,
      upgrades,
      total: allDaemonRows.length,
      machinesCount: machineGroups.length,
    }
  }, [allDaemonRows, machineGroups.length])

  // Filtered daemon rows based on status
  const daemonRows = useMemo(() => {
    if (statusFilter === 'all') return allDaemonRows
    if (statusFilter === 'running') return allDaemonRows.filter(r => r.status === 'running')
    if (statusFilter === 'stopped') {
      return allDaemonRows.filter(r => r.status === 'stopped' || r.status === 'not_ready')
    }
    if (statusFilter === 'not_installed') {
      return allDaemonRows.filter(r => r.status === 'not_installed' || r.installed === false)
    }
    if (statusFilter === 'error') {
      return allDaemonRows.filter(
        r =>
          r.status === 'internal_error' ||
          r.status === 'timeout' ||
          r.status === 'connection_failed' ||
          r.status === 'authentication_failed' ||
          r.status === 'unsupported' ||
          !!r.error
      )
    }
    if (statusFilter === 'upgrades') {
      return allDaemonRows.filter(r => Boolean(r.upgrade_available && r.latest_version))
    }
    return allDaemonRows
  }, [allDaemonRows, statusFilter])

  // Filtered machine groups based on status
  const filteredMachineGroups = useMemo(() => {
    if (statusFilter === 'all') return machineGroups

    const matchStatus = (d?: { status: string; installed?: boolean | null; error?: any; upgrade_available?: boolean; latest_version?: any }) => {
      if (!d) return false
      if (statusFilter === 'running') return d.status === 'running'
      if (statusFilter === 'stopped') return d.status === 'stopped' || d.status === 'not_ready'
      if (statusFilter === 'not_installed') return d.status === 'not_installed' || d.installed === false
      if (statusFilter === 'error') {
        return (
          d.status === 'internal_error' ||
          d.status === 'timeout' ||
          d.status === 'connection_failed' ||
          d.status === 'authentication_failed' ||
          d.status === 'unsupported' ||
          !!d.error
        )
      }
      if (statusFilter === 'upgrades') return Boolean(d.upgrade_available && d.latest_version)
      return true
    }

    return machineGroups.filter(m => matchStatus(m.herdr) || matchStatus(m.paseo))
  }, [machineGroups, statusFilter])

  return (
    <section className={'daemons-view' + (compact ? ' daemons-compact' : '')}>
      <div className="daemons-header">
        <div className="daemons-title">
          <h2>
            {compact
              ? zh
                ? '常驻守护服务 (Daemons & Supervisors)'
                : 'Daemons & Supervisors'
              : zh
              ? '守护服务与宿主'
              : 'Daemons & Supervisors'}
          </h2>
          {!compact && (
            <p>
              {zh
                ? '支持按机器或按 Daemon 自由切换，纳管各环境中的 Paseo、Herdr 等守护服务。'
                : 'Centralized observability for Paseo, Herdr and background daemons across all hosts.'}
            </p>
          )}
        </div>

        <div className="daemons-actions">
          {!compact && (
            <>
              {/* Group By Dimension Toggle */}
              <div className="daemons-dimension-switch">
                <button
                  className={'daemons-dimension-btn' + (groupBy === 'machine' ? ' active' : '')}
                  onClick={() => handleSetGroupBy('machine')}
                  title={zh ? '按机器节点分组统览' : 'Group by machine'}
                >
                  🖥️ {zh ? '按机器看' : 'By Machine'} ({machineGroups.length})
                </button>
                <button
                  className={'daemons-dimension-btn' + (groupBy === 'daemon' ? ' active' : '')}
                  onClick={() => handleSetGroupBy('daemon')}
                  title={zh ? '按守护服务展开' : 'View by daemon'}
                >
                  ⚡ {zh ? '按 Daemon 看' : 'By Daemon'} ({allDaemonRows.length})
                </button>
              </div>

              {/* Service filter tabs (if By Daemon) */}
              {groupBy === 'daemon' && (
                <div className="daemons-filter-tabs">
                  <button
                    className={'daemons-filter-btn' + (filter === 'all' ? ' active' : '')}
                    onClick={() => setFilter('all')}
                  >
                    {zh ? '全部' : 'All'}
                  </button>
                  <button
                    className={'daemons-filter-btn' + (filter === 'herdr' ? ' active' : '')}
                    onClick={() => setFilter('herdr')}
                  >
                    Herdr
                  </button>
                  <button
                    className={'daemons-filter-btn' + (filter === 'paseo' ? ' active' : '')}
                    onClick={() => setFilter('paseo')}
                  >
                    Paseo
                  </button>
                </div>
              )}

              {/* Exclude Windows Hosts Toggle */}
              <label className="daemons-toggle-label" title={zh ? '忽略名称包含 -win 的 Windows 主机' : 'Exclude -win Windows hosts'}>
                <input
                  type="checkbox"
                  checked={excludeWin}
                  onChange={e => handleToggleExcludeWin(e.target.checked)}
                />
                <span>{zh ? '排除 -win 主机' : 'Exclude -win'}</span>
              </label>

              {/* Status Filter dropdown */}
              <select
                value={statusFilter}
                onChange={e => setStatusFilter(e.target.value as StatusFilter)}
                style={{
                  padding: '5px 10px',
                  fontSize: 12,
                  borderRadius: 6,
                  border: '1px solid var(--border)',
                  background: 'var(--panel)',
                  color: 'inherit',
                  cursor: 'pointer',
                }}
              >
                <option value="all">{zh ? '全部状态' : 'All Status'} ({stats.total})</option>
                <option value="running">{zh ? '● 运行中' : '● Running'} ({stats.running})</option>
                <option value="stopped">{zh ? '● 未启动' : '● Stopped'} ({stats.stopped})</option>
                <option value="not_installed">{zh ? '● 未安装' : '● Not installed'} ({stats.notInstalled})</option>
                {stats.error > 0 && <option value="error">{zh ? '● 异常' : '● Errors'} ({stats.error})</option>}
                {stats.upgrades > 0 && <option value="upgrades">{zh ? '↑ 可升级' : '↑ Has Update'} ({stats.upgrades})</option>}
              </select>

              {/* Keyword search */}
              <input
                type="text"
                placeholder={zh ? '搜索主机 / 端点…' : 'Filter hosts / endpoints…'}
                value={search}
                onChange={e => setSearch(e.target.value)}
                style={{
                  padding: '5px 10px',
                  fontSize: 12,
                  borderRadius: 6,
                  border: '1px solid var(--border)',
                  background: 'var(--panel)',
                  color: 'inherit',
                  width: 140,
                }}
              />

              {/* View Switcher */}
              <div className="daemons-view-switch">
                <button
                  className={'daemons-view-switch-btn' + (viewMode === 'table' ? ' active' : '')}
                  onClick={() => handleSetViewMode('table')}
                  title={zh ? '表格视图' : 'Table View'}
                >
                  ☰ {zh ? '表格' : 'Table'}
                </button>
                <button
                  className={'daemons-view-switch-btn' + (viewMode === 'cards' ? ' active' : '')}
                  onClick={() => handleSetViewMode('cards')}
                  title={zh ? '卡片视图' : 'Cards View'}
                >
                  ▦ {zh ? '卡片' : 'Cards'}
                </button>
              </div>
            </>
          )}

          <button
            className="btn small"
            disabled={!!busy || loading}
            onClick={handleRefreshAll}
          >
            {busy === 'all' ? (zh ? '检查中…' : 'Checking…') : zh ? '刷新全部服务' : 'Refresh All'}
          </button>
        </div>
      </div>

      {/* Summary stats bar with clickable filter pills */}
      {!compact && (
        <div className="daemons-summary-bar">
          <span
            className={'daemon-stat-pill' + (statusFilter === 'all' ? ' active' : '')}
            onClick={() => setStatusFilter('all')}
            title={zh ? '显示全部服务' : 'Show all services'}
          >
            {zh ? '全部实例' : 'All'}: <strong>{stats.total}</strong>
          </span>

          <span
            className={'daemon-stat-pill' + (statusFilter === 'running' ? ' active' : '')}
            onClick={() => setStatusFilter(statusFilter === 'running' ? 'all' : 'running')}
            title={zh ? '筛选运行中的服务' : 'Filter running services'}
          >
            <span className="daemon-stat-dot ok" />
            {zh ? '运行中' : 'Running'}: <strong>{stats.running}</strong>
          </span>

          <span
            className={'daemon-stat-pill' + (statusFilter === 'stopped' ? ' active' : '')}
            onClick={() => setStatusFilter(statusFilter === 'stopped' ? 'all' : 'stopped')}
            title={zh ? '筛选未启动的服务' : 'Filter stopped services'}
          >
            <span className="daemon-stat-dot warn" />
            {zh ? '未启动' : 'Stopped'}: <strong>{stats.stopped}</strong>
          </span>

          <span
            className={'daemon-stat-pill' + (statusFilter === 'not_installed' ? ' active' : '')}
            onClick={() => setStatusFilter(statusFilter === 'not_installed' ? 'all' : 'not_installed')}
            title={zh ? '筛选未安装的服务' : 'Filter not installed'}
          >
            <span className="daemon-stat-dot unknown" />
            {zh ? '未安装' : 'Not installed'}: <strong>{stats.notInstalled}</strong>
          </span>

          {stats.error > 0 && (
            <span
              className={'daemon-stat-pill' + (statusFilter === 'error' ? ' active' : '')}
              onClick={() => setStatusFilter(statusFilter === 'error' ? 'all' : 'error')}
              title={zh ? '筛选异常或失败的服务' : 'Filter errors'}
            >
              <span className="daemon-stat-dot bad" />
              {zh ? '异常 / 失败' : 'Errors'}: <strong>{stats.error}</strong>
            </span>
          )}

          {stats.upgrades > 0 && (
            <span
              className={'daemon-stat-pill' + (statusFilter === 'upgrades' ? ' active' : '')}
              onClick={() => setStatusFilter(statusFilter === 'upgrades' ? 'all' : 'upgrades')}
              title={zh ? '筛选有新版本的服务' : 'Filter services with updates'}
            >
              <span style={{ color: 'var(--warn, #f59e0b)', fontSize: 11 }}>▲</span>
              {zh ? '可升级' : 'Upgrades'}: <strong>{stats.upgrades}</strong>
            </span>
          )}

          {(statusFilter !== 'all' || search || !excludeWin) && (
            <button
              className="btn small"
              style={{
                padding: '2px 8px',
                fontSize: 11,
                background: 'rgba(255,255,255,0.06)',
                borderColor: 'var(--border)',
                cursor: 'pointer',
              }}
              onClick={() => {
                setStatusFilter('all')
                setSearch('')
                setExcludeWin(true)
              }}
            >
              ✕ {zh ? '重置过滤' : 'Reset filter'}
            </button>
          )}

          <span style={{ color: 'var(--muted)', marginLeft: 'auto', fontSize: 12 }}>
            {groupBy === 'machine'
              ? zh
                ? `共 ${filteredMachineGroups.length} 台主机 (${stats.total} 个服务实例)`
                : `${filteredMachineGroups.length} machines (${stats.total} instances)`
              : zh
              ? `共 ${daemonRows.length} 个服务实例`
              : `${daemonRows.length} instances`}
          </span>
        </div>
      )}

      {error && <div role="alert" className="daemon-error" style={{ marginBottom: 12 }}>{error}</div>}
      {loading && !allDaemonRows.length && (
        <p className="muted">{zh ? '正在读取守护服务状态…' : 'Loading daemons status…'}</p>
      )}

      {/* ========================================================= */}
      {/* 视角 1: 按机器看 (BY MACHINE)                             */}
      {/* ========================================================= */}
      {groupBy === 'machine' && (
        <>
          {viewMode === 'table' ? (
            <div className="daemons-table-container">
              <table className="daemons-table">
                <thead>
                  <tr>
                    <th>{zh ? '主机 (Machine)' : 'Machine / Host'}</th>
                    <th>{zh ? 'Herdr 守护服务' : 'Herdr Daemon'}</th>
                    <th>{zh ? 'Paseo 守护服务' : 'Paseo Daemon'}</th>
                    <th>{zh ? '活跃会话 / 拓扑' : 'Active Topology'}</th>
                    <th style={{ textAlign: 'right' }}>{zh ? '操作' : 'Actions'}</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredMachineGroups.map(m => {
                    const isExpanded = expandedKeys.has(m.key)
                    const isBusy = busy === `machine:${m.runtime_id}`
                    const herdrSessCount = m.herdr?.sessions?.length || 0
                    const paseoSessCount = m.paseo?.sessions?.length || 0
                    const totalSessions = herdrSessCount + paseoSessCount
                    const herdrWsCount = m.herdr?.workspaces?.length || 0

                    return (
                      <React.Fragment key={m.key}>
                        <tr className={'daemon-row' + (isExpanded ? ' expanded' : '')}>
                          {/* Machine Name */}
                          <td>
                            <strong>{m.host}</strong>
                            <div className="muted small">
                              {m.runtime_key !== 'native' ? m.runtime_key : m.isWin ? 'Windows' : 'POSIX'}
                            </div>
                          </td>

                          {/* Herdr Column */}
                          <td>
                            {m.herdr ? (
                              <div className="machine-daemon-cell">
                                <div className="machine-daemon-header">
                                  <img src="/icons/herdr.svg" width={16} height={16} alt="Herdr" style={{ borderRadius: 3 }} />
                                  <span
                                    className={
                                      'tag ' +
                                      (m.herdr.status === 'running'
                                        ? 'ok'
                                        : m.herdr.status === 'unknown'
                                        ? 'unknown'
                                        : 'warn')
                                    }
                                  >
                                    {label(m.herdr.status)}
                                  </span>
                                  {m.herdr.version && <span className="mono small">v{m.herdr.version}</span>}
                                  {m.herdr.upgrade_available && m.herdr.latest_version && (
                                    <span
                                      className="tag warn"
                                      style={{ cursor: 'pointer', padding: '1px 5px', fontSize: 10, display: 'inline-flex', alignItems: 'center', gap: 2 }}
                                      title={zh ? `升级到 v${m.herdr.latest_version}` : `Upgrade to v${m.herdr.latest_version}`}
                                      onClick={() =>
                                        setUpgradeTarget({
                                          agent: 'herdr',
                                          currentVersion: m.herdr?.version,
                                          latestVersion: m.herdr?.latest_version,
                                          host: m.host,
                                          runtimeKey: m.runtime_key,
                                          packageName: m.herdr?.package,
                                          releaseUrl: m.herdr?.release_url,
                                          runtimeId: m.runtime_id,
                                        })
                                      }
                                    >
                                      <UpgradeIcon size={10} />
                                      ↑ v{m.herdr.latest_version}
                                    </span>
                                  )}
                                </div>
                                {m.herdr.connection.socket && (
                                  <div className="machine-daemon-endpoint" title={m.herdr.connection.socket}>
                                    {m.herdr.connection.socket.length > 28
                                      ? '…' + m.herdr.connection.socket.slice(-24)
                                      : m.herdr.connection.socket}
                                  </div>
                                )}
                              </div>
                            ) : (
                              <span className="muted">—</span>
                            )}
                          </td>

                          {/* Paseo Column */}
                          <td>
                            {m.paseo ? (
                              <div className="machine-daemon-cell">
                                <div className="machine-daemon-header">
                                  <img src="/icons/paseo.svg" width={16} height={16} alt="Paseo" style={{ borderRadius: 3 }} />
                                  <span
                                    className={
                                      'tag ' +
                                      (m.paseo.status === 'running'
                                        ? 'ok'
                                        : m.paseo.status === 'unknown'
                                        ? 'unknown'
                                        : 'warn')
                                    }
                                  >
                                    {label(m.paseo.status)}
                                  </span>
                                  {m.paseo.version && <span className="mono small">v{m.paseo.version}</span>}
                                  {m.paseo.upgrade_available && m.paseo.latest_version && (
                                    <span
                                      className="tag warn"
                                      style={{ cursor: 'pointer', padding: '1px 5px', fontSize: 10, display: 'inline-flex', alignItems: 'center', gap: 2 }}
                                      title={zh ? `升级到 v${m.paseo.latest_version}` : `Upgrade to v${m.paseo.latest_version}`}
                                      onClick={() =>
                                        setUpgradeTarget({
                                          agent: 'paseo',
                                          currentVersion: m.paseo?.version,
                                          latestVersion: m.paseo?.latest_version,
                                          host: m.host,
                                          runtimeKey: m.runtime_key,
                                          packageName: m.paseo?.package,
                                          releaseUrl: m.paseo?.release_url,
                                          runtimeId: m.runtime_id,
                                        })
                                      }
                                    >
                                      <UpgradeIcon size={10} />
                                      ↑ v{m.paseo.latest_version}
                                    </span>
                                  )}
                                </div>
                                {m.paseo.connection.endpoint && (
                                  <div className="machine-daemon-endpoint">{m.paseo.connection.endpoint}</div>
                                )}
                              </div>
                            ) : (
                              <span className="muted">—</span>
                            )}
                          </td>

                          {/* Workspaces & Sessions */}
                          <td>
                            <button
                              className="btn small"
                              onClick={() => toggleExpand(m.key)}
                              style={{
                                padding: '3px 8px',
                                fontSize: 11,
                                background: isExpanded ? 'rgba(255,255,255,0.1)' : undefined,
                              }}
                            >
                              {herdrWsCount > 0 ? `${herdrWsCount} ${zh ? '工作区' : 'ws'} · ` : ''}
                              {totalSessions} {zh ? '会话' : 'sessions'}
                              <span style={{ marginLeft: 4 }}>{isExpanded ? '▲' : '▼'}</span>
                            </button>
                          </td>

                          {/* Machine Actions */}
                          <td style={{ textAlign: 'right' }}>
                            <button
                              className="btn small"
                              disabled={!!busy}
                              onClick={() => handleRefreshMachine(m.runtime_id)}
                              title={zh ? '刷新当前机器的所有守护进程' : 'Refresh all daemons on this machine'}
                            >
                              {isBusy ? '…' : zh ? '整机刷新' : 'Refresh'}
                            </button>
                          </td>
                        </tr>

                        {/* Expandable row showing machine's detailed topology */}
                        {isExpanded && (
                          <tr className="daemon-expand-row">
                            <td colSpan={5}>
                              <div className="daemon-expand-content">
                                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: 16 }}>
                                  {/* Herdr detail */}
                                  <div>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8 }}>
                                      <img src="/icons/herdr.svg" width={16} height={16} alt="Herdr" />
                                      <strong>Herdr</strong>
                                      <span className="muted small">({m.herdr?.status || 'not installed'})</span>
                                      {m.herdr && (
                                        <button
                                          className="btn small"
                                          style={{ marginLeft: 'auto', padding: '1px 6px', fontSize: 10 }}
                                          onClick={() => handleRefreshOne('herdr', m.runtime_id)}
                                        >
                                          {busy === `herdr:${m.runtime_id}` ? '…' : zh ? '刷新' : 'Refresh'}
                                        </button>
                                      )}
                                    </div>
                                    {m.herdr?.workspaces && m.herdr.workspaces.length > 0 && (
                                      <div style={{ marginBottom: 6 }}>
                                        <div className="daemon-workspaces">
                                          {m.herdr.workspaces.map(ws => (
                                            <span className="daemon-workspace-tag" key={ws.workspace_id || Math.random()}>
                                              🏷️ {ws.label || ws.workspace_id} ({ws.pane_count}p)
                                            </span>
                                          ))}
                                        </div>
                                      </div>
                                    )}
                                    {m.herdr?.sessions?.map(s => (
                                      <div className="daemon-session-item" key={s.external_id}>
                                        <div className="daemon-session-title">
                                          <span>{s.title || s.external_id}</span>
                                          <span className={'tag ' + (s.status === 'working' ? 'ok' : 'unknown')}>{s.status}</span>
                                        </div>
                                        <div className="muted small">{s.provider_label} · <span className="mono">{s.external_id}</span></div>
                                      </div>
                                    ))}
                                    {(!m.herdr?.sessions || m.herdr.sessions.length === 0) && (
                                      <div className="muted small">{zh ? '暂无活跃 Herdr Panes' : 'No active panes'}</div>
                                    )}
                                  </div>

                                  {/* Paseo detail */}
                                  <div>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8 }}>
                                      <img src="/icons/paseo.svg" width={16} height={16} alt="Paseo" />
                                      <strong>Paseo</strong>
                                      <span className="muted small">({m.paseo?.status || 'not installed'})</span>
                                      {m.paseo && (
                                        <button
                                          className="btn small"
                                          style={{ marginLeft: 'auto', padding: '1px 6px', fontSize: 10 }}
                                          onClick={() => handleRefreshOne('paseo', m.runtime_id)}
                                        >
                                          {busy === `paseo:${m.runtime_id}` ? '…' : zh ? '刷新' : 'Refresh'}
                                        </button>
                                      )}
                                    </div>
                                    {m.paseo?.sessions?.map(s => (
                                      <div className="daemon-session-item" key={s.external_id}>
                                        <div className="daemon-session-title">
                                          <span>{s.title || s.external_id}</span>
                                          <span className="tag unknown">{s.status}</span>
                                        </div>
                                        <div className="muted small">{s.provider_label} · <span className="mono">{s.external_id}</span></div>
                                      </div>
                                    ))}
                                    {(!m.paseo?.sessions || m.paseo.sessions.length === 0) && (
                                      <div className="muted small">{zh ? '暂无托管 Paseo 会话' : 'No managed sessions'}</div>
                                    )}
                                  </div>
                                </div>
                              </div>
                            </td>
                          </tr>
                        )}
                      </React.Fragment>
                    )
                  })}
                  {filteredMachineGroups.length === 0 && !loading && (
                    <tr>
                      <td colSpan={5} style={{ textAlign: 'center', padding: '24px 0', color: 'var(--muted)' }}>
                        {zh ? '未找到符合条件的主机' : 'No machines found matching query'}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          ) : (
            /* Card view for By Machine */
            <div className="daemons-grid">
              {filteredMachineGroups.map(m => {
                const isExpanded = expandedKeys.has(m.key)
                const isBusy = busy === `machine:${m.runtime_id}`
                return (
                  <article className="daemon-card" key={m.key}>
                    <div className="daemon-card-head">
                      <div>
                        <strong>{m.host}</strong>
                        <span className="muted" style={{ marginLeft: 6, fontSize: 12 }}>
                          {m.runtime_key !== 'native' ? m.runtime_key : m.isWin ? 'Windows' : 'POSIX'}
                        </span>
                      </div>
                      <button
                        className="btn small"
                        disabled={!!busy}
                        onClick={() => handleRefreshMachine(m.runtime_id)}
                      >
                        {isBusy ? '…' : zh ? '整机刷新' : 'Refresh'}
                      </button>
                    </div>

                    <div className="machine-daemon-grid-2">
                      {/* Herdr Box */}
                      <div style={{ background: 'rgba(255,255,255,0.02)', padding: 10, borderRadius: 8, border: '1px solid var(--border)' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
                          <img src="/icons/herdr.svg" width={16} height={16} alt="Herdr" />
                          <strong>Herdr</strong>
                        </div>
                        {m.herdr ? (
                          <>
                            <div className="daemon-badges">
                              <span className={'tag ' + (m.herdr.status === 'running' ? 'ok' : 'warn')}>
                                {label(m.herdr.status)}
                              </span>
                              {m.herdr.version && <span className="mono small">v{m.herdr.version}</span>}
                              {m.herdr.upgrade_available && m.herdr.latest_version && (
                                <span
                                  className="tag warn"
                                  style={{ cursor: 'pointer', padding: '1px 4px', fontSize: 10 }}
                                  onClick={() =>
                                    setUpgradeTarget({
                                      agent: 'herdr',
                                      currentVersion: m.herdr?.version,
                                      latestVersion: m.herdr?.latest_version,
                                      host: m.host,
                                      runtimeKey: m.runtime_key,
                                      packageName: m.herdr?.package,
                                      releaseUrl: m.herdr?.release_url,
                                      runtimeId: m.runtime_id,
                                    })
                                  }
                                >
                                  ↑ v{m.herdr.latest_version}
                                </span>
                              )}
                            </div>
                            <div className="mono small muted" style={{ marginTop: 4, overflowWrap: 'anywhere' }}>
                              {m.herdr.connection.socket || '—'}
                            </div>
                          </>
                        ) : (
                          <div className="muted small">{zh ? '未部署' : 'Not deployed'}</div>
                        )}
                      </div>

                      {/* Paseo Box */}
                      <div style={{ background: 'rgba(255,255,255,0.02)', padding: 10, borderRadius: 8, border: '1px solid var(--border)' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
                          <img src="/icons/paseo.svg" width={16} height={16} alt="Paseo" />
                          <strong>Paseo</strong>
                        </div>
                        {m.paseo ? (
                          <>
                            <div className="daemon-badges">
                              <span className={'tag ' + (m.paseo.status === 'running' ? 'ok' : 'warn')}>
                                {label(m.paseo.status)}
                              </span>
                              {m.paseo.version && <span className="mono small">v{m.paseo.version}</span>}
                              {m.paseo.upgrade_available && m.paseo.latest_version && (
                                <span
                                  className="tag warn"
                                  style={{ cursor: 'pointer', padding: '1px 4px', fontSize: 10 }}
                                  onClick={() =>
                                    setUpgradeTarget({
                                      agent: 'paseo',
                                      currentVersion: m.paseo?.version,
                                      latestVersion: m.paseo?.latest_version,
                                      host: m.host,
                                      runtimeKey: m.runtime_key,
                                      packageName: m.paseo?.package,
                                      releaseUrl: m.paseo?.release_url,
                                      runtimeId: m.runtime_id,
                                    })
                                  }
                                >
                                  ↑ v{m.paseo.latest_version}
                                </span>
                              )}
                            </div>
                            <div className="mono small muted" style={{ marginTop: 4, overflowWrap: 'anywhere' }}>
                              {m.paseo.connection.endpoint || '—'}
                            </div>
                          </>
                        ) : (
                          <div className="muted small">{zh ? '未部署' : 'Not deployed'}</div>
                        )}
                      </div>
                    </div>

                    <button
                      className="btn small"
                      style={{ marginTop: 6 }}
                      onClick={() => toggleExpand(m.key)}
                      aria-expanded={isExpanded}
                    >
                      {zh ? '展开活跃拓扑与会话' : 'Expand Sessions'}
                    </button>

                    {isExpanded && (
                      <div className="daemon-sessions-box">
                        {m.herdr?.workspaces && m.herdr.workspaces.length > 0 && (
                          <div style={{ marginBottom: 8 }}>
                            <div className="muted small" style={{ marginBottom: 4 }}>Herdr Workspaces:</div>
                            <div className="daemon-workspaces">
                              {m.herdr.workspaces.map(ws => (
                                <span className="daemon-workspace-tag" key={ws.workspace_id || Math.random()}>
                                  🏷️ {ws.label || ws.workspace_id} ({ws.pane_count}p)
                                </span>
                              ))}
                            </div>
                          </div>
                        )}
                        <div className="muted small" style={{ marginBottom: 4 }}>Active Sessions:</div>
                        {[...(m.herdr?.sessions || []), ...(m.paseo?.sessions || [])].map((s, idx) => (
                          <div className="daemon-session-item" key={s.external_id || idx}>
                            <div className="daemon-session-title">
                              <strong>{s.title || s.external_id}</strong>
                              <span className="tag unknown">{s.status}</span>
                            </div>
                            <div className="muted small">{s.provider_label} · <span className="mono">{s.external_id}</span></div>
                          </div>
                        ))}
                      </div>
                    )}
                  </article>
                )
              })}
            </div>
          )}
        </>
      )}

      {/* ========================================================= */}
      {/* 视角 2: 按 Daemon 看 (BY DAEMON)                           */}
      {/* ========================================================= */}
      {groupBy === 'daemon' && (
        <>
          {viewMode === 'table' ? (
            <div className="daemons-table-container">
              <table className="daemons-table">
                <thead>
                  <tr>
                    <th>{zh ? '主机与环境' : 'Host & Runtime'}</th>
                    <th>{zh ? '服务' : 'Service'}</th>
                    <th>{zh ? '运行状态' : 'Status'}</th>
                    <th>{zh ? '监听端点 / Socket' : 'Endpoint / Socket'}</th>
                    <th>{zh ? '版本' : 'Version'}</th>
                    <th>{zh ? '会话与工作区' : 'Workspaces & Sessions'}</th>
                    <th style={{ textAlign: 'right' }}>{zh ? '操作' : 'Actions'}</th>
                  </tr>
                </thead>
                <tbody>
                  {daemonRows.map(item => {
                    const isExpanded = expandedKeys.has(item.key)
                    const isBusy = busy === item.key
                    const sessionCount = item.sessions.length
                    const wsCount = item.workspaces.length
                    return (
                      <React.Fragment key={item.key}>
                        <tr className={'daemon-row' + (isExpanded ? ' expanded' : '')}>
                          <td>
                            <strong>{item.host}</strong>
                            <span className="muted" style={{ marginLeft: 6, fontSize: 12 }}>
                              {item.runtime_key}
                            </span>
                          </td>

                          <td>
                            <div className="daemon-service-identity">
                              {item.service === 'paseo' ? (
                                <img
                                  src="/icons/paseo.svg"
                                  width={18}
                                  height={18}
                                  alt="Paseo"
                                  style={{ verticalAlign: 'middle', borderRadius: 3 }}
                                />
                              ) : (
                                <img
                                  src="/icons/herdr.svg"
                                  width={18}
                                  height={18}
                                  alt="Herdr"
                                  style={{ verticalAlign: 'middle', borderRadius: 3 }}
                                />
                              )}
                              <strong>{item.serviceLabel}</strong>
                            </div>
                          </td>

                          <td>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                              <span
                                className={
                                  'tag ' +
                                  (item.status === 'running'
                                    ? 'ok'
                                    : item.status === 'unknown'
                                    ? 'unknown'
                                    : 'warn')
                                }
                              >
                                {label(item.status)}
                              </span>
                              {item.stale && <span className="tag unknown">{zh ? '待确认' : 'Stale'}</span>}
                            </div>
                            {item.error && (
                              <div className="daemon-error small" style={{ marginTop: 2 }}>
                                {zh ? label(item.error_code || item.status) : item.error}
                              </div>
                            )}
                          </td>

                          <td className="mono small">
                            {item.endpoint ? (
                              <span title={item.endpoint}>
                                {item.endpoint.length > 36
                                  ? '…' + item.endpoint.slice(-32)
                                  : item.endpoint}
                              </span>
                            ) : (
                              <span className="muted">—</span>
                            )}
                          </td>

                          <td>
                            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                              <span className="mono small">{item.version ? `v${item.version}` : '—'}</span>
                              {item.upgrade_available && item.latest_version && (
                                <span
                                  className="tag warn"
                                  style={{
                                    cursor: 'pointer',
                                    padding: '1px 5px',
                                    fontSize: 10,
                                    display: 'inline-flex',
                                    alignItems: 'center',
                                    gap: 3,
                                  }}
                                  title={
                                    zh
                                      ? `发现新版本 v${item.latest_version}，点击查看升级命令`
                                      : `Update available: v${item.latest_version}. Click to upgrade.`
                                  }
                                  onClick={() =>
                                    setUpgradeTarget({
                                      agent: item.service,
                                      currentVersion: item.version,
                                      latestVersion: item.latest_version,
                                      host: item.host,
                                      runtimeKey: item.runtime_key,
                                      packageName: item.package,
                                      releaseUrl: item.release_url,
                                      runtimeId: item.runtime_id,
                                    })
                                  }
                                >
                                  <UpgradeIcon size={10} />
                                  ↑ v{item.latest_version}
                                </span>
                              )}
                            </div>
                          </td>

                          <td>
                            <button
                              className="btn small"
                              onClick={() => toggleExpand(item.key)}
                              style={{
                                padding: '3px 8px',
                                fontSize: 11,
                                background: isExpanded ? 'rgba(255,255,255,0.1)' : undefined,
                              }}
                            >
                              {item.service === 'herdr'
                                ? `${wsCount} ${zh ? '工作区' : 'ws'} · ${sessionCount} ${zh ? 'Panes' : 'panes'}`
                                : `${sessionCount} ${zh ? '托管会话' : 'sessions'}`}
                              <span style={{ marginLeft: 4 }}>{isExpanded ? '▲' : '▼'}</span>
                            </button>
                          </td>

                          <td style={{ textAlign: 'right' }}>
                            <button
                              className="btn small"
                              disabled={!!busy || !item.enabled}
                              onClick={() => handleRefreshOne(item.service, item.runtime_id)}
                            >
                              {isBusy ? '…' : zh ? '刷新' : 'Refresh'}
                            </button>
                          </td>
                        </tr>

                        {/* Expandable Detail Row */}
                        {isExpanded && (
                          <tr className="daemon-expand-row">
                            <td colSpan={7}>
                              <div className="daemon-expand-content">
                                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8, fontSize: 12 }}>
                                  <span className="muted">
                                    {zh ? '最近成功' : 'Last success'}: {date(item.last_success_at)} · {zh ? '检查时间' : 'Checked'}: {date(item.checked_at)}
                                  </span>
                                  <span className="muted small">
                                    {item.service === 'herdr' ? 'Source: Herdr MUX' : 'Source: Paseo CLI'}
                                  </span>
                                </div>

                                {item.workspaces.length > 0 && (
                                  <div style={{ marginBottom: 10 }}>
                                    <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 4, color: 'var(--muted)' }}>
                                      {zh ? '活跃工作区 (Workspaces)' : 'Active Workspaces'}:
                                    </div>
                                    <div className="daemon-workspaces">
                                      {item.workspaces.map(ws => (
                                        <span className="daemon-workspace-tag" key={ws.workspace_id || Math.random()}>
                                          🏷️ <strong>{ws.label || ws.workspace_id}</strong> ({ws.pane_count}p / {ws.tab_count}t)
                                        </span>
                                      ))}
                                    </div>
                                  </div>
                                )}

                                <div>
                                  <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6, color: 'var(--muted)' }}>
                                    {zh ? '会话与 Agent 详情' : 'Sessions Details'} ({item.sessions.length}):
                                  </div>
                                  {item.sessions.map((s, idx) => (
                                    <div className="daemon-session-item" key={s.external_id || idx}>
                                      <div className="daemon-session-title">
                                        <span>
                                          <strong>{s.title || s.external_id}</strong>
                                          <span className="muted small" style={{ marginLeft: 8 }}>
                                            {s.provider_label} · <span className="mono">{s.external_id}</span>
                                          </span>
                                        </span>
                                        <span className={'tag ' + (s.status === 'working' || s.status === 'running' ? 'ok' : 'unknown')}>
                                          {s.status}
                                        </span>
                                      </div>
                                      {s.cwd && <div className="mono small muted">{s.cwd}</div>}
                                    </div>
                                  ))}
                                  {item.sessions.length === 0 && (
                                    <p className="muted small" style={{ margin: 0 }}>
                                      {zh ? '暂无活跃会话。' : 'No active sessions.'}
                                    </p>
                                  )}
                                </div>
                              </div>
                            </td>
                          </tr>
                        )}
                      </React.Fragment>
                    )
                  })}
                  {daemonRows.length === 0 && !loading && (
                    <tr>
                      <td colSpan={7} style={{ textAlign: 'center', padding: '24px 0', color: 'var(--muted)' }}>
                        {zh ? '未找到符合条件的守护服务' : 'No daemon services found matching query'}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          ) : (
            /* Card view for By Daemon */
            <div className="daemons-grid">
              {daemonRows.map(item => {
                const isExpanded = expandedKeys.has(item.key)
                const isBusy = busy === item.key
                return (
                  <article className="daemon-card" key={item.key}>
                    <div className="daemon-card-head">
                      <div className="daemon-service-identity">
                        {item.service === 'paseo' ? (
                          <img
                            src="/icons/paseo.svg"
                            width={22}
                            height={22}
                            alt="Paseo"
                            style={{ verticalAlign: 'middle', borderRadius: 4 }}
                          />
                        ) : (
                          <img
                            src="/icons/herdr.svg"
                            width={22}
                            height={22}
                            alt="Herdr"
                            style={{ verticalAlign: 'middle', borderRadius: 4 }}
                          />
                        )}
                        <div>
                          <strong>{item.serviceLabel}</strong>
                          <span className="muted" style={{ marginLeft: 6, fontSize: 12 }}>
                            {item.host} / {item.runtime_key}
                          </span>
                        </div>
                      </div>
                      <button
                        className="btn small"
                        disabled={!!busy || !item.enabled}
                        onClick={() => handleRefreshOne(item.service, item.runtime_id)}
                      >
                        {isBusy ? '…' : zh ? '刷新' : 'Refresh'}
                      </button>
                    </div>

                    <div className="daemon-badges">
                      <span className={'tag ' + (item.status === 'running' ? 'ok' : 'warn')}>
                        {label(item.status)}
                      </span>
                      {item.stale && <span className="tag unknown">{zh ? '待确认' : 'Stale'}</span>}
                      {item.version && <span className="mono small">v{item.version}</span>}
                      {item.upgrade_available && item.latest_version && (
                        <span
                          className="tag warn"
                          style={{
                            cursor: 'pointer',
                            padding: '1px 6px',
                            fontSize: 11,
                            display: 'inline-flex',
                            alignItems: 'center',
                            gap: 3,
                          }}
                          onClick={() =>
                            setUpgradeTarget({
                              agent: item.service,
                              currentVersion: item.version,
                              latestVersion: item.latest_version,
                              host: item.host,
                              runtimeKey: item.runtime_key,
                              packageName: item.package,
                              releaseUrl: item.release_url,
                              runtimeId: item.runtime_id,
                            })
                          }
                        >
                          <UpgradeIcon size={11} />
                          ↑ v{item.latest_version}
                        </span>
                      )}
                      {item.workspaces.length > 0 && (
                        <span className="tag ok">{item.workspaces.length} {zh ? '工作区' : 'workspaces'}</span>
                      )}
                    </div>

                    <dl className="daemon-facts">
                      <dt>{zh ? '通信端点' : 'Endpoint'}</dt>
                      <dd className="mono small">{item.endpoint || '—'}</dd>
                      <dt>{zh ? '最近检查' : 'Last checked'}</dt>
                      <dd>{date(item.checked_at)}</dd>
                      <dt>{zh ? '最近成功' : 'Last success'}</dt>
                      <dd>{date(item.last_success_at)}</dd>
                    </dl>

                    <button
                      className="btn small"
                      style={{ marginTop: 4 }}
                      onClick={() => toggleExpand(item.key)}
                      aria-expanded={isExpanded}
                    >
                      {zh ? '活跃会话' : 'Sessions'} ({item.sessions.length}) · {label(item.sessions_status)}
                    </button>

                    {isExpanded && (
                      <div className="daemon-sessions-box">
                        {item.sessions.map((s, idx) => (
                          <div className="daemon-session-item" key={s.external_id || idx}>
                            <div className="daemon-session-title">
                              <strong>{s.title || s.external_id}</strong>
                              <span className="tag unknown">{s.status}</span>
                            </div>
                            <div className="muted small">{s.provider_label} · <span className="mono">{s.external_id}</span></div>
                          </div>
                        ))}
                      </div>
                    )}
                  </article>
                )
              })}
            </div>
          )}
        </>
      )}

      {upgradeTarget && (
        <UpgradeModal
          target={upgradeTarget}
          onClose={() => setUpgradeTarget(null)}
          onSubmitted={() => {
            setToast?.(
              zh
                ? `已触发 ${upgradeTarget.agent} 升级任务`
                : `Upgrade triggered for ${upgradeTarget.agent}`
            )
          }}
        />
      )}
    </section>
  )
}
