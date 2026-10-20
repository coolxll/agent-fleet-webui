import { useEffect, useState, type ReactNode } from 'react'
import type { ImportedQuotaAccount, QuotaSnapshot } from '../api'
import AgentIcon from './AgentIcon'
import { useI18n } from '../i18n'

export type { QuotaSnapshot } from '../api'

export type QuotaSource = {
  label?: string
  quota: QuotaSnapshot | null | undefined
}

function parseTime(value?: string | null): number | null {
  if (!value) return null
  const ms = Date.parse(value)
  return Number.isNaN(ms) ? null : ms
}

function tone(fraction: number): 'ok' | 'warn' | 'bad' {
  if (fraction > 0.3) return 'ok'
  if (fraction > 0.1) return 'warn'
  return 'bad'
}

function toneColor(level: 'ok' | 'warn' | 'bad'): string {
  return level === 'ok' ? 'var(--ok)' : level === 'warn' ? 'var(--warn)' : 'var(--bad)'
}

function pct(fraction: number): string {
  return (Math.max(0, Math.min(1, fraction)) * 100).toFixed(1)
}

function useDuration() {
  const { language } = useI18n()
  const zh = language === 'zh'
  return (ms: number): string => {
    const totalMinutes = Math.max(0, Math.floor(ms / 60000))
    if (totalMinutes < 1) return zh ? '刚刚' : '<1m'
    const days = Math.floor(totalMinutes / 1440)
    const hours = Math.floor((totalMinutes % 1440) / 60)
    const minutes = totalMinutes % 60
    if (zh) {
      if (days > 0) return `${days}天${hours}小时`
      if (hours > 0) return `${hours}小时${minutes}分`
      return `${minutes}分钟`
    }
    if (days > 0) return `${days}d ${hours}h`
    if (hours > 0) return `${hours}h ${minutes}m`
    return `${minutes}m`
  }
}

export function QuotaCard({
  account,
  agent,
  refreshing,
  onRefresh,
  onRemove
}: {
  account: ImportedQuotaAccount
  agent: string
  refreshing?: boolean
  onRefresh?: () => void
  onRemove?: () => void
}) {
  const { t, language } = useI18n()
  const duration = useDuration()
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 30000)
    return () => clearInterval(timer)
  }, [])

  const windowLabel = (window: string) =>
    window === 'weekly' ? t('quota.weekly') : window === '5h' ? t('quota.fiveHour') : window

  const displayName = account.label || account.name || account.email || account.id
  const showEmail = Boolean(account.email && displayName !== account.email)
  const plan = account.plan || account.quota?.accounts?.[0]?.tier?.name

  const quota = account.quota
  const firstAccount = quota?.accounts?.[0]
  const groups = firstAccount?.groups || []
  const observedAt = parseTime(quota?.observed_at || account.last_refreshed_at)
  const age = observedAt === null ? null : now - observedAt

  return (
    <div className="quota-card">
      <div className="quota-card-header">
        <div className="quota-card-identity">
          <AgentIcon agent={agent} size={18} />
          <div className="quota-card-titles">
            <div className="quota-card-title-row">
              <span className="quota-card-name" title={displayName}>
                {displayName}
              </span>
              {plan ? <span className="tag">{plan}</span> : null}
            </div>
            {showEmail && account.email ? (
              <span className="quota-card-email mono" title={account.email}>
                {account.email}
              </span>
            ) : null}
          </div>
        </div>

        <div className="quota-card-actions">
          {account.status === 'error' ? (
            <span className="tag bad">{t('quotas.statusError')}</span>
          ) : (
            <span className="tag ok">{t('quotas.statusOk')}</span>
          )}
          {onRefresh ? (
            <button
              className="btn small"
              onClick={onRefresh}
              disabled={refreshing}
              title={t('quotas.refresh')}
              style={{ padding: '2px 8px' }}
            >
              {refreshing ? t('quotas.refreshing') : `↻ ${t('quotas.refresh')}`}
            </button>
          ) : null}
          {onRemove ? (
            <button
              className="btn small"
              onClick={onRemove}
              title={t('quotas.remove')}
              style={{ padding: '2px 8px' }}
            >
              {t('quotas.remove')}
            </button>
          ) : null}
        </div>
      </div>

      <div className="quota-card-body">
        {account.status === 'error' || !quota ? (
          <div className="err small" style={{ margin: 'auto 0' }}>
            {account.last_error || t('quotas.noQuotaYet')}
          </div>
        ) : groups.length === 0 ? (
          <div className="muted small" style={{ margin: 'auto 0' }}>
            {t('quota.noQuota')}
          </div>
        ) : (
          groups.map((group) => (
            <div className="quota-group" key={group.id}>
              {groups.length > 1 && (
                <div className="quota-group-name">{group.name}</div>
              )}
              {group.buckets.map((bucket) => {
                const level = tone(bucket.remaining_fraction)
                const resetAt = parseTime(bucket.reset_at)
                const resetText =
                  resetAt === null
                    ? null
                    : resetAt <= now
                      ? t('quota.refreshDue')
                      : t('quota.resetsIn', { time: duration(resetAt - now) })
                return (
                  <div className="quota-bucket" key={bucket.id || bucket.window + bucket.name}>
                    <div className="quota-bucket-header">
                      <span className="quota-bucket-window">{windowLabel(bucket.window)}</span>
                      {resetText && (
                        <span className="quota-bucket-reset" title={bucket.reset_at || undefined}>
                          {resetText}
                          {resetAt !== null && (
                            <span className="quota-reset-at">
                              {' · '}
                              {new Date(resetAt).toLocaleTimeString(
                                language === 'zh' ? 'zh-CN' : 'en-US',
                                { hour: '2-digit', minute: '2-digit' }
                              )}
                            </span>
                          )}
                        </span>
                      )}
                    </div>
                    <div className="quota-bucket-bar">
                      <div className="quota-track">
                        <div
                          className="quota-fill"
                          style={{
                            width: pct(bucket.remaining_fraction) + '%',
                            background: toneColor(level)
                          }}
                        />
                      </div>
                      <span className="quota-pct" style={{ color: toneColor(level) }}>
                        {pct(bucket.remaining_fraction)}%
                      </span>
                    </div>
                  </div>
                )
              })}
            </div>
          ))
        )}
      </div>

      <div className="quota-card-footer">
        <span className="quota-provider-tag muted small">
          {account.provider_name || account.provider}
        </span>
        {age !== null ? (
          <span className="quota-sync-time muted small">
            {t('quota.syncedAgo', { time: duration(age) })}
          </span>
        ) : null}
      </div>
    </div>
  )
}

export default function QuotaPanel({
  sources,
  title,
  actions
}: {
  sources: QuotaSource[]
  title?: ReactNode
  actions?: ReactNode
}) {
  const { t, language } = useI18n()
  const duration = useDuration()
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 30000)
    return () => clearInterval(timer)
  }, [])

  const usable = (sources || []).filter(
    (s) => s.quota && Array.isArray(s.quota.accounts) && s.quota.accounts.length > 0
  )
  if (usable.length === 0) return null

  const windowLabel = (window: string) =>
    window === 'weekly' ? t('quota.weekly') : window === '5h' ? t('quota.fiveHour') : window

  return (
    <div className="panel quota-panel">
      <h2>
        <span>{title || t('quota.title')}</span>
        {!title && (
          <span className="tag">
            {t('quota.accountsCount', {
              count: usable.reduce((sum, s) => sum + (s.quota?.accounts.length || 0), 0)
            })}
          </span>
        )}
        {actions ? <div style={{ marginLeft: 'auto' }}>{actions}</div> : null}
      </h2>
      <div className="body">
        {usable.map((source, index) => {
          const quota = source.quota as QuotaSnapshot
          const observedAt = parseTime(quota.observed_at)
          const age = observedAt === null ? null : now - observedAt
          return (
            <div className="quota-source" key={(source.label || 'quota') + '-' + index}>
              <div className="quota-source-head">
                <span className="bold small">{source.label || t('quota.local')}</span>
                <span className="tag">{quota.source}</span>
                {age !== null ? (
                  <span className={'tag ' + (age > 3600_000 ? 'warn' : 'unknown')}>
                    {t('quota.syncedAgo', { time: duration(age) })}
                  </span>
                ) : null}
                {quota.path ? <span className="mono small muted">{quota.path}</span> : null}
              </div>

              {quota.accounts.map((account) => {
                const minFraction =
                  typeof account.min_remaining_fraction === 'number'
                    ? account.min_remaining_fraction
                    : null
                const minTone = minFraction === null ? null : tone(minFraction)
                return (
                  <div className="quota-account" key={account.email}>
                    <div className="quota-account-head">
                      <span className="bold">{account.name || account.email}</span>
                      <span className="mono small muted">{account.email}</span>
                      {(account.aliases || []).map((alias) => (
                        <span className="ts-tag" key={alias}>{alias}</span>
                      ))}
                      {account.tier?.name ? <span className="tag">{account.tier.name}</span> : null}
                      {minFraction !== null && minTone ? (
                        <span className={'tag ' + minTone}>
                          {t('quota.lowestPool', { pct: pct(minFraction) })}
                        </span>
                      ) : null}
                    </div>

                    {account.groups.length === 0 ? (
                      <div className="muted small quota-empty">{t('quota.noQuota')}</div>
                    ) : (
                      account.groups.map((group) => (
                        <div className="quota-group" key={group.id}>
                          <div className="quota-group-name">{group.name}</div>
                          {group.buckets.map((bucket) => {
                            const level = tone(bucket.remaining_fraction)
                            const resetAt = parseTime(bucket.reset_at)
                            const resetText =
                              resetAt === null
                                ? t('common.unknown')
                                : resetAt <= now
                                  ? t('quota.refreshDue')
                                  : t('quota.resetsIn', { time: duration(resetAt - now) })
                            return (
                              <div className="quota-bucket" key={bucket.id || bucket.window + bucket.name}>
                                <div className="quota-bucket-header">
                                  <span className="quota-bucket-window">{windowLabel(bucket.window)}</span>
                                  <span className="quota-bucket-reset small muted" title={bucket.reset_at || undefined}>
                                    {resetText}
                                    {resetAt !== null && (
                                      <span className="quota-reset-at">
                                        {' · '}
                                        {new Date(resetAt).toLocaleTimeString(
                                          language === 'zh' ? 'zh-CN' : 'en-US',
                                          { hour: '2-digit', minute: '2-digit' }
                                        )}
                                      </span>
                                    )}
                                  </span>
                                </div>
                                <div className="quota-bucket-bar">
                                  <div className="quota-track">
                                    <div
                                      className="quota-fill"
                                      style={{
                                        width: pct(bucket.remaining_fraction) + '%',
                                        background: toneColor(level)
                                      }}
                                    />
                                  </div>
                                  <span className="quota-pct" style={{ color: toneColor(level) }}>
                                    {pct(bucket.remaining_fraction)}%
                                  </span>
                                </div>
                              </div>
                            )
                          })}
                        </div>
                      ))
                    )}
                  </div>
                )
              })}
            </div>
          )
        })}
      </div>
    </div>
  )
}
