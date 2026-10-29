import { useEffect, useState } from 'react'
import { getInventoryRaw, validateInventory, saveInventoryRaw } from '../api'
import { useI18n } from '../i18n'
import InventoryManager from './InventoryManager'

type Props = {
  isOpen: boolean
  onClose: () => void
  onSaved: () => void
}

export default function InventoryModal({ isOpen, onClose, onSaved }: Props) {
  const { language } = useI18n()
  const zh = language === 'zh'

  const [activeTab, setActiveTab] = useState<'structured' | 'yaml'>('structured')
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

    if (activeTab === 'yaml') {
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
    }
  }, [isOpen, activeTab])

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
        summary: res.summary,
      })
    } catch (err: any) {
      setValidationInfo({
        valid: false,
        error: String(err.message || err),
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
        padding: 20,
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div
        style={{
          width: '100%',
          maxWidth: activeTab === 'structured' ? 980 : 780,
          maxHeight: '90vh',
          backgroundColor: '#161b22',
          border: '1px solid #30363d',
          borderRadius: 10,
          display: 'flex',
          flexDirection: 'column',
          boxShadow: '0 20px 40px rgba(0,0,0,0.6)',
          overflow: 'hidden',
          transition: 'max-width 0.2s ease',
        }}
      >
        {/* Header */}
        <div
          style={{
            padding: '12px 20px',
            borderBottom: '1px solid #30363d',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            background: 'rgba(255,255,255,0.02)',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ fontSize: 18 }}>⚙️</span>
            <div>
              <div style={{ fontWeight: 600, fontSize: 15, color: '#f0f6fc' }}>
                {zh ? '主机资产与 Runner 纳管中心' : 'Managed Hosts & Runner Fleet'}
              </div>
              <div style={{ fontSize: 11, color: '#8b949e', marginTop: 2 }}>
                {zh
                  ? 'SQLite 统一真理源 · 零公网端口暴露 · 出站代理调度'
                  : 'SQLite Single Source of Truth · Egress Proxy Scheduled'}
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

        {/* 顶部标签切换 */}
        <div
          style={{
            display: 'flex',
            borderBottom: '1px solid #30363d',
            background: 'rgba(0,0,0,0.15)',
            padding: '0 16px',
            gap: 8,
          }}
        >
          <button
            type="button"
            onClick={() => setActiveTab('structured')}
            style={{
              padding: '10px 14px',
              background: 'transparent',
              border: 'none',
              borderBottom: activeTab === 'structured' ? '2px solid #58a6ff' : '2px solid transparent',
              color: activeTab === 'structured' ? '#f0f6fc' : '#8b949e',
              fontWeight: activeTab === 'structured' ? 600 : 400,
              fontSize: 13,
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: 6,
            }}
          >
            <span>🗄️</span>
            <span>{zh ? '结构化配置表 (SQLite)' : 'Structured Table (SQLite)'}</span>
          </button>

          <button
            type="button"
            onClick={() => setActiveTab('yaml')}
            style={{
              padding: '10px 14px',
              background: 'transparent',
              border: 'none',
              borderBottom: activeTab === 'yaml' ? '2px solid #58a6ff' : '2px solid transparent',
              color: activeTab === 'yaml' ? '#f0f6fc' : '#8b949e',
              fontWeight: activeTab === 'yaml' ? 600 : 400,
              fontSize: 13,
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: 6,
            }}
          >
            <span>📄</span>
            <span>{zh ? '原始 YAML 视图' : 'Raw YAML Editor'}</span>
          </button>
        </div>

        {/* Tab 1: 结构化配置表 */}
        {activeTab === 'structured' && (
          <div style={{ padding: 18, overflowY: 'auto', flex: 1 }}>
            <InventoryManager onDataChanged={onSaved} />
          </div>
        )}

        {/* Tab 2: 原始 YAML 视图 */}
        {activeTab === 'yaml' && (
          <div
            style={{
              padding: 20,
              overflowY: 'auto',
              display: 'flex',
              flexDirection: 'column',
              gap: 12,
              flex: 1,
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div style={{ fontSize: 12, color: '#8b949e' }}>
                {zh ? (
                  <>
                    高级模式：直接编辑底层 <code style={{ color: '#58a6ff' }}>{path}</code> 文件。保存后将自动同步至集群。
                  </>
                ) : (
                  <>
                    Advanced: Edit declarative <code style={{ color: '#58a6ff' }}>{path}</code> directly.
                  </>
                )}
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
                minHeight: 320,
                flex: 1,
                fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace',
                fontSize: 12.5,
                lineHeight: 1.5,
                backgroundColor: '#0d1117',
                color: '#c9d1d9',
                border: '1px solid #30363d',
                borderRadius: 6,
                padding: 12,
                resize: 'none',
              }}
            />

            {errorMsg && (
              <div
                style={{
                  padding: '8px 12px',
                  backgroundColor: 'rgba(248, 81, 73, 0.1)',
                  border: '1px solid #f85149',
                  borderRadius: 6,
                  color: '#f85149',
                  fontSize: 12,
                }}
              >
                ⚠️ {errorMsg}
              </div>
            )}

            {successMsg && (
              <div
                style={{
                  padding: '8px 12px',
                  backgroundColor: 'rgba(46, 160, 67, 0.15)',
                  border: '1px solid #2ea043',
                  borderRadius: 6,
                  color: '#3fb950',
                  fontSize: 12,
                }}
              >
                ✅ {successMsg}
              </div>
            )}

            {validationInfo && (
              <div
                style={{
                  padding: '8px 12px',
                  backgroundColor: validationInfo.valid ? 'rgba(56, 139, 253, 0.1)' : 'rgba(248, 81, 73, 0.1)',
                  border: `1px solid ${validationInfo.valid ? '#388bfd' : '#f85149'}`,
                  borderRadius: 6,
                  fontSize: 12,
                }}
              >
                {validationInfo.valid ? (
                  <div>
                    <span style={{ color: '#58a6ff', fontWeight: 600 }}>✓ YAML 格式校验通过</span>
                    {validationInfo.summary && (
                      <span style={{ marginLeft: 8, color: '#8b949e' }}>
                        (解析出 {validationInfo.summary.hosts_count || 0} 台主机)
                      </span>
                    )}
                  </div>
                ) : (
                  <div style={{ color: '#f85149' }}>
                    ✕ 校验未通过: {validationInfo.error}
                  </div>
                )}
              </div>
            )}

            <div
              style={{
                display: 'flex',
                justifyContent: 'flex-end',
                alignItems: 'center',
                gap: 10,
                marginTop: 4,
              }}
            >
              <button
                type="button"
                className="btn"
                onClick={handleValidate}
                disabled={validating || loading}
                style={{ fontSize: 12 }}
              >
                {validating ? (zh ? '校验中...' : 'Validating...') : zh ? '校验 YAML 格式' : 'Validate'}
              </button>
              <button
                type="button"
                className="btn primary"
                onClick={handleSave}
                disabled={saving || loading}
                style={{ fontSize: 12 }}
              >
                {saving ? (zh ? '保存并同步中...' : 'Saving...') : zh ? '保存并同步到集群' : 'Save & Sync'}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
