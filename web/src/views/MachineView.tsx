import { useEffect, useState } from 'react'
import { api } from '../api'
import AgentIcon, { agentLabel } from '../components/AgentIcon'
import { useI18n, type TranslationKey } from '../i18n'

export default function MachineView({
  hostId,
  onOpenAgent
}: {
  hostId: string
  onOpenAgent: (id: string) => void
}) {
  const { t } = useI18n()
  const [data, setData] = useState<any>(null)
  const [error, setError] = useState<string | null>(null)

  const getStatusLabel = (status: string) => {
    const key = `matrix.state.${status}` as TranslationKey
    const label = t(key)
    return label !== key ? label : status
  }

  useEffect(() => {
    let alive = true
    api
      .machine(hostId)
      .then((d) => alive && setData(d))
      .catch((e: any) => alive && setError(String(e.message || e)))
    return () => {
      alive = false
    }
  }, [hostId])

  if (error) return <div className="err">{error}</div>
  if (!data) return <div className="muted">{t('common.loading')}</div>

  return (
    <div>
      <div className="cards">
        <div className="card">
          <div className="label">{t('machine.title')}</div>
          <div className="value" style={{ fontSize: 20 }}>
            {data.name}
          </div>
          <div className="hint">{data.hostname || ''}</div>
        </div>
        <div className="card">
          <div className="label">{t('machine.runtimes')}</div>
          <div className="value">{data.runtimes.length}</div>
          <div className="hint">
            {data.runtimes.map((r: any) => r.kind).join(', ')}
          </div>
        </div>
        <div className="card">
          <div className="label">{t('machine.agents')}</div>
          <div className="value">{data.agents_detected}</div>
          <div className="hint">{t('machine.detectedOnThis')}</div>
        </div>
        <div className="card">
          <div className="label">{t('machine.reachability')}</div>
          <div className="value" style={{ fontSize: 18 }}>
            {data.runtimes.some((r: any) => r.reachable === true)
              ? t('common.online')
              : data.runtimes.some((r: any) => r.reachable === false)
                ? t('common.offline')
                : t('common.unscanned')}
          </div>
          <div className="hint">
            {t('machine.errorsHint', {
              count: data.runtimes.filter((r: any) => r.last_error).length
            })}
          </div>
        </div>
      </div>

      {data.runtimes.map((rt: any) => (
        <div className="panel" key={rt.id}>
          <h2>
            <span className="mono">{rt.key}</span>
            <span className="tag">{rt.kind}</span>
            {rt.os_name ? <span className="muted small">{rt.os_name}</span> : null}
            <span
              className={
                'tag ' + (rt.reachable === true ? 'ok' : rt.reachable === false ? 'bad' : 'unknown')
              }
            >
              {rt.reachable === true
                ? t('common.up')
                : rt.reachable === false
                  ? t('common.down')
                  : t('common.unscanned')}
            </span>
            {rt.stale ? <span className="tag warn">{t('common.stale')}</span> : null}
          </h2>
          <div className="body">
            {rt.installations.length === 0 ? (
              <div className="muted small">{t('machine.noAgentsOnRuntime')}</div>
            ) : (
              <div className="agent-cards">
                {rt.installations.map((i: any) => {
                  const cs = i.config_summary || {}
                  return (
                    <div
                      className="agent-card"
                      key={i.id}
                      onClick={() => onOpenAgent(i.id)}
                    >
                      <div className="agent-card-top">
                        <AgentIcon agent={i.agent} size={22} />
                        <span className="agent-card-name">{agentLabel(i.agent)}</span>
                      </div>
                      <div className="agent-card-model mono">
                        {cs.model || <span className="muted">{t('machine.noModelSet')}</span>}
                      </div>
                      <div className="agent-card-provider small muted">
                        {cs.provider || '—'}
                        {cs.base_url ? ' · ' + cs.base_url : ''}
                      </div>
                      <div className="agent-card-foot">
                        <span className="mono small">{i.version ? 'v' + i.version : '—'}</span>
                        <span
                          className={
                            'tag ' +
                            (i.status === 'installed'
                              ? 'ok'
                              : i.status === 'config_only'
                                ? 'warn'
                                : i.status === 'not_installed'
                                  ? 'unknown'
                                  : 'bad')
                          }
                        >
                          {getStatusLabel(i.status)}
                        </span>
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        </div>
      ))}
    </div>
  )
}
