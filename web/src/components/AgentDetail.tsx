import AgentIcon, { agentLabel } from './AgentIcon'
import type { InstallationDetail } from '../api'
import { useI18n, type TranslationKey } from '../i18n'

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
  const { t } = useI18n()
  const c = detail.config
  const scopes = (c?.sanitized as any)?.scopes || (c?.settings as any)?.scopes || []

  const getStatusLabel = (status: string) => {
    const key = `matrix.state.${status}` as TranslationKey
    const label = t(key)
    return label !== key ? label : status
  }

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
            <dt>{t('agentDetail.status')}</dt>
            <dd>
              <span className={'tag ' + (STATUS_TAG[detail.status] || '')}>
                {getStatusLabel(detail.status)}
              </span>
            </dd>
            <dt>{t('agentDetail.version')}</dt>
            <dd className="mono">{detail.version || <span className="muted">{t('common.unknown')}</span>}</dd>
            <dt>{t('agentDetail.execPath')}</dt>
            <dd className="mono">{detail.exec_path || '-'}</dd>
            <dt>{t('agentDetail.installMethod')}</dt>
            <dd>{detail.install_method || <span className="muted">{t('common.unknown')}</span>}</dd>
            <dt>{t('agentDetail.configDir')}</dt>
            <dd className="mono">{detail.config_dir || '-'}</dd>
            <dt>{t('agentDetail.detectedAt')}</dt>
            <dd className="small">{detail.detected_at || t('common.never')}</dd>
            <dt>{t('agentDetail.runtime')}</dt>
            <dd className="small">
              {detail.runtime?.kind || '?'} · {detail.runtime?.os_name || 'unknown OS'} · {t('common.user')}{' '}
              {detail.runtime?.collect_user || '?'}
            </dd>
            {detail.error ? (
              <>
                <dt>{t('agentDetail.detectionError')}</dt>
                <dd className="err">{detail.error}</dd>
              </>
            ) : null}
          </dl>
        </div>
      </div>

      <div className="panel">
        <h2>{t('agentDetail.configuration')}</h2>
        <div className="body">
          {!c ? (
            <div className="muted small">
              {t('agentDetail.noConfig', { status: detail.config_status })}
            </div>
          ) : (
            <>
              <dl className="kv">
                <dt>{t('agentDetail.parseStatus')}</dt>
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
                    {c.resolution === 'declared' ? t('agentDetail.declared') : c.resolution}
                  </span>
                </dd>
                <dt>{t('agentDetail.model')}</dt>
                <dd className="mono">{c.model || <span className="muted">{t('common.unknown')}</span>}</dd>
                <dt>{t('agentDetail.provider')}</dt>
                <dd className="mono">{c.provider || <span className="muted">{t('common.unknown')}</span>}</dd>
                <dt>{t('agentDetail.baseUrl')}</dt>
                <dd className="mono">
                  {c.base_url || <span className="muted">{t('common.unknown')}</span>}
                  {c.base_url_masked ? <span className="tag warn">{t('agentDetail.masked')}</span> : null}
                </dd>
                <dt>{t('agentDetail.mcpServers')}</dt>
                <dd>
                  {c.mcp_servers && c.mcp_servers.length ? (
                    <table>
                      <thead>
                        <tr>
                          <th>{t('agentDetail.thName')}</th>
                          <th>{t('agentDetail.thTransport')}</th>
                          <th>{t('agentDetail.thCommandUrl')}</th>
                          <th>{t('agentDetail.thEnvKeys')}</th>
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
                    <span className="muted small">{t('agentDetail.none')}</span>
                  )}
                </dd>
                <dt>{t('agentDetail.skills')}</dt>
                <dd className="small">
                  {c.skills && c.skills.length ? (
                    <div>
                      {t('agentDetail.skillsCount', { count: c.skills.length })}{' '}
                      {c.skills
                        .slice(0, 25)
                        .map((s: any) => s.name)
                        .join(', ')}
                      {c.skills.length > 25 ? ', ...' : ''}
                    </div>
                  ) : (
                    <span className="muted">{t('agentDetail.none')}</span>
                  )}
                </dd>
                <dt>{t('agentDetail.scopes')}</dt>
                <dd>
                  {scopes.length === 0 ? (
                    <span className="muted small">{t('agentDetail.notReported')}</span>
                  ) : (
                    <table>
                      <thead>
                        <tr>
                          <th>{t('agentDetail.thScope')}</th>
                          <th>{t('agentDetail.thPath')}</th>
                          <th>{t('agentDetail.thState')}</th>
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
                <dt>{t('agentDetail.otherSettings')}</dt>
                <dd>
                  <pre>{JSON.stringify(c.settings ?? {}, null, 2)}</pre>
                </dd>
              </dl>
              {c.unsupported_fields?.length ? (
                <div className="warnings">
                  {t('agentDetail.notNormalizedPrefix')} {c.unsupported_fields.join(', ')}
                </div>
              ) : null}
            </>
          )}
        </div>
      </div>

      <div className="panel">
        <h2>{t('agentDetail.configSources')}</h2>
        <div className="body">
          {detail.sources.length === 0 ? (
            <div className="muted small">{t('agentDetail.noSources')}</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>{t('agentDetail.thScope')}</th>
                  <th>{t('agentDetail.thPath')}</th>
                  <th>{t('agentDetail.thFormat')}</th>
                  <th>{t('agentDetail.thState')}</th>
                  <th>{t('agentDetail.thSize')}</th>
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
          <h2>{t('agentDetail.sanitizedJson')}</h2>
          <div className="body">
            {c.warnings?.length ? (
              <div className="warnings">{t('agentDetail.warningsPrefix')} {c.warnings.join(' | ')}</div>
            ) : null}
            <pre>{JSON.stringify(c.sanitized ?? {}, null, 2)}</pre>
          </div>
        </div>
      ) : null}
    </div>
  )
}
