import { useEffect, useState } from 'react'
import {
  getAntigravityQuotaSettings,
  updateAntigravityQuotaSettings,
  type AntigravityClientStatus
} from '../api'
import { useI18n } from '../i18n'

type Props = {
  isOpen: boolean
  onClose: () => void
  onSaved?: () => void
}

export default function QuotaAuthSettingsModal({ isOpen, onClose, onSaved }: Props) {
  const { language } = useI18n()
  const zh = language === 'zh'

  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [clients, setClients] = useState<AntigravityClientStatus[]>([])
  const [secretInputs, setSecretInputs] = useState<Record<string, string>>({})
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const [successMsg, setSuccessMsg] = useState<string | null>(null)
  const [showPlain, setShowPlain] = useState<Record<string, boolean>>({})

  const loadSettings = () => {
    setLoading(true)
    setErrorMsg(null)
    getAntigravityQuotaSettings()
      .then((res) => {
        setClients(res.clients)
        setSecretInputs({})
      })
      .catch((err) => {
        setErrorMsg(String(err.message || err))
      })
      .finally(() => {
        setLoading(false)
      })
  }

  useEffect(() => {
    if (isOpen) {
      setErrorMsg(null)
      setSuccessMsg(null)
      loadSettings()
    }
  }, [isOpen])

  if (!isOpen) return null

  const handleSave = async () => {
    setSaving(true)
    setErrorMsg(null)
    setSuccessMsg(null)

    // Only send non-empty modified inputs
    const payload: Record<string, string> = {}
    for (const [cid, val] of Object.entries(secretInputs)) {
      if (val.trim() !== '') {
        payload[cid] = val.trim()
      }
    }

    if (Object.keys(payload).length === 0) {
      setSuccessMsg(zh ? '未做修改' : 'No changes made')
      setSaving(false)
      return
    }

    try {
      const res = await updateAntigravityQuotaSettings(payload)
      setClients(res.clients)
      setSecretInputs({})
      setSuccessMsg(zh ? 'Antigravity 客户端密钥已保存成功！' : 'Client secrets saved successfully!')
      if (onSaved) onSaved()
      setTimeout(() => setSuccessMsg(null), 3000)
    } catch (err: any) {
      setErrorMsg(String(err.message || err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 1000,
        backgroundColor: 'rgba(0, 0, 0, 0.75)',
        backdropFilter: 'blur(3px)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 20
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div
        style={{
          width: '100%',
          maxWidth: 720,
          maxHeight: '90vh',
          backgroundColor: '#161b22',
          border: '1px solid #30363d',
          borderRadius: 10,
          display: 'flex',
          flexDirection: 'column',
          boxShadow: '0 20px 40px rgba(0,0,0,0.6)',
          overflow: 'hidden'
        }}
      >
        {/* Header */}
        <div
          style={{
            padding: '14px 20px',
            borderBottom: '1px solid #30363d',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            background: 'rgba(255,255,255,0.02)'
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ fontSize: 18 }}>🔐</span>
            <div>
              <div style={{ fontWeight: 600, fontSize: 15, color: '#f0f6fc' }}>
                {zh ? '额度查询认证与凭据配置' : 'Quota Provider Authentication Settings'}
              </div>
              <div style={{ fontSize: 12, color: '#8b949e', marginTop: 2 }}>
                {zh ? '集中管理各厂商配额接口所需的凭据与 OAuth 密钥' : 'Manage vendor credentials and OAuth secrets'}
              </div>
            </div>
          </div>
          <button
            type="button"
            className="btn small"
            onClick={onClose}
            style={{ padding: '4px 10px', fontSize: 14 }}
          >
            ✕
          </button>
        </div>

        {/* Content Body */}
        <div
          style={{
            padding: 20,
            overflowY: 'auto',
            display: 'flex',
            flexDirection: 'column',
            gap: 16,
            flex: 1
          }}
        >
          {/* Antigravity Section */}
          <div
            style={{
              padding: 16,
              border: '1px solid #30363d',
              borderRadius: 8,
              backgroundColor: 'rgba(255,255,255,0.01)'
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ fontWeight: 600, fontSize: 14, color: '#f0f6fc' }}>
                  Antigravity (AGY) OAuth 认证
                </span>
                <span className="tag ok" style={{ fontSize: 10 }}>Google OAuth</span>
              </div>
              <span className="muted small" style={{ fontSize: 11 }}>
                {zh ? '持久化在 data/antigravity_clients.json' : 'Stored in data/antigravity_clients.json'}
              </span>
            </div>

            <div style={{ fontSize: 12, color: '#8b949e', marginBottom: 14, lineHeight: 1.5 }}>
              {zh
                ? '导入 Antigravity 账号（refresh_token）刷新配额时，Google OAuth 接口要求提供对应的 Client Secret。在此配置后系统将自动保存并刷新。'
                : 'Antigravity OAuth refresh requires matching Client Secrets. Configure them here to enable automatic quota refreshes.'}
            </div>

            {loading ? (
              <div className="muted small">{zh ? '正在读取配置...' : 'Loading...'}</div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                {clients.map((c) => {
                  const inputVal = secretInputs[c.client_id] ?? ''
                  const isVisible = showPlain[c.client_id] ?? false
                  return (
                    <div
                      key={c.client_id}
                      style={{
                        padding: 12,
                        backgroundColor: '#0d1117',
                        border: '1px solid #21262d',
                        borderRadius: 6
                      }}
                    >
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                          <span className="mono bold small" style={{ color: '#c9d1d9', fontSize: 12 }}>
                            {c.client_id}
                          </span>
                          {c.is_default && (
                            <span className="tag" style={{ fontSize: 9, padding: '0 4px', backgroundColor: '#1f6feb', color: '#fff' }}>
                              {zh ? '默认客户端' : 'Default'}
                            </span>
                          )}
                        </div>
                        <span
                          className={`tag ${c.configured ? 'ok' : 'warn'}`}
                          style={{ fontSize: 10, padding: '1px 6px' }}
                        >
                          {c.configured
                            ? zh ? `已配置 (${c.source})` : `Configured (${c.source})`
                            : zh ? '未配置' : 'Not configured'}
                        </span>
                      </div>

                      {c.configured && (
                        <div style={{ fontSize: 11, color: '#8b949e', marginBottom: 6 }}>
                          {zh ? '已生效脱敏密钥' : 'Active Secret'}: <code style={{ color: '#58a6ff' }}>{c.masked_secret}</code>
                        </div>
                      )}

                      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                        <input
                          type={isVisible ? 'text' : 'password'}
                          placeholder={
                            c.configured
                              ? zh ? '输入新密钥以更新（留空保留原值）' : 'Enter new secret to update (blank to keep)'
                              : zh ? '粘贴 Google OAuth Client Secret (以 GOCSPX- 开头)' : 'Paste Client Secret (GOCSPX-...)'
                          }
                          value={inputVal}
                          onChange={(e) =>
                            setSecretInputs((prev) => ({
                              ...prev,
                              [c.client_id]: e.target.value
                            }))
                          }
                          style={{
                            flex: 1,
                            padding: '6px 10px',
                            fontSize: 12,
                            backgroundColor: '#161b22',
                            color: '#c9d1d9',
                            border: '1px solid #30363d',
                            borderRadius: 4,
                            outline: 'none'
                          }}
                        />
                        <button
                          type="button"
                          className="btn small"
                          onClick={() =>
                            setShowPlain((prev) => ({
                              ...prev,
                              [c.client_id]: !isVisible
                            }))
                          }
                          style={{ padding: '4px 8px', fontSize: 11 }}
                          title={isVisible ? '隐藏' : '显示明文'}
                        >
                          {isVisible ? '🙈' : '👁️'}
                        </button>
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </div>

          {/* Guide for other providers */}
          <div
            style={{
              padding: 16,
              border: '1px solid #30363d',
              borderRadius: 8,
              backgroundColor: 'rgba(255,255,255,0.01)'
            }}
          >
            <div style={{ fontWeight: 600, fontSize: 13, color: '#f0f6fc', marginBottom: 10 }}>
              {zh ? '其他 Provider 认证方式速查' : 'Other Providers Reference'}
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8, fontSize: 12, color: '#8b949e' }}>
              <div>
                <strong style={{ color: '#c9d1d9' }}>ChatGPT (Codex)</strong>:{' '}
                {zh
                  ? '支持在「添加账号」中粘贴 auth.json，或在额度页关联本地 Magpie 自动采集。'
                  : 'Import auth.json via "Add Account" or link local Magpie accounts below.'}
              </div>
              <div>
                <strong style={{ color: '#c9d1d9' }}>Trae CN</strong>:{' '}
                {zh
                  ? '直接粘贴个人中心积分请求中的 Cloud-IDE-JWT，或由 scripts/sync_trae_credential.py 自动同步。'
                  : 'Import Cloud-IDE-JWT directly or sync via scripts/sync_trae_credential.py.'}
              </div>
              <div>
                <strong style={{ color: '#c9d1d9' }}>WorkBuddy / QoderCN</strong>:{' '}
                {zh
                  ? '添加账号时凭证填写 auto，系统将自动读取并解密本地已登录凭据。'
                  : 'Enter "auto" in credential to automatically read local login sessions.'}
              </div>
            </div>
          </div>

          {/* Status Message */}
          {errorMsg && (
            <div
              style={{
                padding: '8px 12px',
                borderRadius: 6,
                fontSize: 12,
                backgroundColor: 'rgba(248, 81, 73, 0.15)',
                border: '1px solid #f85149',
                color: '#f85149'
              }}
            >
              ✗ {errorMsg}
            </div>
          )}

          {successMsg && (
            <div
              style={{
                padding: '8px 12px',
                borderRadius: 6,
                fontSize: 12,
                backgroundColor: 'rgba(46, 160, 67, 0.15)',
                border: '1px solid #2ea043',
                color: '#3fb950'
              }}
            >
              ✓ {successMsg}
            </div>
          )}
        </div>

        {/* Footer */}
        <div
          style={{
            padding: '12px 20px',
            borderTop: '1px solid #30363d',
            display: 'flex',
            justifyContent: 'flex-end',
            gap: 10,
            background: 'rgba(255,255,255,0.02)'
          }}
        >
          <button type="button" className="btn small" onClick={onClose} disabled={saving}>
            {zh ? '关闭' : 'Close'}
          </button>
          <button
            type="button"
            className="btn small primary"
            onClick={handleSave}
            disabled={saving || loading}
            style={{
              backgroundColor: '#238636',
              color: '#fff',
              borderColor: 'rgba(240, 246, 252, 0.1)'
            }}
          >
            {saving ? (zh ? '保存中...' : 'Saving...') : zh ? '保存配置' : 'Save Settings'}
          </button>
        </div>
      </div>
    </div>
  )
}
