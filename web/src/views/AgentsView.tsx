import { useEffect, useState } from 'react'
import { api } from '../api'
import AgentIcon from '../components/AgentIcon'

export default function AgentsView() {
  const [agents, setAgents] = useState<{ id: string; name: string }[]>([])
  const [counts, setCounts] = useState<Record<string, number>>({})
  const [selected, setSelected] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    api.overview().then((ov) => {
      if (!alive) return
      setAgents(ov.agents.map((a) => ({ id: a.id, name: a.id })))
      setCounts(Object.fromEntries(ov.agents.map((a) => [a.id, a.installations])))
    })
    return () => {
      alive = false
    }
  }, [])

  if (selected) {
    return <AgentFleet agentId={selected} onBack={() => setSelected(null)} />
  }

  return (
    <div>
      <div className="panel">
        <h2>Pick an agent</h2>
        <div className="body">
          <div className="machine-grid">
            {agents.map((a) => (
              <div className="machine-card" key={a.id} onClick={() => setSelected(a.id)}>
                <div className="machine-card-head">
                  <AgentIcon agent={a.id} size={22} />
                  <span className="machine-name">{a.id}</span>
                </div>
                <div className="machine-os small muted">
                  {a.id in counts ? counts[a.id] + ' installation(s) across the fleet' : ''}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

function AgentFleet({ agentId, onBack }: { agentId: string; onBack: () => void }) {
  const [data, setData] = useState<any>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    api
      .agentView(agentId)
      .then((d) => alive && setData(d))
      .catch((e: any) => alive && setError(String(e.message || e)))
    return () => {
      alive = false
    }
  }, [agentId])

  if (error) return <div className="err">{error}</div>
  if (!data) return <div className="muted">Loading...</div>

  const v = data.version
  return (
    <div>
      <div className="cards">
        <div className="card">
          <div className="label">Agent</div>
          <div className="value" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <AgentIcon agent={data.agent} size={24} />
            {data.name}
          </div>
          <div className="hint">
            <button className="btn" onClick={onBack} style={{ marginTop: 6 }}>
              ← all agents
            </button>
          </div>
        </div>
        <div className="card">
          <div className="label">Installations</div>
          <div className="value">{data.detected_count}</div>
          <div className="hint">across the fleet</div>
        </div>
        <div className="card">
          <div className="label">
            {v.unknown_source ? 'Latest version' : 'Latest on npm'}
          </div>
          <div className="value mono" style={{ fontSize: 18 }}>
            {v.latest_available || <span className="muted">unknown</span>}
          </div>
          <div className="hint">
            {v.unknown_source
              ? 'no registry source for this agent'
              : 'via ' + v.package}
          </div>
        </div>
        <div className="card">
          <div className="label">Up to date</div>
          <div className="value" style={{ color: 'var(--ok)' }}>
            {v.up_to_date}
          </div>
          <div className="hint">{v.behind} behind</div>
        </div>
        <div className="card">
          <div className="label">Config</div>
          <div className="value" style={{ fontSize: 18 }}>
            {data.consistent_configs ? 'consistent' : data.config_groups.length + ' variants'}
          </div>
          <div className="hint">
            {data.consistent_configs ? 'same model/provider everywhere' : 'machines differ'}
          </div>
        </div>
      </div>

      <div className="panel">
        <h2>Installations</h2>
        <div className="body">
          <table>
            <thead>
              <tr>
                <th>Machine</th>
                <th>Version</th>
                <th>Currency</th>
                <th>Model</th>
                <th>Provider</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {data.installations
                .slice()
                .sort((a: any, b: any) => (a.host + a.runtime_key).localeCompare(b.host + b.runtime_key))
                .map((i: any) => (
                  <tr key={i.id}>
                    <td className="mono">
                      {i.host}/{i.runtime_key}
                      {i.collect_user ? <span className="muted small"> @{i.collect_user}</span> : null}
                    </td>
                    <td className="mono">{i.version || '—'}</td>
                    <td>
                      <span
                        className={
                          'tag ' +
                          (i.version_state === 'latest'
                            ? 'ok'
                            : i.version_state === 'behind'
                              ? 'warn'
                              : 'unknown')
                        }
                      >
                        {i.version_state}
                      </span>
                    </td>
                    <td className="mono small">
                      {i.config_summary?.model || <span className="muted">—</span>}
                    </td>
                    <td className="small">{i.config_summary?.provider || '—'}</td>
                    <td>
                      <span className="tag">{i.status}</span>
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="panel">
        <h2>Configuration variants</h2>
        <div className="body">
          {data.consistent_configs ? (
            <div className="muted small">
              Every machine runs the same model / provider / endpoint.
            </div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Machines</th>
                  <th>model / provider / base URL</th>
                </tr>
              </thead>
              <tbody>
                {data.config_groups.map((g: any) => (
                  <tr key={g.signature}>
                    <td className="small">{g.count}</td>
                    <td className="mono small">{g.signature}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  )
}
