import { useCallback, useEffect, useState } from 'react'
import {
  type InventoryHost,
  type SSHTestResult,
  listInventoryHosts,
  updateInventoryHost,
  deleteInventoryHost,
  testHostSSH,
  exportInventoryYaml,
  seedInventoryFromLegacy,
  api,
} from '../api'
import { useI18n } from '../i18n'
import HostConfigModal from './HostConfigModal'

type Props = {
  onDataChanged?: () => void
}

export default function InventoryManager({ onDataChanged }: Props) {
  const { language } = useI18n()
  const zh = language === 'zh'

  const [hosts, setHosts] = useState<InventoryHost[]>([])
  const [loading, setLoading] = useState(false)
  const [search, setSearch] = useState('')
  const [testingHostId, setTestingHostId] = useState<string | null>(null)
  const [sshResults, setSshResults] = useState<Record<string, SSHTestResult>>({})
  const [editingHost, setEditingHost] = useState<InventoryHost | null>(null)
  const [isModalOpen, setIsModalOpen] = useState(false)
  const [msg, setMsg] = useState<{ type: 'ok' | 'err'; text: string } | null>(null)
  const [busyAction, setBusyAction] = useState<string | null>(null)

  const loadHosts = useCallback(async () => {
    setLoading(true)
    try {
      const data = await listInventoryHosts()
      setHosts(data)
    } catch (err: any) {
      setMsg({ type: 'err', text: String(err.message || err) })
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadHosts()
  }, [loadHosts])

  const handleTestSSH = async (host: InventoryHost) => {
    setTestingHostId(host.id)
    try {
      const res = await testHostSSH(host.id || host.name)
      setSshResults((prev) => ({ ...prev, [host.id]: res }))
      if (res.ok) {
        setMsg({
          type: 'ok',
          text: zh
            ? `主机 ${host.name} SSH 连通正常 (延迟: ${res.latency_ms}ms)`
            : `Host ${host.name} SSH connected successfully (${res.latency_ms}ms)`,
        })
      } else {
        setMsg({
          type: 'err',
          text: zh
            ? `主机 ${host.name} SSH 连接失败: ${res.error || '未知错误'}`
            : `Host ${host.name} SSH failed: ${res.error || 'unknown error'}`,
        })
      }
    } catch (err: any) {
      setMsg({
        type: 'err',
        text: String(err.message || err),
      })
    } finally {
      setTestingHostId(null)
    }
  }

  const handleToggleEnabled = async (host: InventoryHost) => {
    try {
      const updated = await updateInventoryHost(host.id || host.name, {
        enabled: !host.enabled,
      })
      setHosts((prev) => prev.map((h) => (h.id === host.id ? updated : h)))
      onDataChanged?.()
    } catch (err: any) {
      setMsg({ type: 'err', text: String(err.message || err) })
    }
  }

  const handleDeleteHost = async (host: InventoryHost) => {
    const confirmText = zh
      ? `确认要从 SQLite 资产中心删除主机 ${host.name} 吗？`
      : `Are you sure you want to delete host ${host.name} from inventory?`
    if (!window.confirm(confirmText)) return

    try {
      await deleteInventoryHost(host.id || host.name)
      setHosts((prev) => prev.filter((h) => h.id !== host.id))
      setMsg({
        type: 'ok',
        text: zh ? `主机 ${host.name} 已成功移除` : `Host ${host.name} deleted`,
      })
      onDataChanged?.()
    } catch (err: any) {
      setMsg({ type: 'err', text: String(err.message || err) })
    }
  }

  const handleExportYaml = async () => {
    setBusyAction('export')
    try {
      const yamlContent = await exportInventoryYaml()
      const blob = new Blob([yamlContent], { type: 'text/yaml' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `inventory-export-${new Date().toISOString().slice(0, 10)}.yaml`
      a.click()
      URL.revokeObjectURL(url)
      setMsg({
        type: 'ok',
        text: zh ? '已成功导出 SQLite 主机配置为 YAML 文件' : 'Exported inventory YAML successfully',
      })
    } catch (err: any) {
      setMsg({ type: 'err', text: String(err.message || err) })
    } finally {
      setBusyAction(null)
    }
  }

  const handleSeedLegacy = async () => {
    setBusyAction('seed')
    try {
      const res = await seedInventoryFromLegacy()
      await loadHosts()
      setMsg({
        type: 'ok',
        text: zh
          ? `已从历史配置同步无损导入 (新增 ${res.created} 台, 更新 ${res.updated} 台)`
          : `Imported legacy files (created: ${res.created}, updated: ${res.updated})`,
      })
      onDataChanged?.()
    } catch (err: any) {
      setMsg({ type: 'err', text: String(err.message || err) })
    } finally {
      setBusyAction(null)
    }
  }

  const handleSyncFleet = async () => {
    setBusyAction('sync')
    try {
      const res = await api.syncInventory()
      await loadHosts()
      setMsg({
        type: 'ok',
        text: zh
          ? `集群探针与运行环境同步完成 (发现主机: ${res.hosts_seen ?? 'ok'})`
          : `Fleet runtimes synced (hosts seen: ${res.hosts_seen ?? 'ok'})`,
      })
      onDataChanged?.()
    } catch (err: any) {
      setMsg({ type: 'err', text: String(err.message || err) })
    } finally {
      setBusyAction(null)
    }
  }

  const filteredHosts = hosts.filter((h) => {
    if (!search.trim()) return true
    const q = search.toLowerCase()
    return (
      h.name.toLowerCase().includes(q) ||
      (h.description && h.description.toLowerCase().includes(q)) ||
      (h.ssh_host && h.ssh_host.toLowerCase().includes(q)) ||
      (h.proxy && h.proxy.toLowerCase().includes(q)) ||
      (h.target_repos && h.target_repos.some((r) => r.toLowerCase().includes(q)))
    )
  })

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {/* 提示消息 */}
      {msg && (
        <div
          className={msg.type === 'ok' ? 'tag ok' : 'err'}
          style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            padding: '8px 12px',
            fontSize: 12,
          }}
        >
          <span>{msg.text}</span>
          <button
            className="btn small"
            style={{ padding: '1px 6px', background: 'transparent', border: 'none', cursor: 'pointer' }}
            onClick={() => setMsg(null)}
          >
            ✕
          </button>
        </div>
      )}

      {/* 顶部操作工具栏 */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flex: 1, minWidth: 220 }}>
          <input
            type="text"
            className="input small"
            placeholder={zh ? '搜索主机名、IP、代理或仓库...' : 'Filter hosts, IP, proxy, repos...'}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{ width: '100%', maxWidth: 280 }}
          />
          <span className="small muted">
            {zh ? `共 ${hosts.length} 台主机` : `${hosts.length} hosts`}
          </span>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <button
            type="button"
            className="btn primary small"
            onClick={() => {
              setEditingHost(null)
              setIsModalOpen(true)
            }}
            style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}
          >
            <span>➕</span>
            <span>{zh ? '新增主机' : 'Add Host'}</span>
          </button>

          <button
            type="button"
            className="btn small"
            onClick={handleExportYaml}
            disabled={busyAction === 'export'}
            title={zh ? '将当前 SQLite 主机配置导出为 YAML' : 'Export SQLite inventory as YAML'}
          >
            {busyAction === 'export' ? '...' : `📥 ${zh ? '导出 YAML' : 'Export'}`}
          </button>

          <button
            type="button"
            className="btn small"
            onClick={handleSeedLegacy}
            disabled={busyAction === 'seed'}
            title={zh ? '从既有 legacy YAML 重新增量导入' : 'Re-seed from legacy YAML'}
          >
            {busyAction === 'seed' ? '...' : `🌱 ${zh ? '导入旧配置' : 'Seed'}`}
          </button>

          <button
            type="button"
            className="btn small"
            onClick={handleSyncFleet}
            disabled={busyAction === 'sync'}
            title={zh ? '触发集群运行时探针同步' : 'Sync fleet runtimes and probe'}
          >
            {busyAction === 'sync' ? '...' : `🔄 ${zh ? '同步探针' : 'Sync'}`}
          </button>
        </div>
      </div>

      {/* 主机资产表格 */}
      <div
        style={{
          border: '1px solid var(--border)',
          borderRadius: 8,
          overflowX: 'auto',
          background: 'var(--bg-card, #16181d)',
        }}
      >
        <table className="ts-table" style={{ fontSize: 12, width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ borderBottom: '1px solid var(--border)', background: 'var(--bg-subtle, rgba(255,255,255,0.02))' }}>
              <th style={{ padding: '8px 12px', textAlign: 'left' }}>{zh ? '主机标识 / 说明' : 'Host & Description'}</th>
              <th style={{ padding: '8px 12px', textAlign: 'left' }}>{zh ? 'SSH 连接' : 'SSH Connection'}</th>
              <th style={{ padding: '8px 12px', textAlign: 'left' }}>{zh ? '出站代理出口' : 'Egress Proxy'}</th>
              <th style={{ padding: '8px 12px', textAlign: 'left' }}>{zh ? 'Runner 纳管池' : 'Runner Fleet'}</th>
              <th style={{ padding: '8px 12px', textAlign: 'center' }}>{zh ? '连通探测' : 'SSH Probe'}</th>
              <th style={{ padding: '8px 12px', textAlign: 'center' }}>{zh ? '启用状态' : 'Status'}</th>
              <th style={{ padding: '8px 12px', textAlign: 'right' }}>{zh ? '操作' : 'Actions'}</th>
            </tr>
          </thead>
          <tbody>
            {loading && hosts.length === 0 ? (
              <tr>
                <td colSpan={7} style={{ textAlign: 'center', padding: '24px', color: 'var(--text-muted)' }}>
                  {zh ? '加载主机资产数据中...' : 'Loading hosts from SQLite...'}
                </td>
              </tr>
            ) : filteredHosts.length === 0 ? (
              <tr>
                <td colSpan={7} style={{ textAlign: 'center', padding: '24px', color: 'var(--text-muted)' }}>
                  {search ? (zh ? '无匹配的主机' : 'No matching hosts') : (zh ? '暂无主机资产，点击上方「新增主机」或「导入旧配置」' : 'No hosts enrolled. Add one or seed from legacy.')}
                </td>
              </tr>
            ) : (
              filteredHosts.map((h) => {
                const testRes = sshResults[h.id]
                const isTesting = testingHostId === h.id
                return (
                  <tr
                    key={h.id}
                    style={{
                      borderBottom: '1px solid var(--border)',
                      opacity: h.enabled ? 1 : 0.6,
                    }}
                  >
                    {/* 主机标识 & 说明 */}
                    <td style={{ padding: '10px 12px', verticalAlign: 'middle' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        <span className="bold mono" style={{ fontSize: 13 }}>{h.name}</span>
                        <span
                          className="tag"
                          style={{
                            fontSize: 10,
                            padding: '1px 5px',
                            background: 'rgba(56, 189, 248, 0.15)',
                            color: '#38bdf8',
                            border: '1px solid rgba(56, 189, 248, 0.3)',
                          }}
                        >
                          sqlite
                        </span>
                      </div>
                      {h.description && (
                        <div className="small muted" style={{ fontSize: 11, marginTop: 2 }}>
                          {h.description}
                        </div>
                      )}
                    </td>

                    {/* SSH 连接 */}
                    <td style={{ padding: '10px 12px', verticalAlign: 'middle' }}>
                      <div className="mono small">
                        {h.ssh_user ? <span style={{ color: 'var(--text-muted)' }}>{h.ssh_user}@</span> : null}
                        <span>{h.ssh_host || h.hostname || h.name}</span>
                        <span style={{ color: 'var(--text-muted)' }}>:{h.ssh_port || 22}</span>
                      </div>
                    </td>

                    {/* 出站代理 */}
                    <td style={{ padding: '10px 12px', verticalAlign: 'middle' }}>
                      {h.proxy ? (
                        <span
                          className="mono small"
                          style={{
                            background: 'rgba(168, 85, 247, 0.15)',
                            color: '#c084fc',
                            padding: '2px 6px',
                            borderRadius: 4,
                            border: '1px solid rgba(168, 85, 247, 0.3)',
                            fontSize: 11,
                          }}
                          title={zh ? '出站经由此代理长轮询，0 端口暴露' : 'Outbound long-polling proxy'}
                        >
                          🌐 {h.proxy}
                        </span>
                      ) : (
                        <span className="small muted" style={{ fontSize: 11 }}>
                          {zh ? '直连 (Direct)' : 'Direct'}
                        </span>
                      )}
                    </td>

                    {/* Runner 纳管池 */}
                    <td style={{ padding: '10px 12px', verticalAlign: 'middle' }}>
                      {h.runner_enabled ? (
                        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                            <span
                              className="tag ok"
                              style={{ fontSize: 10, padding: '1px 6px', fontWeight: 600 }}
                            >
                              ⚡ 并发: {h.runner_max_concurrency || 2}
                            </span>
                          </div>
                          {h.target_repos && h.target_repos.length > 0 ? (
                            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                              {h.target_repos.map((repo) => (
                                <span
                                  key={repo}
                                  className="mono"
                                  style={{
                                    fontSize: 10,
                                    background: 'var(--bg-subtle, rgba(255,255,255,0.05))',
                                    padding: '1px 5px',
                                    borderRadius: 3,
                                    border: '1px solid var(--border)',
                                  }}
                                >
                                  {repo}
                                </span>
                              ))}
                            </div>
                          ) : (
                            <span className="small muted" style={{ fontSize: 10 }}>
                              {zh ? '所有仓库池' : 'All pools'}
                            </span>
                          )}
                        </div>
                      ) : (
                        <span className="small muted" style={{ fontSize: 11 }}>
                          {zh ? '未启用' : 'Off'}
                        </span>
                      )}
                    </td>

                    {/* 连通探测 */}
                    <td style={{ padding: '10px 12px', verticalAlign: 'middle', textAlign: 'center' }}>
                      <div style={{ display: 'inline-flex', flexDirection: 'column', alignItems: 'center', gap: 3 }}>
                        <button
                          type="button"
                          className="btn small"
                          style={{ padding: '2px 8px', fontSize: 11 }}
                          onClick={() => handleTestSSH(h)}
                          disabled={isTesting}
                          title={zh ? '测试 SSH 连通性' : 'Test SSH connectivity'}
                        >
                          {isTesting ? (zh ? '探测中...' : 'Testing...') : `⚡ ${zh ? '探测' : 'Test'}`}
                        </button>
                        {testRes && (
                          <span
                            className={'tag ' + (testRes.ok ? 'ok' : 'bad')}
                            style={{ fontSize: 10, padding: '1px 5px' }}
                            title={testRes.error || testRes.banner || ''}
                          >
                            {testRes.ok ? `✓ ${testRes.latency_ms}ms` : '✕ 失败'}
                          </span>
                        )}
                      </div>
                    </td>

                    {/* 启用状态 */}
                    <td style={{ padding: '10px 12px', verticalAlign: 'middle', textAlign: 'center' }}>
                      <button
                        type="button"
                        className={'tag ' + (h.enabled ? 'ok' : 'unknown')}
                        style={{ cursor: 'pointer', padding: '3px 8px', fontSize: 11 }}
                        onClick={() => handleToggleEnabled(h)}
                      >
                        {h.enabled ? (zh ? '已启用' : 'Active') : (zh ? '已停用' : 'Disabled')}
                      </button>
                    </td>

                    {/* 操作按钮 */}
                    <td style={{ padding: '10px 12px', verticalAlign: 'middle', textAlign: 'right' }}>
                      <div style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                        <button
                          type="button"
                          className="btn small"
                          style={{ padding: '3px 7px' }}
                          onClick={() => {
                            setEditingHost(h)
                            setIsModalOpen(true)
                          }}
                          title={zh ? '编辑配置' : 'Edit Host'}
                        >
                          ✏️
                        </button>
                        <button
                          type="button"
                          className="btn small"
                          style={{ padding: '3px 7px', color: 'var(--err-fg, #ef4444)' }}
                          onClick={() => handleDeleteHost(h)}
                          title={zh ? '删除主机' : 'Delete Host'}
                        >
                          🗑️
                        </button>
                      </div>
                    </td>
                  </tr>
                )
              })
            )}
          </tbody>
        </table>
      </div>

      {/* 新增 / 编辑 模态框 */}
      <HostConfigModal
        isOpen={isModalOpen}
        initialHost={editingHost}
        onClose={() => {
          setIsModalOpen(false)
          setEditingHost(null)
        }}
        onSaved={async () => {
          await loadHosts()
          setMsg({
            type: 'ok',
            text: zh ? '主机配置已保存至 SQLite 资产库' : 'Host saved to SQLite inventory',
          })
          onDataChanged?.()
        }}
      />
    </div>
  )
}
