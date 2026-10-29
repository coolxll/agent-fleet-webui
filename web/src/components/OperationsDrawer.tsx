import { useEffect, useState, useCallback, useMemo } from 'react'
import { api, type OperationLog, type OperationStatus } from '../api'
import { useI18n } from '../i18n'

export type OperationsDrawerProps = {
  isOpen?: boolean
  onClose?: () => void
  initialTargetType?: string
  initialTargetId?: string
  showFloatingTrigger?: boolean
}

/** Global event helper to open OperationsDrawer from anywhere in the UI */
export function openOperationsDrawer(detail?: { target_type?: string; target_id?: string }) {
  window.dispatchEvent(new CustomEvent('open-operations-drawer', { detail }))
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

function formatDuration(startIso: string | null, finishIso: string | null): string {
  if (!startIso) return '-'
  const start = new Date(startIso).getTime()
  const end = finishIso ? new Date(finishIso).getTime() : Date.now()
  if (isNaN(start) || isNaN(end)) return '-'
  const ms = Math.max(0, end - start)
  if (ms < 1000) return `${ms}ms`
  const s = (ms / 1000).toFixed(1)
  return `${s}s`
}

export function OperationsTriggerButton({
  onClick,
  className = '',
  style = {},
}: {
  onClick?: () => void
  className?: string
  style?: React.CSSProperties
}) {
  const { t } = useI18n()
  const handleClick = () => {
    if (onClick) onClick()
    else openOperationsDrawer()
  }

  return (
    <button
      type="button"
      onClick={handleClick}
      className={`btn-ghost ${className}`}
      title={t('operations.title')}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        padding: '5px 10px',
        fontSize: 12,
        borderRadius: 6,
        border: '1px solid var(--border-color, #e2e8f0)',
        cursor: 'pointer',
        ...style,
      }}
    >
      <span style={{ fontSize: 14 }}>📋</span>
      <span>{t('operations.triggerBtn')}</span>
    </button>
  )
}

export default function OperationsDrawer({
  isOpen: controlledIsOpen,
  onClose: controlledOnClose,
  initialTargetType,
  initialTargetId,
  showFloatingTrigger = false,
}: OperationsDrawerProps) {
  const { t } = useI18n()
  const [internalOpen, setInternalOpen] = useState(false)
  const isControlled = controlledIsOpen !== undefined
  const isOpen = isControlled ? controlledIsOpen : internalOpen

  const handleClose = () => {
    if (controlledOnClose) controlledOnClose()
    if (!isControlled) setInternalOpen(false)
  }

  // Filter states
  const [statusFilter, setStatusFilter] = useState<'all' | OperationStatus>('all')
  const [targetTypeFilter, setTargetTypeFilter] = useState<string>(initialTargetType || 'all')
  const [searchQuery, setSearchQuery] = useState('')
  const [operations, setOperations] = useState<OperationLog[]>([])
  const [loading, setLoading] = useState(false)
  const [expandedIds, setExpandedIds] = useState<Record<string, boolean>>({})

  // Event listener for opening drawer externally via CustomEvent or hotkey Alt+O
  useEffect(() => {
    const handleOpenEvent = (e: Event) => {
      const customEvent = e as CustomEvent
      if (!isControlled) setInternalOpen(true)
      if (customEvent.detail?.target_type) {
        setTargetTypeFilter(customEvent.detail.target_type)
      }
    }

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.altKey && (e.key === 'o' || e.key === 'O')) {
        e.preventDefault()
        if (isControlled && controlledOnClose && isOpen) {
          controlledOnClose()
        } else if (!isControlled) {
          setInternalOpen((prev) => !prev)
        }
      }
    }

    window.addEventListener('open-operations-drawer', handleOpenEvent)
    window.addEventListener('keydown', handleKeyDown)
    return () => {
      window.removeEventListener('open-operations-drawer', handleOpenEvent)
      window.removeEventListener('keydown', handleKeyDown)
    }
  }, [isControlled, isOpen, controlledOnClose])

  const loadOperations = useCallback(async () => {
    try {
      setLoading(true)
      const params: Parameters<typeof api.listOperations>[0] = { limit: 100 }
      if (statusFilter !== 'all') params.status = statusFilter
      if (targetTypeFilter !== 'all') params.target_type = targetTypeFilter
      if (initialTargetId) params.target_id = initialTargetId

      const res = await api.listOperations(params)
      setOperations(res.operations || [])
    } catch {
      /* ignore fetch errors during polling */
    } finally {
      setLoading(false)
    }
  }, [statusFilter, targetTypeFilter, initialTargetId])

  // Polling every 3s while open
  useEffect(() => {
    if (!isOpen) return
    loadOperations()
    const timer = setInterval(loadOperations, 3000)
    return () => clearInterval(timer)
  }, [isOpen, loadOperations])

  const toggleExpand = (id: string) => {
    setExpandedIds((prev) => ({ ...prev, [id]: !prev[id] }))
  }

  const getStatusBadge = (status: OperationStatus) => {
    switch (status) {
      case 'succeeded':
        return {
          label: t('operations.filter.succeeded'),
          color: '#10b981',
          bg: 'rgba(16, 185, 129, 0.12)',
          border: 'rgba(16, 185, 129, 0.3)',
        }
      case 'running':
        return {
          label: t('operations.filter.running'),
          color: '#3b82f6',
          bg: 'rgba(59, 130, 246, 0.12)',
          border: 'rgba(59, 130, 246, 0.3)',
        }
      case 'failed':
        return {
          label: t('operations.filter.failed'),
          color: '#ef4444',
          bg: 'rgba(239, 68, 68, 0.12)',
          border: 'rgba(239, 68, 68, 0.3)',
        }
      case 'accepted':
        return {
          label: t('operations.filter.accepted'),
          color: '#f59e0b',
          bg: 'rgba(245, 158, 11, 0.12)',
          border: 'rgba(245, 158, 11, 0.3)',
        }
      default:
        return {
          label: status,
          color: '#6b7280',
          bg: 'rgba(107, 114, 128, 0.12)',
          border: 'rgba(107, 114, 128, 0.3)',
        }
    }
  }

  const getOperationTypeLabel = (opType: string) => {
    const key = `operations.opType.${opType}` as any
    const translated = t(key)
    return translated !== key ? translated : opType
  }

  const filteredOperations = useMemo(() => {
    return operations.filter((op) => {
      if (!searchQuery.trim()) return true
      const q = searchQuery.toLowerCase()
      return (
        op.id.toLowerCase().includes(q) ||
        op.operation_type.toLowerCase().includes(q) ||
        op.target_id.toLowerCase().includes(q) ||
        (op.target_name && op.target_name.toLowerCase().includes(q)) ||
        (op.error && op.error.toLowerCase().includes(q))
      )
    })
  }, [operations, searchQuery])

  const counts = useMemo(() => {
    return {
      all: operations.length,
      succeeded: operations.filter((o) => o.status === 'succeeded').length,
      running: operations.filter((o) => o.status === 'running').length,
      failed: operations.filter((o) => o.status === 'failed').length,
      accepted: operations.filter((o) => o.status === 'accepted').length,
    }
  }, [operations])

  return (
    <>
      {showFloatingTrigger && !isOpen && (
        <div style={{ position: 'fixed', bottom: 20, right: 20, zIndex: 990 }}>
          <button
            type="button"
            onClick={() => setInternalOpen(true)}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              padding: '8px 14px',
              backgroundColor: 'var(--bg-panel, #ffffff)',
              color: 'var(--text-primary, #1e293b)',
              border: '1px solid var(--border-color, #cbd5e1)',
              borderRadius: 20,
              boxShadow: '0 4px 12px rgba(0,0,0,0.12)',
              cursor: 'pointer',
              fontWeight: 500,
              fontSize: 13,
            }}
          >
            <span>📜</span>
            <span>{t('operations.triggerBtn')}</span>
            {counts.running > 0 && (
              <span
                style={{
                  backgroundColor: '#3b82f6',
                  color: '#fff',
                  borderRadius: 10,
                  padding: '1px 6px',
                  fontSize: 11,
                }}
              >
                {counts.running}
              </span>
            )}
          </button>
        </div>
      )}

      {isOpen && (
        <div className="drawer-backdrop" onClick={handleClose}>
          <div
            className="drawer-panel"
            onClick={(e) => e.stopPropagation()}
            style={{ maxWidth: 720, width: '100%' }}
          >
            {/* Header */}
            <div className="drawer-head" style={{ padding: '16px 20px', borderBottom: '1px solid var(--border-color, #e2e8f0)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <span style={{ fontSize: 22 }}>📋</span>
                <div>
                  <div style={{ fontWeight: 600, fontSize: 16 }}>{t('operations.title')}</div>
                  <div style={{ fontSize: 12, color: 'var(--text-muted, #64748b)' }}>
                    {t('operations.subtitle')}
                  </div>
                </div>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <button
                  type="button"
                  onClick={loadOperations}
                  disabled={loading}
                  className="btn-ghost"
                  style={{ fontSize: 12, padding: '4px 8px', borderRadius: 4 }}
                >
                  {loading ? '...' : t('operations.refresh')}
                </button>
                <button type="button" className="drawer-close" onClick={handleClose}>
                  ✕
                </button>
              </div>
            </div>

            {/* Filter Tabs & Search */}
            <div style={{ padding: '12px 20px', borderBottom: '1px solid var(--border-color, #e2e8f0)', display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                  {(['all', 'succeeded', 'running', 'failed', 'accepted'] as const).map((st) => {
                    const active = statusFilter === st
                    const count = counts[st]
                    return (
                      <button
                        key={st}
                        type="button"
                        onClick={() => setStatusFilter(st)}
                        style={{
                          padding: '4px 10px',
                          borderRadius: 6,
                          fontSize: 12,
                          border: active
                            ? '1px solid var(--primary, #3b82f6)'
                            : '1px solid var(--border-color, #e2e8f0)',
                          backgroundColor: active
                            ? 'rgba(59, 130, 246, 0.1)'
                            : 'transparent',
                          color: active ? 'var(--primary, #3b82f6)' : 'inherit',
                          cursor: 'pointer',
                          display: 'flex',
                          alignItems: 'center',
                          gap: 6,
                        }}
                      >
                        <span>{t(`operations.filter.${st}` as any)}</span>
                        <span
                          style={{
                            fontSize: 11,
                            opacity: 0.8,
                            backgroundColor: 'rgba(0,0,0,0.06)',
                            padding: '1px 5px',
                            borderRadius: 10,
                          }}
                        >
                          {count}
                        </span>
                      </button>
                    )
                  })}
                </div>

                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <select
                    value={targetTypeFilter}
                    onChange={(e) => setTargetTypeFilter(e.target.value)}
                    style={{
                      fontSize: 12,
                      padding: '4px 8px',
                      borderRadius: 6,
                      border: '1px solid var(--border-color, #e2e8f0)',
                      backgroundColor: 'transparent',
                    }}
                  >
                    <option value="all">All Targets</option>
                    <option value="runtime">{t('operations.targetType.runtime')}</option>
                    <option value="host">{t('operations.targetType.host')}</option>
                    <option value="runner">{t('operations.targetType.runner')}</option>
                    <option value="task">{t('operations.targetType.task')}</option>
                  </select>
                </div>
              </div>

              <div>
                <input
                  type="text"
                  placeholder="Filter by ID, target, operation or error..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  style={{
                    width: '100%',
                    padding: '6px 10px',
                    fontSize: 12,
                    borderRadius: 6,
                    border: '1px solid var(--border-color, #e2e8f0)',
                    backgroundColor: 'var(--bg-input, rgba(0,0,0,0.02))',
                  }}
                />
              </div>
            </div>

            {/* Content List */}
            <div style={{ flex: 1, overflowY: 'auto', padding: '16px 20px' }}>
              {filteredOperations.length === 0 ? (
                <div style={{ textAlign: 'center', padding: '40px 0', color: 'var(--text-muted, #64748b)' }}>
                  <div style={{ fontSize: 32, marginBottom: 8 }}>📂</div>
                  <div>{t('operations.empty')}</div>
                </div>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                  {filteredOperations.map((op) => {
                    const isExpanded = !!expandedIds[op.id]
                    const statusMeta = getStatusBadge(op.status)
                    const hasVerification = !!op.details?.verification

                    return (
                      <div
                        key={op.id}
                        style={{
                          border: '1px solid var(--border-color, #e2e8f0)',
                          borderRadius: 8,
                          backgroundColor: 'var(--bg-card, #ffffff)',
                          overflow: 'hidden',
                          boxShadow: '0 1px 3px rgba(0,0,0,0.04)',
                        }}
                      >
                        {/* Summary Header Row */}
                        <div
                          onClick={() => toggleExpand(op.id)}
                          style={{
                            padding: '12px 14px',
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'space-between',
                            cursor: 'pointer',
                            userSelect: 'none',
                          }}
                        >
                          <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0 }}>
                            {/* Status Pill */}
                            <span
                              style={{
                                fontSize: 11,
                                fontWeight: 600,
                                padding: '2px 8px',
                                borderRadius: 12,
                                color: statusMeta.color,
                                backgroundColor: statusMeta.bg,
                                border: `1px solid ${statusMeta.border}`,
                                flexShrink: 0,
                              }}
                            >
                              {statusMeta.label}
                            </span>

                            {/* Operation Type & Target */}
                            <div style={{ minWidth: 0 }}>
                              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                <span style={{ fontWeight: 600, fontSize: 13 }}>
                                  {getOperationTypeLabel(op.operation_type)}
                                </span>
                                {hasVerification && (
                                  <span
                                    title={t('operations.verified')}
                                    style={{
                                      fontSize: 10,
                                      fontWeight: 600,
                                      color: '#059669',
                                      backgroundColor: 'rgba(5, 150, 105, 0.1)',
                                      padding: '1px 5px',
                                      borderRadius: 4,
                                      border: '1px solid rgba(5, 150, 105, 0.25)',
                                    }}
                                  >
                                    🛡️ {t('operations.verified')}
                                  </span>
                                )}
                              </div>
                              <div
                                style={{
                                  fontSize: 12,
                                  color: 'var(--text-muted, #64748b)',
                                  overflow: 'hidden',
                                  textOverflow: 'ellipsis',
                                  whiteSpace: 'nowrap',
                                }}
                              >
                                <span style={{ opacity: 0.8 }}>[{op.target_type}]</span>{' '}
                                <span style={{ fontWeight: 500 }}>{op.target_name || op.target_id}</span>
                              </div>
                            </div>
                          </div>

                          <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexShrink: 0 }}>
                            <div style={{ textAlign: 'right', fontSize: 11, color: 'var(--text-muted, #64748b)' }}>
                              <div>{formatAgo(op.started_at, t)}</div>
                              <div style={{ opacity: 0.75 }}>
                                {formatDuration(op.started_at, op.finished_at)}
                              </div>
                            </div>
                            <span style={{ fontSize: 12, opacity: 0.6 }}>{isExpanded ? '▲' : '▼'}</span>
                          </div>
                        </div>

                        {/* Error preview banner if failed */}
                        {op.error && !isExpanded && (
                          <div
                            style={{
                              padding: '6px 14px',
                              backgroundColor: 'rgba(239, 68, 68, 0.06)',
                              borderTop: '1px dashed rgba(239, 68, 68, 0.2)',
                              fontSize: 11,
                              color: '#dc2626',
                              overflow: 'hidden',
                              textOverflow: 'ellipsis',
                              whiteSpace: 'nowrap',
                            }}
                          >
                            ⚠️ {op.error}
                          </div>
                        )}

                        {/* Expanded Detail Panel */}
                        {isExpanded && (
                          <div
                            style={{
                              padding: '12px 14px',
                              borderTop: '1px solid var(--border-color, #e2e8f0)',
                              backgroundColor: 'rgba(0,0,0,0.01)',
                              fontSize: 12,
                              display: 'flex',
                              flexDirection: 'column',
                              gap: 10,
                            }}
                          >
                            <div
                              style={{
                                display: 'grid',
                                gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
                                gap: 8,
                                fontSize: 11,
                              }}
                            >
                              <div>
                                <span style={{ color: 'var(--text-muted, #64748b)' }}>ID: </span>
                                <code style={{ fontSize: 11 }}>{op.id}</code>
                              </div>
                              <div>
                                <span style={{ color: 'var(--text-muted, #64748b)' }}>Target ID: </span>
                                <code style={{ fontSize: 11 }}>{op.target_id}</code>
                              </div>
                              {op.requested_by && (
                                <div>
                                  <span style={{ color: 'var(--text-muted, #64748b)' }}>Requested by: </span>
                                  <span>{op.requested_by}</span>
                                </div>
                              )}
                              <div>
                                <span style={{ color: 'var(--text-muted, #64748b)' }}>Started: </span>
                                <span>{op.started_at}</span>
                              </div>
                              {op.finished_at && (
                                <div>
                                  <span style={{ color: 'var(--text-muted, #64748b)' }}>Finished: </span>
                                  <span>{op.finished_at}</span>
                                </div>
                              )}
                            </div>

                            {/* Error Details */}
                            {op.error && (
                              <div>
                                <div style={{ fontWeight: 600, color: '#dc2626', marginBottom: 4 }}>
                                  {t('operations.error')}:
                                </div>
                                <pre
                                  style={{
                                    backgroundColor: 'rgba(239, 68, 68, 0.08)',
                                    color: '#991b1b',
                                    border: '1px solid rgba(239, 68, 68, 0.25)',
                                    borderRadius: 4,
                                    padding: '8px 10px',
                                    fontSize: 11,
                                    overflowX: 'auto',
                                    whiteSpace: 'pre-wrap',
                                    margin: 0,
                                  }}
                                >
                                  {op.error}
                                </pre>
                              </div>
                            )}

                            {/* Verification Evidence */}
                            {hasVerification && (
                              <div>
                                <div
                                  style={{
                                    fontWeight: 600,
                                    color: '#059669',
                                    marginBottom: 4,
                                    display: 'flex',
                                    alignItems: 'center',
                                    gap: 6,
                                  }}
                                >
                                  <span>🛡️</span>
                                  <span>{t('operations.verification')}</span>
                                </div>
                                <pre
                                  style={{
                                    backgroundColor: 'rgba(16, 185, 129, 0.08)',
                                    color: '#065f46',
                                    border: '1px solid rgba(16, 185, 129, 0.25)',
                                    borderRadius: 4,
                                    padding: '8px 10px',
                                    fontSize: 11,
                                    overflowX: 'auto',
                                    margin: 0,
                                  }}
                                >
                                  {JSON.stringify(op.details.verification, null, 2)}
                                </pre>
                              </div>
                            )}

                            {/* Full Details JSON */}
                            {op.details && Object.keys(op.details).length > 0 && (
                              <div>
                                <details>
                                  <summary
                                    style={{
                                      cursor: 'pointer',
                                      color: 'var(--text-muted, #64748b)',
                                      fontSize: 11,
                                      outline: 'none',
                                    }}
                                  >
                                    {t('operations.viewRaw')}
                                  </summary>
                                  <pre
                                    style={{
                                      marginTop: 6,
                                      backgroundColor: 'var(--bg-code, #1e293b)',
                                      color: '#f8fafc',
                                      padding: '8px 10px',
                                      borderRadius: 4,
                                      fontSize: 11,
                                      overflowX: 'auto',
                                      margin: '6px 0 0 0',
                                    }}
                                  >
                                    {JSON.stringify(op.details, null, 2)}
                                  </pre>
                                </details>
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  )
}
