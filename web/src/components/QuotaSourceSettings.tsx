import { useEffect, useState } from 'react'
import { api, type MagpieQuotaSource, type QuotaCollectionSettings } from '../api'
import { useI18n } from '../i18n'

export default function QuotaSourceSettings({ onChanged }: { onChanged: () => void }) {
  const { language } = useI18n()
  const zh = language === 'zh'
  const [settings, setSettings] = useState<QuotaCollectionSettings | null>(null)
  const [source, setSource] = useState<MagpieQuotaSource | null>(null)
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.quotaCollection().then(setSettings).catch((e) => setError(String(e.message || e)))
  }, [])

  const discover = async () => {
    setBusy(true)
    setError(null)
    try {
      setSource(await api.magpieQuotaAccounts())
      setOpen(true)
    } catch (e: any) {
      setError(String(e.message || e))
    } finally { setBusy(false) }
  }

  const save = async (next: QuotaCollectionSettings) => {
    setBusy(true)
    setError(null)
    try { setSettings(await api.setQuotaCollection(next)) }
    catch (e: any) { setError(String(e.message || e)) }
    finally { setBusy(false) }
  }

  const link = async (id: string) => {
    setBusy(true)
    setError(null)
    try {
      await api.linkMagpieQuotaAccount(id)
      onChanged()
      setSource(await api.magpieQuotaAccounts())
    } catch (e: any) {
      setError(String(e.message || e))
    } finally { setBusy(false) }
  }

  return (
    <div className="panel" style={{ marginBottom: 16 }}>
      <div className="body">
        <div style={{ display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap' }}>
          <strong>{zh ? 'magpie 配额采集' : 'Magpie quota collection'}</strong>
          <button className="btn small" disabled={busy} onClick={() => open ? setOpen(false) : discover()}>
            {open ? (zh ? '收起' : 'Close') : (zh ? '关联 magpie 账号' : 'Link magpie accounts')}
          </button>
          {settings && <>
            <label className="small" style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
              <input type="checkbox" checked={settings.enabled} disabled={busy}
                onChange={(e) => save({ ...settings, enabled: e.target.checked })} />
              {zh ? '后台定时查询' : 'Query in background'}
            </label>
            <label className="small">
              {zh ? '间隔 ' : 'Every '}
              <select value={settings.interval_seconds} disabled={busy}
                onChange={(e) => save({ ...settings, interval_seconds: Number(e.target.value) })}>
                {[...new Set([300, 600, 1800, 3600, settings.interval_seconds])].sort((a, b) => a - b).map((n) => (
                  <option key={n} value={n}>{n / 60} {zh ? '分钟' : 'min'}</option>
                ))}
              </select>
            </label>
          </>}
        </div>
        <div className="muted small" style={{ marginTop: 8, lineHeight: 1.6 }}>
          {zh
            ? '仅自动采集已关联的 Codex 账号。使用 magpie 当前登录查询配额，不续期、不修改登录；失效时请在 magpie 更新认证。'
            : 'Only linked Codex accounts are collected automatically. Uses the current magpie login to query quota; never renews or modifies authentication. Update expired logins in magpie.'}
        </div>
        {error && <div className="err small" style={{ marginTop: 8 }}>{error}</div>}
        {open && source && <div style={{ marginTop: 12 }}>
          {!source.available ? <div className="err small">{source.error}</div>
            : source.accounts.length === 0 ? <div className="muted small">{zh ? 'magpie 中没有可关联的 Codex 账号。' : 'No Codex accounts found in magpie.'}</div>
            : source.accounts.map((a) => (
              <div key={a.id} style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap', padding: '8px 0' }}>
                <span className="small">{a.label}</span>
                {a.linked_account_id
                  ? <span className="tag">{zh ? '已关联' : 'Linked'}</span>
                  : <button className="btn small" disabled={busy} onClick={() => link(a.id)}>{zh ? '关联并查询' : 'Link & query'}</button>}
              </div>
            ))}
          <button className="btn small" style={{ marginTop: 8 }} disabled={busy} onClick={discover}>
            {zh ? '重新读取账号列表' : 'Reload account list'}
          </button>
        </div>}
      </div>
    </div>
  )
}
