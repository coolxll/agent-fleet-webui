import { useState } from 'react'
import { AttentionItem, AttentionSummary } from '../api'
import { useI18n } from '../i18n'

type Props = {
  isOpen: boolean
  onClose: () => void
  onNavigate: (view: 'machines' | 'tasks', payload?: any) => void
  summary: AttentionSummary | null
  loading: boolean
  onRefresh: () => void
}

function formatAgo(isoString: string | null, t: any): string {
  if (!isoString) return ''
  const d = new Date(isoString)
  if (isNaN(d.getTime())) return ''
  const secs = Math.max(0, Math.floor((Date.now() - d.getTime()) / 1000))
  if (secs < 60) return t('time.ago.s', { n: secs })
  if (secs < 3600) return t('time.ago.m', { n: Math.floor(secs / 60) })
  if (secs < 86400) return t('time.ago.h', { n: Math.floor(secs / 3600) })
  return t('time.ago.d', { n: Math.floor(secs / 86400) })
}

export default function AttentionDrawer({
  isOpen,
  onClose,
  onNavigate,
  summary,
  loading,
  onRefresh,
}: Props) {
  const { t } = useI18n()
  const [filter, setFilter] = useState<'all' | 'error' | 'warning'>('all')

  if (!isOpen) return null

  const items = summary?.items || []
  const filteredItems = items.filter((item) => {
    if (filter === 'error') return item.severity === 'error'
    if (filter === 'warning') return item.severity === 'warning'
    return true
  })

  const errorCount = summary?.counts.error || 0
  const warningCount = summary?.counts.warning || 0
  const totalCount = summary?.total || 0

  const handleItemClick = (item: AttentionItem) => {
    onNavigate(item.action_view, item.action_payload)
    onClose()
  }

  const getTypeLabel = (type: string) => {
    switch (type) {
      case 'scan_failed':
        return t('attention.type.scan_failed')
      case 'scan_stale':
        return t('attention.type.scan_stale')
      case 'task_blocked':
        return t('attention.type.task_blocked')
      default:
        return type
    }
  }

  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <div
        className="drawer-panel"
        onClick={(e) => e.stopPropagation()}
        style={{ maxWidth: 660 }}
      >
        {/* Drawer Header */}
        <div className="drawer-head">
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ fontSize: 20 }}>⚠️</span>
            <div>
              <div
                style={{
                  fontWeight: 600,
                  fontSize: 15,
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                }}
              >
                <span>{t('attention.title')}</span>
                {errorCount > 0 && (
                  <span
                    className="tag red"
                    style={{ fontSize: 11, padding: '1px 8px' }}
                  >
                    {t('attention.errors', { count: errorCount })}
                  </span>
                )}
                {warningCount > 0 && (
                  <span
                    className="tag yellow"
                    style={{ fontSize: 11, padding: '1px 8px' }}
                  >
                    {t('attention.warnings', { count: warningCount })}
                  </span>
                )}
              </div>
              <div className="muted small">{t('attention.subtitle')}</div>
            </div>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <button
              className="btn small"
              disabled={loading}
              onClick={onRefresh}
              title={t('attention.refresh')}
            >
              {loading ? (
                <span className="spinner small" />
              ) : (
                `🔄 ${t('attention.refresh')}`
              )}
            </button>
            <button
              className="btn small"
              onClick={onClose}
              title="Close"
              style={{ padding: '4px 8px' }}
            >
              ✕
            </button>
          </div>
        </div>

        {/* Filter Navigation */}
        <div
          style={{
            display: 'flex',
            gap: 6,
            padding: '10px 18px',
            borderBottom: '1px solid var(--border)',
            background: 'var(--panel)',
          }}
        >
          <button
            className={`btn small ${filter === 'all' ? 'primary' : ''}`}
            onClick={() => setFilter('all')}
          >
            {t('attention.filter.all')} ({totalCount})
          </button>
          <button
            className={`btn small ${filter === 'error' ? 'primary' : ''}`}
            onClick={() => setFilter('error')}
          >
            {t('attention.filter.errors')} ({errorCount})
          </button>
          <button
            className={`btn small ${filter === 'warning' ? 'primary' : ''}`}
            onClick={() => setFilter('warning')}
          >
            {t('attention.filter.warnings')} ({warningCount})
          </button>
        </div>

        {/* Drawer Body */}
        <div className="drawer-body">
          {loading && !summary ? (
            <div
              style={{
                display: 'flex',
                justifyContent: 'center',
                padding: '40px 0',
              }}
            >
              <span className="spinner" />
            </div>
          ) : filteredItems.length === 0 ? (
            <div
              style={{
                padding: '40px 20px',
                textAlign: 'center',
                color: 'var(--muted)',
              }}
            >
              <div style={{ fontSize: 32, marginBottom: 12 }}>✨</div>
              <div style={{ fontSize: 14 }}>{t('attention.empty')}</div>
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              {filteredItems.map((item) => {
                const isError = item.severity === 'error'
                const isHost = item.target_type === 'host'
                return (
                  <div
                    key={item.id}
                    onClick={() => handleItemClick(item)}
                    style={{
                      border: `1px solid ${
                        isError ? 'var(--red, #e53e3e)' : 'var(--yellow, #d69e2e)'
                      }`,
                      borderRadius: 8,
                      padding: '12px 14px',
                      background: isError
                        ? 'rgba(229, 62, 62, 0.04)'
                        : 'rgba(214, 158, 46, 0.04)',
                      cursor: 'pointer',
                      transition: 'background 0.15s ease, border-color 0.15s ease',
                    }}
                    className="hover-card"
                  >
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        marginBottom: 6,
                      }}
                    >
                      <div
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: 8,
                        }}
                      >
                        <span
                          className={`tag ${isError ? 'red' : 'yellow'}`}
                          style={{ fontSize: 11, padding: '1px 6px' }}
                        >
                          {getTypeLabel(item.type)}
                        </span>
                        <span style={{ fontWeight: 600, fontSize: 13 }}>
                          {isHost ? '🖥️' : '📋'} {item.target_name}
                        </span>
                      </div>
                      <span className="muted small">
                        {formatAgo(item.last_updated_at, t)}
                      </span>
                    </div>

                    <div
                      style={{
                        fontSize: 12,
                        color: 'var(--text)',
                        marginBottom: 8,
                        lineHeight: 1.5,
                        wordBreak: 'break-word',
                      }}
                    >
                      {item.message}
                    </div>

                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                      }}
                    >
                      <span
                        className="small"
                        style={{
                          color: isError
                            ? 'var(--red, #e53e3e)'
                            : 'var(--yellow, #d69e2e)',
                          fontWeight: 500,
                        }}
                      >
                        {isHost
                          ? t('attention.action.jumpToHost')
                          : t('attention.action.jumpToTask')}
                        {' →'}
                      </span>
                      <span className="muted small" style={{ fontSize: 11 }}>
                        ID: {item.id}
                      </span>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
