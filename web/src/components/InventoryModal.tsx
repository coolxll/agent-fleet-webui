import { useEffect, useState } from 'react'
import { getInventoryRaw, validateInventory, saveInventoryRaw } from '../api'
import { useI18n } from '../i18n'

type Props = {
  isOpen: boolean
  onClose: () => void
  onSaved: () => void
}

export default function InventoryModal({ isOpen, onClose, onSaved }: Props) {
  const { language } = useI18n()
  const zh = language === 'zh'

  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [validating, setValidating] = useState(false)
  const [path, setPath] = useState('inventory.yaml')
  const [content, setContent] = useState('')
  const [exampleContent, setExampleContent] = useState('')
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const [successMsg, setSuccessMsg] = useState<string | null>(null)
  const [validationInfo, setValidationInfo] = useState<{
    valid: boolean
    error: string | null
    summary?: any
  } | null>(null)

  useEffect(() => {
    if (!isOpen) {
      setValidationInfo(null)
      setErrorMsg(null)
      setSuccessMsg(null)
      return
    }

    setLoading(true)
    setErrorMsg(null)
    getInventoryRaw()
      .then((res) => {
        setPath(res.path || 'inventory.yaml')
        setContent(res.content || res.example_content || '')
        setExampleContent(res.example_content || '')
      })
      .catch((err) => {
        setErrorMsg(String(err.message || err))
      })
      .finally(() => {
        setLoading(false)
      })
  }, [isOpen])

  if (!isOpen) return null

  const handleValidate = async () => {
    setValidating(true)
    setErrorMsg(null)
    setSuccessMsg(null)
    try {
      const res = await validateInventory(content)
      setValidationInfo({
        valid: res.valid,
        error: res.error,
        summary: res.summary
      })
    } catch (err: any) {
      setValidationInfo({
        valid: false,
        error: String(err.message || err)
      })
    } finally {
      setValidating(false)
    }
  }

  const handleSave = async () => {
    setSaving(true)
    setErrorMsg(null)
    setSuccessMsg(null)
    try {
      const res = await saveInventoryRaw(content)
      setSuccessMsg(
        zh
          ? `已成功保存至 ${res.path}，并完成集群同步！`
          : `Saved to ${res.path} and fleet synced!`
      )
      setTimeout(() => {
        onSaved()
        onClose()
      }, 700)
    } catch (err: any) {
      setErrorMsg(String(err.message || err))
    } finally {
      setSaving(false)
    }
  }

  const handleLoadExample = () => {
    if (exampleContent) {
      setContent(exampleContent)
      setValidationInfo(null)
      setSuccessMsg(zh ? '已载入示例模版' : 'Loaded template')
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
          maxWidth: 780,
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
            <span style={{ fontSize: 18 }}>⚙️</span>
            <div>
              <div style={{ fontWeight: 600, fontSize: 15, color: '#f0f6fc' }}>
                {zh ? '配置集群主机 Inventory' : 'Fleet Inventory Configuration'}
              </div>
              <div style={{ fontSize: 12, color: '#8b949e', marginTop: 2 }}>
                {zh ? '配置文件' : 'File'}: <code style={{ color: '#58a6ff' }}>{path}</code>
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
            gap: 12,
            flex: 1
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div style={{ fontSize: 12, color: '#8b949e' }}>
              {zh
                ? '声明集群主机与运行环境（macOS, Linux, Windows, WSL）。'
                : 'Declare target hosts and runtimes (macOS, Linux, Windows, WSL).'}
            </div>
            {exampleContent && (
              <button
                type="button"
                className="btn small"
                onClick={handleLoadExample}
                style={{ fontSize: 11, padding: '3px 8px' }}
              >
                📋 {zh ? '载入示例模版' : 'Load Example'}
              </button>
            )}
          </div>

          {/* Code Editor */}
          <textarea
            value={content}
            onChange={(e) => {
              setContent(e.target.value)
              if (validationInfo) setValidationInfo(null)
              if (errorMsg) setErrorMsg(null)
            }}
            disabled={loading}
            placeholder={zh ? '正在读取配置...' : 'Loading config...'}
            spellCheck={false}
            style={{
              width: '100%',
              minHeight: 340,
              flex: 1,
              fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
              fontSize: 12.5,
              lineHeight: 1.5,
              backgroundColor: '#0d1117',
              color: '#c9d1d9',
              border: '1px solid #30363d',
              borderRadius: 6,
              padding: 12,
              resize: 'vertical',
              boxSizing: 'border-box',
              outline: 'none'
            }}
          />

          {/* Validation / Alert Message */}
          {validationInfo && (
            <div
              style={{
                padding: '10px 14px',
                borderRadius: 6,
                fontSize: 12.5,
                backgroundColor: validationInfo.valid ? 'rgba(46, 160, 67, 0.15)' : 'rgba(248, 81, 73, 0.15)',
                border: `1px solid ${validationInfo.valid ? '#2ea043' : '#f85149'}`,
                color: validationInfo.valid ? '#3fb950' : '#f85149'
              }}
            >
              {validationInfo.valid ? (
                <div>
                  ✓ {zh ? 'YAML 语法与结构校验通过' : 'Validation passed'}
                  {validationInfo.summary?.host_count !== undefined && (
                    <span style={{ marginLeft: 8, color: '#8b949e' }}>
                      ({zh ? '包含主机' : 'hosts'}: {validationInfo.summary.host_count}
                      {validationInfo.summary.wsl_count ? `, WSL: ${validationInfo.summary.wsl_count}` : ''})
                    </span>
                  )}
                </div>
              ) : (
                <div>✗ {validationInfo.error}</div>
              )}
            </div>
          )}

          {errorMsg && (
            <div
              style={{
                padding: '10px 14px',
                borderRadius: 6,
                fontSize: 12.5,
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
                padding: '10px 14px',
                borderRadius: 6,
                fontSize: 12.5,
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
            padding: '14px 20px',
            borderTop: '1px solid #30363d',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            background: 'rgba(255,255,255,0.02)'
          }}
        >
          <button
            type="button"
            className="btn small"
            onClick={handleValidate}
            disabled={validating || saving || !content.trim()}
            style={{ display: 'flex', alignItems: 'center', gap: 6 }}
          >
            {validating ? '...' : '🔍 ' + (zh ? '校验语法' : 'Validate')}
          </button>

          <div style={{ display: 'flex', gap: 10 }}>
            <button type="button" className="btn small" onClick={onClose} disabled={saving}>
              {zh ? '取消' : 'Cancel'}
            </button>
            <button
              type="button"
              className="btn small primary"
              onClick={handleSave}
              disabled={saving || !content.trim()}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                backgroundColor: '#238636',
                color: '#fff',
                borderColor: 'rgba(240, 246, 252, 0.1)'
              }}
            >
              {saving ? (zh ? '保存中...' : 'Saving...') : '💾 ' + (zh ? '保存并同步' : 'Save & Sync')}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
