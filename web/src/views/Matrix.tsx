import { useEffect, useState } from 'react'
import { api, type MatrixColumn, type MatrixCell, type AgentMeta } from '../api'
import AgentIcon, { agentLabel } from '../components/AgentIcon'
import { useI18n, type TranslationKey } from '../i18n'

export default function Matrix({
  onOpenInstallation
}: {
  onOpenInstallation: (id: string, agentName?: string, hostName?: string) => void
}) {
  const { t } = useI18n()
  const [columns, setColumns] = useState<MatrixColumn[]>([])
  const [agents, setAgents] = useState<AgentMeta[]>([])
  const [error, setError] = useState<string | null>(null)

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

  if (error) return <div className="err">{error}</div>
  if (!columns.length) return <div className="muted">{t('common.loading')}</div>

  const sorted = [...columns].sort((a, b) =>
    (a.runtime.host + '/' + a.runtime.key).localeCompare(b.runtime.host + '/' + b.runtime.key)
  )

  return (
    <div>
      <div className="panel">
        <h2>
          {t('matrix.title')}{' '}
          <span className="muted" style={{ textTransform: 'none', letterSpacing: 0, fontWeight: 'normal', fontSize: 12 }}>
            {t('matrix.subtitle')}
          </span>
        </h2>
        <div className="body">
          <table>
            <thead>
              <tr>
                <th style={{ position: 'sticky', left: 0, background: 'var(--panel)' }}>{t('matrix.thAgent')}</th>
                {sorted.map((c) => (
                  <th key={c.runtime.id} className="small">
                    <div className="mono">{c.runtime.host}</div>
                    {c.runtime.key && c.runtime.key !== 'native' ? (
                      <span className="ts-tag" style={{ fontSize: 10, padding: '0 4px', marginTop: 2 }}>
                        {c.runtime.key}
                      </span>
                    ) : null}
                    {c.runtime.stale ? <span className="tag warn">{t('common.stale')}</span> : null}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {agents.map((a) => (
                <tr key={a.id}>
                  <td className="mono" style={{ position: 'sticky', left: 0, background: 'var(--panel)' }}>
                    <span className="agent-cell">
                      <AgentIcon agent={a.id} size={17} />
                      {agentLabel(a.id)}
                    </span>
                  </td>
                  {sorted.map((c) => {
                    const cell: MatrixCell | undefined = c.cells[a.id]
                    const state = cell?.state ?? 'unknown'
                    const label = getStateLabel(state)
                    return (
                      <td key={c.runtime.id}>
                        <button
                          className={'cell ' + state}
                          title={cell?.error || label}
                          onClick={() =>
                            cell?.installation_id &&
                            onOpenInstallation(cell.installation_id, agentLabel(a.id), c.runtime.host)
                          }
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
        </div>
      </div>
      <div className="muted small">
        {t('matrix.legend')}{' '}
        <span className="tag ok">{t('matrix.state.installed')}</span>{' '}
        <span className="tag">{t('matrix.state.not_installed')}</span>{' '}
        <span className="tag warn">{t('matrix.state.config_only')}</span>{' '}
        <span className="tag bad">{t('matrix.state.scan_failed')}</span>{' '}
        <span className="tag unknown">{t('matrix.state.not_scanned')} / {t('matrix.state.unknown')}</span>
      </div>
    </div>
  )
}
