import { useEffect, useState } from 'react'
import {
  type InventoryHost,
  type InventoryHostPayload,
  createInventoryHost,
  updateInventoryHost,
} from '../api'
import { useI18n } from '../i18n'

type Props = {
  isOpen: boolean
  initialHost?: InventoryHost | null
  onClose: () => void
  onSaved: (savedHost: InventoryHost) => void
}

export default function HostConfigModal({
  isOpen,
  initialHost,
  onClose,
  onSaved,
}: Props) {
  const { language } = useI18n()
  const zh = language === 'zh'
  const isEditing = Boolean(initialHost)

  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [sshHost, setSshHost] = useState('')
  const [sshPort, setSshPort] = useState(22)
  const [sshUser, setSshUser] = useState('lynn')
  const [proxy, setProxy] = useState('')
  const [runnerEnabled, setRunnerEnabled] = useState(false)
  const [runnerMaxConcurrency, setRunnerMaxConcurrency] = useState(2)
  const [labelsText, setLabelsText] = useState('')
  const [targetReposText, setTargetReposText] = useState('')
  const [enabled, setEnabled] = useState(true)

  const [saving, setSaving] = useState(false)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)

  useEffect(() => {
    if (!isOpen) return
    setErrorMsg(null)
    if (initialHost) {
      setName(initialHost.name)
      setDescription(initialHost.description || '')
      setSshHost(initialHost.ssh_host || initialHost.hostname || initialHost.name)
      setSshPort(initialHost.ssh_port || 22)
      setSshUser(initialHost.ssh_user || 'lynn')
      setProxy(initialHost.proxy || '')
      setRunnerEnabled(Boolean(initialHost.runner_enabled))
      setRunnerMaxConcurrency(initialHost.runner_max_concurrency || 2)
      setLabelsText((initialHost.runner_labels || []).join(', '))
      setTargetReposText((initialHost.target_repos || []).join('\n'))
      setEnabled(initialHost.enabled !== false)
    } else {
      setName('')
      setDescription('')
      setSshHost('')
      setSshPort(22)
      setSshUser('lynn')
      setProxy('')
      setRunnerEnabled(true)
      setRunnerMaxConcurrency(2)
      setLabelsText('self-hosted, Linux, X64')
      setTargetReposText('')
      setEnabled(true)
    }
  }, [isOpen, initialHost])

  if (!isOpen) return null

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    const cleanName = name.trim()
    if (!cleanName) {
      setErrorMsg(zh ? '请输入主机标识名称' : 'Host name is required')
      return
    }

    const labels = labelsText
      .split(/[,，\n]/)
      .map((s) => s.trim())
      .filter(Boolean)

    const targetRepos = targetReposText
      .split(/[,，\n]/)
      .map((s) => s.trim())
      .filter(Boolean)

    const payload: InventoryHostPayload = {
      name: cleanName,
      hostname: sshHost.trim() || cleanName,
      description: description.trim(),
      ssh_host: sshHost.trim() || cleanName,
      ssh_port: Number(sshPort) || 22,
      ssh_user: sshUser.trim() || 'lynn',
      proxy: proxy.trim(),
      runner_enabled: runnerEnabled,
      runner_max_concurrency: Number(runnerMaxConcurrency) || 2,
      runner_labels: labels.length > 0 ? labels : [cleanName],
      target_repos: targetRepos,
      enabled,
    }

    setSaving(true)
    setErrorMsg(null)
    try {
      let saved: InventoryHost
      if (isEditing && initialHost) {
        saved = await updateInventoryHost(initialHost.id || initialHost.name, payload)
      } else {
        saved = await createInventoryHost(payload)
      }
      onSaved(saved)
      onClose()
    } catch (err: any) {
      setErrorMsg(String(err.message || err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose} style={{ zIndex: 1100 }}>
      <div
        className="modal-card"
        onClick={(e) => e.stopPropagation()}
        style={{
          maxWidth: 620,
          maxHeight: '90vh',
          display: 'flex',
          flexDirection: 'column',
          padding: 0,
          overflow: 'hidden',
        }}
      >
        <div className="modal-head" style={{ padding: '14px 20px', borderBottom: '1px solid var(--border)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: 18 }}>🖥️</span>
            <span style={{ fontWeight: 600, fontSize: 16 }}>
              {isEditing
                ? zh
                  ? `编辑主机资产: ${initialHost?.name}`
                  : `Edit Host: ${initialHost?.name}`
                : zh
                ? '新增主机资产 & Runner 纳管'
                : 'Add Managed Host & Runner'}
            </span>
          </div>
          <button className="btn small" onClick={onClose} style={{ padding: '2px 8px' }}>
            ✕
          </button>
        </div>

        <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', flex: 1, overflow: 'hidden' }}>
          <div className="modal-body" style={{ flex: 1, overflowY: 'auto', padding: '18px 20px', display: 'flex', flexDirection: 'column', gap: 16 }}>
            {errorMsg && (
              <div className="err" style={{ marginBottom: 4, padding: '8px 12px' }}>
                {errorMsg}
              </div>
            )}

            {/* 基本属性 */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div style={{ fontWeight: 600, fontSize: 13, color: 'var(--text-muted)' }}>
                {zh ? '基础标识与说明' : 'General & Metadata'}
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                <div>
                  <label className="small bold" style={{ display: 'block', marginBottom: 4 }}>
                    {zh ? '主机标识 (Name) *' : 'Host Identifier *'}
                  </label>
                  <input
                    type="text"
                    className="input full"
                    placeholder="linux-srv-01"
                    value={name}
                    disabled={isEditing}
                    onChange={(e) => setName(e.target.value)}
                    required
                  />
                </div>
                <div>
                  <label className="small bold" style={{ display: 'block', marginBottom: 4 }}>
                    {zh ? '主机简要描述' : 'Description'}
                  </label>
                  <input
                    type="text"
                    className="input full"
                    placeholder={zh ? '例如: 核心工作站 & 出站代理' : 'e.g. Primary dev workstation'}
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                  />
                </div>
              </div>
            </div>

            {/* SSH 配置 */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div style={{ fontWeight: 600, fontSize: 13, color: 'var(--text-muted)' }}>
                {zh ? 'SSH 连接配置' : 'SSH Connection'}
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr 1fr', gap: 10 }}>
                <div>
                  <label className="small bold" style={{ display: 'block', marginBottom: 4 }}>
                    {zh ? 'SSH 地址 / IP / 别名' : 'SSH Host / IP / Alias'}
                  </label>
                  <input
                    type="text"
                    className="input full"
                    placeholder="192.168.1.100 或 linux-srv-01"
                    value={sshHost}
                    onChange={(e) => setSshHost(e.target.value)}
                  />
                </div>
                <div>
                  <label className="small bold" style={{ display: 'block', marginBottom: 4 }}>
                    {zh ? 'SSH 端口' : 'Port'}
                  </label>
                  <input
                    type="number"
                    className="input full"
                    value={sshPort}
                    onChange={(e) => setSshPort(Number(e.target.value))}
                  />
                </div>
                <div>
                  <label className="small bold" style={{ display: 'block', marginBottom: 4 }}>
                    {zh ? 'SSH 用户' : 'User'}
                  </label>
                  <input
                    type="text"
                    className="input full"
                    value={sshUser}
                    onChange={(e) => setSshUser(e.target.value)}
                  />
                </div>
              </div>
            </div>

            {/* 出站代理 */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <label className="small bold">
                  {zh ? '出站统一代理出口 (Proxy Egress)' : 'Outbound Proxy Egress'}
                </label>
                <span className="small muted" style={{ fontSize: 11 }}>
                  {zh ? '对公网 0 端口暴露' : 'Zero inbound exposure'}
                </span>
              </div>
              <input
                type="text"
                className="input full mono"
                placeholder="http://127.0.0.1:8118 或 socks5://100.64.0.1:1080"
                value={proxy}
                onChange={(e) => setProxy(e.target.value)}
              />
              <div className="small muted" style={{ fontSize: 11 }}>
                {zh
                  ? '留空表示直连。配置后 Runner 或探针的出站长轮询流量将经由指定代理出口。'
                  : 'Leave empty for direct connection. Directs outbound long-polling via proxy.'}
              </div>
            </div>

            {/* Runner Fleet 纳管 */}
            <div
              style={{
                border: '1px solid var(--border)',
                borderRadius: 8,
                padding: '12px 14px',
                background: 'var(--bg-subtle, rgba(255,255,255,0.02))',
                display: 'flex',
                flexDirection: 'column',
                gap: 12,
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div>
                  <div style={{ fontWeight: 600, fontSize: 13 }}>
                    ⚡ {zh ? 'GitHub Actions Runner Fleet 纳管' : 'Runner Fleet Management'}
                  </div>
                  <div className="small muted" style={{ fontSize: 11 }}>
                    {zh ? '允许将此主机作为 Self-Hosted Runner 编排池' : 'Enroll host in runner fleet pools'}
                  </div>
                </div>
                <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
                  <input
                    type="checkbox"
                    checked={runnerEnabled}
                    onChange={(e) => setRunnerEnabled(e.target.checked)}
                  />
                  <span className="small bold">{zh ? '启用 Runner' : 'Enable Runner'}</span>
                </label>
              </div>

              {runnerEnabled && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 4 }}>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 2fr', gap: 12 }}>
                    <div>
                      <label className="small bold" style={{ display: 'block', marginBottom: 4 }}>
                        {zh ? '并发数 (Max Concurrency)' : 'Max Concurrency'}
                      </label>
                      <input
                        type="number"
                        min="1"
                        max="16"
                        className="input full"
                        value={runnerMaxConcurrency}
                        onChange={(e) => setRunnerMaxConcurrency(Number(e.target.value))}
                      />
                    </div>
                    <div>
                      <label className="small bold" style={{ display: 'block', marginBottom: 4 }}>
                        {zh ? 'Runner 标签 (逗号分隔)' : 'Runner Labels (comma separated)'}
                      </label>
                      <input
                        type="text"
                        className="input full"
                        placeholder="self-hosted, Linux, X64"
                        value={labelsText}
                        onChange={(e) => setLabelsText(e.target.value)}
                      />
                    </div>
                  </div>

                  <div>
                    <label className="small bold" style={{ display: 'block', marginBottom: 4 }}>
                      {zh ? '目标仓库矩阵 (Target Repos, 每行一个)' : 'Target Repos (one per line)'}
                    </label>
                    <textarea
                      rows={3}
                      className="input full mono"
                      placeholder="coolxll/agent-fleet-webui"
                      style={{ resize: 'vertical', fontSize: 12 }}
                      value={targetReposText}
                      onChange={(e) => setTargetReposText(e.target.value)}
                    />
                    <div className="small muted" style={{ fontSize: 11, marginTop: 2 }}>
                      {zh
                        ? '指定为哪些 GitHub 仓库注册 Runner 监听队列'
                        : 'GitHub repositories to bind self-hosted runner worker pools'}
                    </div>
                  </div>
                </div>
              )}
            </div>

            {/* 启用总开关 */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 4 }}>
              <input
                type="checkbox"
                id="host-enabled-check"
                checked={enabled}
                onChange={(e) => setEnabled(e.target.checked)}
              />
              <label htmlFor="host-enabled-check" className="small" style={{ cursor: 'pointer' }}>
                {zh ? '参与探针采集与集群巡检 (Enabled in Fleet)' : 'Include in fleet scans and monitoring'}
              </label>
            </div>
          </div>

          <div
            className="modal-foot"
            style={{
              padding: '12px 20px',
              borderTop: '1px solid var(--border)',
              display: 'flex',
              justifyContent: 'flex-end',
              gap: 10,
            }}
          >
            <button type="button" className="btn" onClick={onClose} disabled={saving}>
              {zh ? '取消' : 'Cancel'}
            </button>
            <button type="submit" className="btn primary" disabled={saving}>
              {saving ? (zh ? '保存中...' : 'Saving...') : zh ? '保存主机配置' : 'Save Host'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
