import { useEffect, useState } from 'react'
import { api, type MatrixColumn, type MatrixCell, type AgentMeta } from '../api'
import AgentIcon, { agentLabel } from '../components/AgentIcon'
import InstallationDrawer from '../components/InstallationDrawer'
import { useI18n, type TranslationKey } from '../i18n'

export default function Matrix({
  onOpenInstallation
}: {
  onOpenInstallation?: (id: string, agentName?: string, hostName?: string) => void
} = {}) {
  const { t, language } = useI18n()
  const [columns, setColumns] = useState<MatrixColumn[]>([])
  const [agents, setAgents] = useState<AgentMeta[]>([])
  const [error, setError] = useState<string | null>(null)
  const [selectedInstId, setSelectedInstId] = useState<string | null>(null)

  // Transpose mode: 'machine_rows' (Rows=Machines, Cols=Agents) vs 'agent_rows' (Rows=Agents, Cols=Machines)
  const [orientation, setOrientation] = useState<'machine_rows' | 'agent_rows'>(() => {
    return (
      (localStorage.getItem('agent_fleet_matrix_orientation') as
        | 'machine_rows'
        | 'agent_rows') || 'machine_rows'
    )
  })

  const toggleOrientation = () => {
    const next = orientation === 'machine_rows' ? 'agent_rows' : 'machine_rows'
    setOrientation(next)
    localStorage.setItem('agent_fleet_matrix_orientation', next)
  }

  const getStateLabel = (state: string): string => {
    const key = `matrix.state.${state}` as TranslationKey
    const label = t(key)
    return label !== key ? label : state
  }

  useEffect(() => {
    let alive = true
    const load = async () => {
      try {
        const m = await api.matrix()
        if (!alive) return
        setColumns(m.columns)
        setAgents(m.agents)
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

  const handleCellClick = (cell?: MatrixCell, agentId?: string, hostName?: string) => {
    if (!cell?.installation_id) return
    if (onOpenInstallation) {
      onOpenInstallation(cell.installation_id, agentId ? agentLabel(agentId) : undefined, hostName)
    } else {
      setSelectedInstId(cell.installation_id)
    }
  }

  if (error) return <div className="err">{error}</div>
  if (!columns.length) return <div className="muted">{t('common.loading')}</div>

  const sortedColumns = [...columns].sort((a, b) =>
    (a.runtime.host + '/' + a.runtime.key).localeCompare(b.runtime.host + '/' + b.runtime.key)
  )

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <div>
          <span style={{ fontWeight: 600, fontSize: 16 }}>{t('matrix.title')}</span>
          <span className="muted small" style={{ marginLeft: 8 }}>
            {t('matrix.subtitle')}
          </span>
        </div>
        <button
          className="btn small"
          onClick={toggleOrientation}
          title={language === 'zh' ? '切换矩阵行列方向' : 'Transpose matrix rows and columns'}
          style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
        >
          <span>⇄</span>
          <span>
            {orientation === 'machine_rows'
              ? language === 'zh'
                ? '转置: 主机 × Agent (当前)'
                : 'Transpose: Machine × Agent'
              : language === 'zh'
                ? '转置: Agent × 主机 (当前)'
                : 'Transpose: Agent × Machine'}
          </span>
        </button>
      </div>

      <div className="panel" style={{ overflow: 'hidden' }}>
        <div className="body" style={{ padding: 0, overflowX: 'auto' }}>
          {orientation === 'machine_rows' ? (
            // Mode A: Rows = Machines, Columns = Agents
            <table className="ts-table">
              <thead>
                <tr>
                  <th style={{ position: 'sticky', left: 0, background: 'var(--panel)', zIndex: 2, minWidth: 200 }}>
                    {t('overview.thHost')}
                  </th>
                  {agents.map((a) => (
                    <th key={a.id} style={{ textAlign: 'center', minWidth: 100 }}>
                      <span className="agent-cell" style={{ justifyContent: 'center' }}>
                        <AgentIcon agent={a.id} size={16} />
                        <span>{agentLabel(a.id)}</span>
                      </span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {sortedColumns.map((c) => {
                  const online = c.runtime.reachable === true
                  const offline = c.runtime.reachable === false
                  return (
                    <tr key={c.runtime.id}>
                      <td style={{ position: 'sticky', left: 0, background: 'var(--panel)', zIndex: 1 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                          <span className={'dot ' + (online ? 'on' : offline ? 'off' : 'unk')} />
                          <span className="mono bold" style={{ fontSize: 13.5 }}>
                            {c.runtime.host}
                          </span>
                          {c.runtime.key && c.runtime.key !== 'native' && (
                            <span className="ts-tag" style={{ fontSize: 10 }}>{c.runtime.key}</span>
                          )}
                        </div>
                        <div className="small muted" style={{ marginTop: 2 }}>
                          {c.runtime.os_name || c.runtime.kind}
                          {c.runtime.collect_user ? ` · @${c.runtime.collect_user}` : ''}
                        </div>
                      </td>

                      {agents.map((a) => {
                        const cell: MatrixCell | undefined = c.cells[a.id]
                        const state = cell?.state ?? 'unknown'
                        const label = getStateLabel(state)
                        return (
                          <td key={a.id} style={{ textAlign: 'center', verticalAlign: 'middle' }}>
                            <button
                              className={'cell ' + state}
                              title={cell?.error || label}
                              onClick={() => handleCellClick(cell, a.id, c.runtime.host)}
                              style={{ width: '100%', minWidth: 80 }}
                            >
                              <div>{label}</div>
                              {cell?.version ? <div className="mono">{cell.version}</div> : null}
                            </button>
                          </td>
                        )
                      })}
                    </tr>
                  )
                })}
              </tbody>
            </table>
          ) : (
            // Mode B: Rows = Agents, Columns = Machines
            <table className="ts-table">
              <thead>
                <tr>
                  <th style={{ position: 'sticky', left: 0, background: 'var(--panel)', zIndex: 2, minWidth: 160 }}>
                    {t('matrix.thAgent')}
                  </th>
                  {sortedColumns.map((c) => (
                    <th key={c.runtime.id} style={{ textAlign: 'center', minWidth: 120 }}>
                      <div className="mono bold">{c.runtime.host}</div>
                      {c.runtime.key && c.runtime.key !== 'native' && (
                        <span className="ts-tag" style={{ fontSize: 10, padding: '0 4px', marginTop: 2 }}>
                          {c.runtime.key}
                        </span>
                      )}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {agents.map((a) => (
                  <tr key={a.id}>
                    <td style={{ position: 'sticky', left: 0, background: 'var(--panel)', zIndex: 1 }}>
                      <span className="agent-cell">
                        <AgentIcon agent={a.id} size={18} />
                        <span className="bold">{agentLabel(a.id)}</span>
                      </span>
                    </td>
                    {sortedColumns.map((c) => {
                      const cell: MatrixCell | undefined = c.cells[a.id]
                      const state = cell?.state ?? 'unknown'
                      const label = getStateLabel(state)
                      return (
                        <td key={c.runtime.id} style={{ textAlign: 'center', verticalAlign: 'middle' }}>
                          <button
                            className={'cell ' + state}
                            title={cell?.error || label}
                            onClick={() => handleCellClick(cell, a.id, c.runtime.host)}
                            style={{ width: '100%', minWidth: 78 }}
                          >
                            <div>{label}</div>
                            {cell?.version ? <div className="mono">{cell.version}</div> : null}
                          </button>
                        </td>
                      )
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      <div className="muted small" style={{ marginTop: 10, display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span>{t('matrix.legend')}</span>
        <span className="tag ok">{t('matrix.state.installed')}</span>
        <span className="tag">{t('matrix.state.not_installed')}</span>
        <span className="tag warn">{t('matrix.state.config_only')}</span>
        <span className="tag bad">{t('matrix.state.scan_failed')}</span>
        <span className="tag unknown">{t('matrix.state.not_scanned')} / {t('matrix.state.unknown')}</span>
      </div>

      <InstallationDrawer
        installationId={selectedInstId}
        onClose={() => setSelectedInstId(null)}
      />
    </div>
  )
}
