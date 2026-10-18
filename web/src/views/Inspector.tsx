import { useEffect, useMemo, useState } from 'react'
import { api, type Installation, type InstallationDetail, type Runtime } from '../api'
import AgentIcon, { agentLabel } from '../components/AgentIcon'

const STATUS_TAG: Record<string, string> = {
  installed: 'ok',
  config_only: 'warn',
  not_installed: 'unknown',
  unknown: 'unknown',
  error: 'bad',
  not_scanned: 'unknown'
}

export default function Inspector({
  initialRuntimeId,
  initialInstallationId
}: {
  initialRuntimeId?: string
  initialInstallationId?: string
}) {
  const [runtimes, setRuntimes] = useState<Runtime[]>([])
  const [agents, setAgents] = useState<{ id: string; name: string }[]>([])
  const [runtimeId, setRuntimeId] = useState<string>(initialRuntimeId || '')
  const [agentId, setAgentId] = useState<string>('')
  const [detectedOnly, setDetectedOnly] = useState(false)
  const [items, setItems] = useState<Installation[]>([])
  const [selected, setSelected] = useState<InstallationDetail | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    Promise.all([api.runtimes(), api.agents()]).then(([r, a]) => {
      setRuntimes(r.runtimes)
      setAgents(a.agents)
    })
  }, [])

  useEffect(() => {
    if (initialRuntimeId) setRuntimeId(initialRuntimeId)
  }, [initialRuntimeId])

  // Opening straight from a matrix cell: jump to that installation.
  useEffect(() => {
    if (!initialInstallationId) return
    let alive = true
    api
      .installation(initialInstallationId)
      .then((d) => {
        if (!alive) return
        setSelected(d)
        setRuntimeId(d.runtime_id)
      })
      .catch(() => undefined)
    return () => {
      alive = false
    }
  }, [initialInstallationId])

  useEffect(() => {
    let alive = true
    api
      .installations({
        runtime_id: runtimeId || undefined,
        agent_id: agentId || undefined,
        detected_only: detectedOnly || undefined
      })
      .then((res) => {
        if (!alive) return
        setItems(res.installations)
        setError(null)
      })
      .catch((e: any) => alive && setError(String(e.message || e)))
    return () => {
      alive = false
    }
  }, [runtimeId, agentId, detectedOnly])

  const open = async (id: string) => {
    const detail = await api.installation(id)
    setSelected(detail)
  }

  const runtimeName = useMemo(() => {
    const r = runtimes.find((x) => x.id === selected?.runtime_id)
    return r ? r.host + '/' + r.key : ''
  }, [runtimes, selected])

  return (
    <div>
      <div className="panel">
        <h2>Filters</h2>
        <div className="body filters">
          <select value={runtimeId} onChange={(e) => setRuntimeId(e.target.value)}>
            <option value="">All runtimes</option>
            {runtimes.map((r) => (
              <option key={r.id} value={r.id}>
                {r.host}/{r.key} ({r.kind})
              </option>
            ))}
          </select>
          <select value={agentId} onChange={(e) => setAgentId(e.target.value)}>
            <option value="">All agents</option>
            {agents.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
          <label className="small">
            <input
              type="checkbox"
              checked={detectedOnly}
              onChange={(e) => setDetectedOnly(e.target.checked)}
            />{' '}
            installed only
          </label>
          <span className="muted small">{items.length} installation(s)</span>
        </div>
      </div>

      {error ? <div className="err">{error}</div> : null}

      <div className="split">
        <div>
          {items.map((i) => (
            <div
              key={i.id}
              className={'list-item ' + (selected?.id === i.id ? 'selected' : '')}
              onClick={() => open(i.id)}
            >
              <div className="mono">
                <span className="agent-cell">
                  <AgentIcon agent={i.agent} size={17} />
                  {agentLabel(i.agent)}
                </span>{' '}
                <span className={'tag ' + (STATUS_TAG[i.status] || '')}>{i.status}</span>
              </div>
              <div className="small muted">
                {i.runtime?.host}/{i.runtime?.key} · {i.runtime?.collect_user || '-'}
              </div>
              <div className="small">{i.version ? 'v' + i.version : ''} {i.install_method || ''}</div>
            </div>
          ))}
          {items.length === 0 ? <div className="muted small">No installations match.</div> : null}
        </div>

        <div>
          {!selected ? (
            <div className="muted small">Select an installation to inspect its configuration.</div>
          ) : (
            <Detail detail={selected} runtimeName={runtimeName} />
          )}
        </div>
      </div>
    </div>
  )
}

function Detail({ detail, runtimeName }: { detail: InstallationDetail; runtimeName: string }) {
  const c = detail.config
  const scopes = (c?.sanitized as any)?.scopes || (c?.settings as any)?.scopes || []
  return (
    <div>
      <div className="panel">
        <h2>
          <span className="agent-cell">
            <AgentIcon agent={detail.agent} size={18} />
            {agentLabel(detail.agent)}
          </span>{' '}
          @ {runtimeName}
        </h2>
        <div className="body">
          <dl className="kv">
            <dt>Status</dt>
            <dd>
              <span className={'tag ' + (STATUS_TAG[detail.status] || '')}>{detail.status}</span>
            </dd>
            <dt>Version</dt>
            <dd className="mono">{detail.version || <span className="muted">unknown</span>}</dd>
            <dt>Exec path</dt>
            <dd className="mono">{detail.exec_path || '-'}</dd>
            <dt>Install method</dt>
            <dd>{detail.install_method || <span className="muted">unknown</span>}</dd>
            <dt>Config dir</dt>
            <dd className="mono">{detail.config_dir || '-'}</dd>
            <dt>Detected at</dt>
            <dd className="small">{detail.detected_at || 'never'}</dd>
            <dt>Runtime</dt>
            <dd className="small">
              {detail.runtime?.kind || '?'} · {detail.runtime?.os_name || 'unknown OS'} · user{' '}
              {detail.runtime?.collect_user || '?'}
            </dd>
            {detail.error ? (
              <>
                <dt>Detection error</dt>
                <dd className="err">{detail.error}</dd>
              </>
            ) : null}
          </dl>
        </div>
      </div>

      <div className="panel">
        <h2>Configuration</h2>
        <div className="body">
          {!c ? (
            <div className="muted small">
              No normalized configuration stored (status: {detail.config_status}).
            </div>
          ) : (
            <>
              <dl className="kv">
                <dt>Parse status</dt>
                <dd>
                  <span
                    className={
                      'tag ' +
                      (c.parse_status === 'parsed'
                        ? 'ok'
                        : c.parse_status === 'partial' || c.parse_status === 'empty'
                          ? 'warn'
                          : 'bad')
                    }
                  >
                    {c.parse_status}
                  </span>{' '}
                  <span className="tag">
                    {c.resolution === 'declared' ? 'as written in file' : c.resolution}
                  </span>
                </dd>
                <dt>Model</dt>
                <dd className="mono">{c.model || <span className="muted">unknown</span>}</dd>
                <dt>Provider</dt>
                <dd className="mono">{c.provider || <span className="muted">unknown</span>}</dd>
                <dt>Base URL</dt>
                <dd className="mono">
                  {c.base_url || <span className="muted">unknown</span>}
                  {c.base_url_masked ? <span className="tag warn">masked</span> : null}
                </dd>
                <dt>MCP servers</dt>
                <dd>
                  {c.mcp_servers && c.mcp_servers.length ? (
                    <table>
                      <thead>
                        <tr>
                          <th>Name</th>
                          <th>Transport</th>
                          <th>Command / URL</th>
                          <th>Env keys</th>
                        </tr>
                      </thead>
                      <tbody>
                        {c.mcp_servers.map((m: any) => (
                          <tr key={m.name}>
                            <td className="mono">{m.name}</td>
                            <td className="small">{m.transport}</td>
                            <td className="mono small">{m.url || m.command || '-'}</td>
                            <td className="small muted">
                              {(m.env_keys || []).join(', ') || '-'}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  ) : (
                    <span className="muted small">none</span>
                  )}
                </dd>
                <dt>Skills</dt>
                <dd className="small">
                  {c.skills && c.skills.length ? (
                    <div>
                      {c.skills.length} skill(s):{' '}
                      {c.skills
                        .slice(0, 25)
                        .map((s: any) => s.name)
                        .join(', ')}
                      {c.skills.length > 25 ? ', ...' : ''}
                    </div>
                  ) : (
                    <span className="muted">none</span>
                  )}
                </dd>
                <dt>Config scopes</dt>
                <dd>
                  {scopes.length === 0 ? (
                    <span className="muted small">not reported by this adapter</span>
                  ) : (
                    <table>
                      <thead>
                        <tr>
                          <th>Scope</th>
                          <th>Path</th>
                          <th>State</th>
                        </tr>
                      </thead>
                      <tbody>
                        {scopes.map((s: any) => (
                          <tr key={s.path}>
                            <td>
                              <span className="tag">{s.scope}</span>
                            </td>
                            <td className="mono small">{s.path}</td>
                            <td>
                              <span
                                className={
                                  'tag ' +
                                  (s.status === 'ok'
                                    ? 'ok'
                                    : s.status === 'missing'
                                      ? 'unknown'
                                      : s.status === 'not_probed'
                                        ? 'unknown'
                                        : 'warn')
                                }
                              >
                                {s.status}
                              </span>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </dd>
                <dt>Other settings</dt>
                <dd>
                  <pre>{JSON.stringify(c.settings ?? {}, null, 2)}</pre>
                </dd>
              </dl>
              {c.unsupported_fields?.length ? (
                <div className="warnings">
                  Not normalized (values never read): {c.unsupported_fields.join(', ')}
                </div>
              ) : null}
            </>
          )}
        </div>
      </div>

      <div className="panel">
        <h2>Config sources</h2>
        <div className="body">
          {detail.sources.length === 0 ? (
            <div className="muted small">No sources recorded.</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>Scope</th>
                  <th>Path</th>
                  <th>Format</th>
                  <th>Status</th>
                  <th>Size</th>
                </tr>
              </thead>
              <tbody>
                {detail.sources.map((s) => (
                  <tr key={s.id}>
                    <td className="small">{s.scope}</td>
                    <td className="mono small">{s.path}</td>
                    <td className="small">{s.format || '-'}</td>
                    <td>
                      <span
                        className={
                          'tag ' +
                          (s.status === 'ok'
                            ? 'ok'
                            : s.status === 'missing'
                              ? 'unknown'
                              : 'warn')
                        }
                      >
                        {s.status}
                      </span>
                    </td>
                    <td className="small muted">{s.size_bytes ?? '-'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      {c ? (
        <div className="panel">
          <h2>Sanitized configuration (JSON)</h2>
          <div className="body">
            {c.warnings?.length ? (
              <div className="warnings">Warnings: {c.warnings.join(' | ')}</div>
            ) : null}
            <pre>{JSON.stringify(c.sanitized ?? {}, null, 2)}</pre>
          </div>
        </div>
      ) : null}
    </div>
  )
}
