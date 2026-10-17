import { useEffect, useState } from 'react'
import { api, type MatrixColumn, type MatrixCell, type AgentMeta } from '../api'

const STATE_LABEL: Record<string, string> = {
  installed: 'installed',
  not_installed: 'absent',
  config_only: 'config only',
  scan_failed: 'scan failed',
  not_scanned: 'not scanned',
  unknown: 'unknown',
  error: 'error'
}

export default function Matrix({
  onOpenInstallation
}: {
  onOpenInstallation: (id: string) => void
}) {
  const [columns, setColumns] = useState<MatrixColumn[]>([])
  const [agents, setAgents] = useState<AgentMeta[]>([])
  const [error, setError] = useState<string | null>(null)

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
    const t = setInterval(load, 10000)
    return () => {
      alive = false
      clearInterval(t)
    }
  }, [])

  if (error) return <div className="err">{error}</div>
  if (!columns.length) return <div className="muted">Loading...</div>

  const sorted = [...columns].sort((a, b) =>
    (a.runtime.host + '/' + a.runtime.key).localeCompare(b.runtime.host + '/' + b.runtime.key)
  )

  return (
    <div>
      <div className="panel">
        <h2>
          Agent &times; Machine matrix
          <span className="muted" style={{ textTransform: 'none', letterSpacing: 0 }}>
            click a cell to inspect its configuration
          </span>
        </h2>
        <div className="body">
          <table>
            <thead>
              <tr>
                <th style={{ position: 'sticky', left: 0, background: 'var(--panel)' }}>Agent</th>
                {sorted.map((c) => (
                  <th key={c.runtime.id} className="small">
                    <div className="mono">{c.runtime.host}</div>
                    <div className="muted">{c.runtime.key}</div>
                    {c.runtime.stale ? <span className="tag warn">stale</span> : null}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {agents.map((a) => (
                <tr key={a.id}>
                  <td className="mono" style={{ position: 'sticky', left: 0, background: 'var(--panel)' }}>
                    {a.id}
                  </td>
                  {sorted.map((c) => {
                    const cell: MatrixCell | undefined = c.cells[a.id]
                    const state = cell?.state ?? 'unknown'
                    return (
                      <td key={c.runtime.id}>
                        <button
                          className={'cell ' + state}
                          title={cell?.error || STATE_LABEL[state]}
                          onClick={() => cell?.installation_id && onOpenInstallation(cell.installation_id)}
                        >
                          <div>{STATE_LABEL[state] || state}</div>
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
        Legend: <span className="tag ok">installed</span>{' '}
        <span className="tag">absent</span>{' '}
        <span className="tag warn">config only</span>{' '}
        <span className="tag bad">scan failed</span>{' '}
        <span className="tag unknown">not scanned / unknown</span>
      </div>
    </div>
  )
}
