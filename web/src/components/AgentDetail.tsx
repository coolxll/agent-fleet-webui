import AgentIcon, { agentLabel } from './AgentIcon'
import type { InstallationDetail } from '../api'

export const STATUS_TAG: Record<string, string> = {
  installed: 'ok',
  config_only: 'warn',
  not_installed: 'unknown',
  unknown: 'unknown',
  error: 'bad',
  not_scanned: 'unknown'
}

export default function AgentDetail({
  detail,
  runtimeName
}: {
  detail: InstallationDetail
  runtimeName: string
}) {
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
                          (s.status === 'ok' ? 'ok' : s.status === 'missing' ? 'unknown' : 'warn')
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
