import { useEffect, useMemo, useRef, useState } from 'react'
import {
  api,
  type ImportedQuotaAccount,
  type QuotaProviderInfo
} from '../api'
import AgentIcon, { agentLabel } from '../components/AgentIcon'
import { QuotaCard } from '../components/QuotaPanel'
import QuotaSourceSettings from '../components/QuotaSourceSettings'
import QuotaAuthSettingsModal from '../components/QuotaAuthSettingsModal'
import { useI18n } from '../i18n'

function minFraction(account: ImportedQuotaAccount): number | null {
  const value = account.quota_summary?.min_remaining_fraction
  return typeof value === 'number' ? value : null
}

export default function QuotasView({ refreshSignal }: { refreshSignal?: number }) {
  const { t, language } = useI18n()
  const [accounts, setAccounts] = useState<ImportedQuotaAccount[] | null>(null)
  const [providers, setProviders] = useState<QuotaProviderInfo[]>([])
  const [error, setError] = useState<string | null>(null)
  const [showAdd, setShowAdd] = useState(false)
  const [authModalOpen, setAuthModalOpen] = useState(false)
  const [provider, setProvider] = useState('codex')
  const [label, setLabel] = useState('')
  const [credential, setCredential] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [addError, setAddError] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState<Set<string>>(new Set())
  const [refreshProgress, setRefreshProgress] = useState<{ done: number; total: number } | null>(null)
  const refreshBusy = useRef(false)
  const [agentFilter, setAgentFilter] = useState<string>('all')
  const [layoutMode, setLayoutMode] = useState<'flat' | 'grouped'>('flat')
  const fileInput = useRef<HTMLInputElement | null>(null)

  const load = () => {
    api
      .quotaAccounts()
      .then((res) => {
        setAccounts(res.accounts)
        setError(null)
      })
      .catch((e: any) => setError(String(e.message || e)))
  }

  useEffect(() => {
    load()
    api
      .quotaProviders()
      .then((res) => {
        setProviders(res.providers)
        if (res.providers.length > 0) setProvider(res.providers[0].id)
      })
      .catch(() => undefined)
  }, [refreshSignal])

  // Read persisted snapshots only; opening this page never queries vendors.
  useEffect(() => {
    const timer = setInterval(load, 15000)
    return () => clearInterval(timer)
  }, [])

  const providerById = useMemo(
    () => Object.fromEntries(providers.map((p) => [p.id, p])),
    [providers]
  )
  const currentProvider = providerById[provider]

  const allAgents = useMemo(() => {
    const counts = new Map<string, number>()
    for (const a of accounts || []) {
      const agent = a.agent || providerById[a.provider]?.agent || a.provider
      counts.set(agent, (counts.get(agent) || 0) + 1)
    }
    return counts
  }, [accounts, providerById])

  const filteredAccounts = useMemo(() => {
    if (!accounts) return []
    if (agentFilter === 'all') return accounts
    return accounts.filter((a) => {
      const agent = a.agent || providerById[a.provider]?.agent || a.provider
      return agent === agentFilter
    })
  }, [accounts, agentFilter, providerById])

  const filteredGroups = useMemo(() => {
    const byAgent = new Map<string, ImportedQuotaAccount[]>()
    for (const account of filteredAccounts) {
      const agent = account.agent || providerById[account.provider]?.agent || account.provider
      byAgent.set(agent, [...(byAgent.get(agent) || []), account])
    }
    return [...byAgent.entries()].sort(([a], [b]) => a.localeCompare(b))
  }, [filteredAccounts, providerById])

  const totals = useMemo(() => {
    const list = accounts || []
    const fractions = list.map(minFraction).filter((v): v is number => v !== null)
    return {
      providers: new Set(list.map((a) => a.provider)).size,
      accounts: list.length,
      ok: list.filter((a) => a.status === 'ok').length,
      lowest: fractions.length ? Math.min(...fractions) : null
    }
  }, [accounts])

  const submit = async () => {
    let cred = credential.trim()
    if (!cred) {
      if (provider === 'qodercn' || provider === 'workbuddy' || provider === 'codebuddy') {
        cred = 'auto'
      } else {
        setAddError(t('quotas.credentialRequired'))
        return
      }
    }
    setSubmitting(true)
    setAddError(null)
    try {
      await api.addQuotaAccount({
        provider,
        credential: cred,
        label: label.trim() || undefined
      })
      setCredential('')
      setLabel('')
      setShowAdd(false)
      load()
    } catch (e: any) {
      setAddError(String(e.message || e))
    } finally {
      setSubmitting(false)
    }
  }

  const refreshAccounts = async (targets: ImportedQuotaAccount[], batch = false) => {
    if (refreshBusy.current || targets.length === 0) return
    refreshBusy.current = true
    setError(null)
    if (batch) setRefreshProgress({ done: 0, total: targets.length })
    const failures: string[] = []
    const queue = [...targets]
    const worker = async () => {
      for (let account = queue.shift(); account; account = queue.shift()) {
        setRefreshing((prev) => new Set([...prev, account.id]))
        try {
          const updated = await api.refreshQuotaAccount(account.id)
          setAccounts((prev) => (prev || []).map((a) => a.id === updated.id ? updated : a))
        } catch (e: any) {
          failures.push(`${account.email || account.label || account.id}: ${String(e.message || e)}`)
        } finally {
          const id = account.id
          setRefreshing((prev) => {
            const next = new Set(prev)
            next.delete(id)
            return next
          })
          if (batch) setRefreshProgress((prev) => prev && ({ ...prev, done: prev.done + 1 }))
        }
      }
    }
    try {
      await Promise.all(Array.from({ length: Math.min(3, targets.length) }, worker))
      if (failures.length) {
        // Read persisted error states without losing the batch failure summary.
        try {
          const result = await api.quotaAccounts()
          setAccounts(result.accounts)
        } catch (e: any) {
          failures.push(String(e.message || e))
        }
      }
      setError(failures.length ? failures.join(' · ') : null)
    } finally {
      refreshBusy.current = false
      setRefreshProgress(null)
    }
  }

  const remove = async (account: ImportedQuotaAccount) => {
    const name = account.label || account.name || account.email || account.id
    if (!window.confirm(t('quotas.deleteConfirm', { name }))) return
    try {
      await api.deleteQuotaAccount(account.id)
      load()
    } catch (e: any) {
      setError(String(e.message || e))
    }
  }

  const pickFile = async (file: File | undefined) => {
    if (!file) return
    try {
      setCredential(await file.text())
      setAddError(null)
    } catch (e: any) {
      setAddError(String(e.message || e))
    }
  }


  return (
    <div>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 12,
          marginBottom: 16
        }}
      >
        <div>
          <div style={{ fontWeight: 600, fontSize: 16 }}>{t('quotas.title')}</div>
          <div className="muted small">{t('quotas.subtitle')}</div>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button
            className="btn small"
            onClick={() => refreshAccounts(accounts || [], true)}
            disabled={!accounts?.length || refreshing.size > 0 || refreshProgress !== null}
          >
            {refreshProgress
              ? `${t('quotas.refreshing')} ${refreshProgress.done}/${refreshProgress.total}`
              : `↻ ${t('quotas.refreshAll')}`}
          </button>
          <button
            className="btn small"
            onClick={() => setAuthModalOpen(true)}
            title={language === 'zh' ? '配置 Antigravity 等平台 Client Secret 认证信息' : 'Configure OAuth Client Secrets and credentials'}
          >
            ⚙️ {language === 'zh' ? '认证配置' : 'Auth Settings'}
          </button>
          <button
            className="btn primary small"
            onClick={() => {
              setShowAdd((v) => !v)
              setAddError(null)
            }}
          >
            {showAdd ? t('common.close') : `+ ${t('quotas.importAccount')}`}
          </button>
        </div>
      </div>

      <QuotaSourceSettings onChanged={load} />

      {showAdd ? (
        <div className="panel">
          <h2>{t('quotas.importTitle')}</h2>
          <div className="body">
            <div className="filters">
              <label className="small">
                {t('quotas.provider')}{' '}
                <select value={provider} onChange={(e) => setProvider(e.target.value)}>
                  {providers.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="small">
                {t('quotas.label')}{' '}
                <input
                  value={label}
                  onChange={(e) => setLabel(e.target.value)}
                  placeholder={t('quotas.labelPlaceholder')}
                />
              </label>
              <button className="btn small" onClick={() => fileInput.current?.click()}>
                {t('quotas.chooseFile')}
              </button>
              <input
                ref={fileInput}
                type="file"
                accept=".json,application/json,text/plain"
                style={{ display: 'none' }}
                onChange={(e) => pickFile(e.target.files?.[0])}
              />
            </div>
            <div className="muted small" style={{ margin: '8px 0' }}>
              {currentProvider
                ? `${currentProvider.credential_label} — ${currentProvider.credential_hint}`
                : t('quotas.credentialHint')}
            </div>
            <textarea
              value={credential}
              onChange={(e) => setCredential(e.target.value)}
              rows={7}
              spellCheck={false}
              placeholder={t('quotas.credentialPlaceholder')}
              style={{
                width: '100%',
                fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
                fontSize: 12,
                background: 'var(--bg)',
                color: 'var(--text)',
                border: '1px solid var(--border)',
                borderRadius: 6,
                padding: 10
              }}
            />
            {addError ? <div className="err" style={{ marginTop: 10 }}>{addError}</div> : null}
            <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
              <button className="btn primary small" onClick={submit} disabled={submitting}>
                {submitting ? t('quotas.verifying') : t('quotas.verifyImport')}
              </button>
              <button className="btn small" onClick={() => setShowAdd(false)} disabled={submitting}>
                {t('common.cancel')}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      <div className="cards">
        <div className="card">
          <div className="label">{t('quotas.providers')}</div>
          <div className="value">{totals.providers}</div>
          <div className="hint">{t('quotas.providersHint')}</div>
        </div>
        <div className="card">
          <div className="label">{t('quotas.accounts')}</div>
          <div className="value">{totals.accounts}</div>
          <div className="hint">{t('quotas.healthy', { count: totals.ok })}</div>
        </div>
        <div className="card">
          <div className="label">{t('quotas.healthyRate')}</div>
          <div
            className="value"
            style={{
              color:
                totals.accounts === 0
                  ? undefined
                  : totals.ok === totals.accounts
                    ? 'var(--ok)'
                    : totals.ok > 0
                      ? 'var(--warn)'
                      : 'var(--bad)'
            }}
          >
            {totals.accounts === 0 ? '—' : `${((totals.ok / totals.accounts) * 100).toFixed(0)}%`}
          </div>
          <div className="hint">
            {t('quotas.healthyRateHint', { ok: totals.ok, total: totals.accounts })}
          </div>
        </div>
        <div className="card">
          <div className="label">{t('quotas.online')}</div>
          <div className="value" style={{ color: 'var(--ok)', fontSize: 20 }}>
            {t('quotas.onlineValue')}
          </div>
          <div className="hint">{t('quotas.onlineHint')}</div>
        </div>
      </div>

      <div className="quotas-controls-row">
        <div className="quotas-agent-tabs">
          <button
            className={`quotas-agent-tab ${agentFilter === 'all' ? 'active' : ''}`}
            onClick={() => setAgentFilter('all')}
          >
            {language === 'zh' ? '全部' : 'All'} ({accounts?.length || 0})
          </button>
          {[...allAgents.entries()].map(([agent, count]) => (
            <button
              key={agent}
              className={`quotas-agent-tab ${agentFilter === agent ? 'active' : ''}`}
              onClick={() => setAgentFilter(agent)}
            >
              <AgentIcon agent={agent} size={15} />
              <span>{agentLabel(agent)}</span>
              <span className="muted" style={{ fontSize: 11 }}>({count})</span>
            </button>
          ))}
        </div>

        <button
          className={`quotas-mode-btn ${layoutMode === 'flat' ? 'active' : ''}`}
          onClick={() => setLayoutMode(layoutMode === 'flat' ? 'grouped' : 'flat')}
          title={language === 'zh' ? '切换平铺/分组布局' : 'Toggle flat/grouped view'}
        >
          {layoutMode === 'flat'
            ? language === 'zh' ? '⊞ 连续平铺' : '⊞ Continuous'
            : language === 'zh' ? '☰ 按 Agent 分隔' : '☰ By Agent'}
        </button>
      </div>

      {error ? <div className="err" style={{ marginBottom: 10 }}>{error}</div> : null}

      {accounts === null ? (
        <div className="muted">{t('common.loading')}</div>
      ) : accounts.length === 0 ? (
        <div className="panel">
          <h2>{t('quotas.emptyTitle')}</h2>
          <div className="body">
            <div className="muted small" style={{ lineHeight: 1.7 }}>
              {t('quotas.emptyBody')}
            </div>
          </div>
        </div>
      ) : filteredAccounts.length === 0 ? (
        <div className="muted small" style={{ padding: '20px 0', textAlign: 'center' }}>
          {language === 'zh' ? '当前筛选下没有账号' : 'No accounts match current filter'}
        </div>
      ) : layoutMode === 'flat' ? (
        <div className="quota-cards-grid">
          {filteredAccounts.map((account) => {
            const providerInfo = providerById[account.provider]
            const agent = account.agent || providerInfo?.agent || account.provider
            return (
              <QuotaCard
                key={account.id}
                account={account}
                agent={agent}
                refreshing={refreshing.has(account.id)}
                refreshDisabled={refreshProgress !== null || refreshing.size > 0}
                onRefresh={() => refreshAccounts([account])}
                onRemove={refreshProgress !== null || refreshing.has(account.id) ? undefined : () => remove(account)}
              />
            )
          })}
        </div>
      ) : (
        filteredGroups.map(([groupAgent, groupAccounts]) => (
          <section className="quota-agent-group" key={groupAgent} aria-label={agentLabel(groupAgent)}>
            <div className="quota-agent-heading">
              <AgentIcon agent={groupAgent} size={16} />
              <h2>{agentLabel(groupAgent)}</h2>
              <span className="tag" style={{ fontSize: 10, padding: '1px 5px' }}>
                {t('quotas.accountsCount', { count: groupAccounts.length })}
              </span>
            </div>
            <div className="quota-cards-grid">
              {groupAccounts.map((account) => {
                const providerInfo = providerById[account.provider]
                const agent = account.agent || providerInfo?.agent || account.provider
                return (
                  <QuotaCard
                    key={account.id}
                    account={account}
                    agent={agent}
                    refreshing={refreshing.has(account.id)}
                    refreshDisabled={refreshProgress !== null || refreshing.size > 0}
                    onRefresh={() => refreshAccounts([account])}
                    onRemove={refreshProgress !== null || refreshing.has(account.id) ? undefined : () => remove(account)}
                  />
                )
              })}
            </div>
          </section>
        ))
      )}

      {accounts && accounts.length > 0 ? (
        <div className="muted small" style={{ lineHeight: 1.6 }}>
          {language === 'zh'
            ? 'magpie 关联账号只保存来源标识，每次查询读取当前认证；手动导入账号的凭据由后台保存。'
            : 'Magpie links store only an account reference and read current authentication for each query. Manually imported credentials are stored by the backend.'}
        </div>
      ) : null}

      <QuotaAuthSettingsModal
        isOpen={authModalOpen}
        onClose={() => setAuthModalOpen(false)}
        onSaved={load}
      />
    </div>
  )
}
