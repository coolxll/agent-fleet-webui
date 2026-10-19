import { useState } from 'react'
import AgentIcon, { agentLabel } from './AgentIcon'
import { getUpdateCommand } from '../api'
import { useI18n } from '../i18n'

export type UpgradeTarget = {
  agent: string
  currentVersion?: string | null
  latestVersion?: string | null
  host?: string | null
  runtimeKey?: string | null
  osName?: string | null
  packageName?: string | null
  releaseUrl?: string | null
}

type Props = {
  target: UpgradeTarget
  onClose: () => void
}

export function UpgradeIcon({
  size = 14,
  title,
  onClick
}: {
  size?: number
  title?: string
  onClick?: (e: React.MouseEvent) => void
}) {
  return (
    <span
      className="tailscale-upgrade-btn"
      title={title || 'Update available'}
      onClick={onClick}
      role={onClick ? 'button' : undefined}
      tabIndex={onClick ? 0 : undefined}
    >
      <svg width={size} height={size} viewBox="0 0 16 16" fill="none">
        <circle cx="8" cy="8" r="7" stroke="currentColor" strokeWidth="1.2" />
        <path
          d="M8 11.5V4.5M8 4.5L5 7.5M8 4.5L11 7.5"
          stroke="currentColor"
          strokeWidth="1.3"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </span>
  )
}

export default function UpgradeModal({ target, onClose }: Props) {
  const { t } = useI18n()
  const [copiedType, setCopiedType] = useState<'remote' | 'local' | null>(null)
  const [tab, setTab] = useState<'remote' | 'local'>('remote')

  const commands = getUpdateCommand(target.agent, target.packageName, target.host)

  const handleCopy = (text: string, type: 'remote' | 'local') => {
    navigator.clipboard.writeText(text)
    setCopiedType(type)
    setTimeout(() => {
      setCopiedType(null)
    }, 2000)
  }

  const activeCmd = tab === 'remote' ? commands.remote : commands.local

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-card" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <AgentIcon agent={target.agent} size={24} />
            <div>
              <div style={{ fontWeight: 600, fontSize: 15 }}>
                {t('upgrade.title', { agent: agentLabel(target.agent) })}
              </div>
              <div className="muted small">
                {target.host || 'local'}
                {target.runtimeKey ? ` (${target.runtimeKey})` : ''}
                {target.osName ? ` · ${target.osName}` : ''}
              </div>
            </div>
          </div>
          <button className="btn small" onClick={onClose} style={{ padding: '3px 9px' }}>
            ✕
          </button>
        </div>

        <div className="modal-body">
          <div className="upgrade-version-compare">
            <div className="version-pill current">
              <span className="muted small">{t('upgrade.currentVersion')}</span>
              <span className="mono bold">{target.currentVersion || '—'}</span>
            </div>
            <div className="version-arrow">➔</div>
            <div className="version-pill latest">
              <span className="muted small">{t('upgrade.latestAvailable')}</span>
              <span className="mono bold" style={{ color: 'var(--ok)' }}>
                {target.latestVersion || '—'}
              </span>
            </div>
          </div>

          <div style={{ margin: '14px 0 8px', display: 'flex', gap: 6 }}>
            <button
              className={'btn small ' + (tab === 'remote' ? 'primary' : '')}
              onClick={() => setTab('remote')}
            >
              {t('upgrade.tabRemote')}
            </button>
            <button
              className={'btn small ' + (tab === 'local' ? 'primary' : '')}
              onClick={() => setTab('local')}
            >
              {t('upgrade.tabLocal')}
            </button>
          </div>

          <div className="cmd-box">
            <pre className="mono cmd-text">{activeCmd}</pre>
            <button
              className="btn small"
              onClick={() => handleCopy(activeCmd, tab)}
              style={{ minWidth: 70 }}
            >
              {copiedType === tab ? t('common.copied') : t('common.copy')}
            </button>
          </div>

          {target.packageName && (
            <div className="muted small" style={{ marginTop: 12 }}>
              {t('upgrade.packageLabel')}:{' '}
              <a
                href={
                  target.releaseUrl ||
                  `https://www.npmjs.com/package/${target.packageName}`
                }
                target="_blank"
                rel="noreferrer"
                style={{ color: 'var(--accent)', textDecoration: 'none' }}
              >
                {target.packageName}
              </a>
            </div>
          )}
        </div>

        <div className="modal-foot">
          <button className="btn" onClick={onClose}>
            {t('common.close')}
          </button>
        </div>
      </div>
    </div>
  )
}
