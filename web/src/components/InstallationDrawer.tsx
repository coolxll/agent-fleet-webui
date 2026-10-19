import { useEffect, useState } from 'react'
import { api, type InstallationDetail } from '../api'
import AgentDetail from './AgentDetail'
import AgentIcon, { agentLabel } from './AgentIcon'
import { useI18n } from '../i18n'

type Props = {
  installationId: string | null
  onClose: () => void
}

export default function InstallationDrawer({ installationId, onClose }: Props) {
  const { t } = useI18n()
  const [detail, setDetail] = useState<InstallationDetail | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!installationId) {
      setDetail(null)
      return
    }
    let alive = true
    setLoading(true)
    setError(null)
    api
      .installation(installationId)
      .then((d) => {
        if (!alive) return
        setDetail(d)
        setLoading(false)
      })
      .catch((e: any) => {
        if (!alive) return
        setError(String(e.message || e))
        setLoading(false)
      })
    return () => {
      alive = false
    }
  }, [installationId])

  if (!installationId) return null

  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <div className="drawer-panel" onClick={(e) => e.stopPropagation()}>
        <div className="drawer-head">
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            {detail ? (
              <>
                <AgentIcon agent={detail.agent} size={22} />
                <div>
                  <div style={{ fontWeight: 600, fontSize: 15 }}>
                    {agentLabel(detail.agent)}
                  </div>
                  <div className="muted small">
                    {detail.runtime?.host || '?'}
                    {detail.runtime?.key && detail.runtime?.key !== 'native'
                      ? ` [${detail.runtime.key}]`
                      : ''}
                  </div>
                </div>
              </>
            ) : (
              <span style={{ fontWeight: 600 }}>{t('agentDetail.configuration')}</span>
            )}
          </div>
          <button className="btn small" onClick={onClose} style={{ padding: '3px 9px' }}>
            ✕
          </button>
        </div>

        <div className="drawer-body">
          {loading && <div className="muted small" style={{ padding: 16 }}>{t('common.loading')}</div>}
          {error && <div className="err" style={{ margin: 16 }}>{error}</div>}
          {detail && !loading && <AgentDetail detail={detail} />}
        </div>
      </div>
    </div>
  )
}
