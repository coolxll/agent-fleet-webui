import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import MachinesView from './views/MachinesView'
import AgentsView from './views/AgentsView'
import Matrix from './views/Matrix'
import SettingsModal from './components/SettingsModal'
import TasksDrawer from './components/TasksDrawer'
import { useI18n } from './i18n'

type ViewTab = 'machines' | 'agents' | 'matrix'

export default function App() {
  const { t, language } = useI18n()
  const [tab, setTab] = useState<ViewTab>(() => {
    return (localStorage.getItem('agent_fleet_active_tab') as ViewTab) || 'machines'
  })
  const [runId, setRunId] = useState<string | null>(null)
  const [progress, setProgress] = useState<{
    done: number
    total: number
    ok: number
    failed: number
  } | null>(null)
  const [busy, setBusy] = useState(false)
  const [toast, setToast] = useState<string | null>(null)
  const [showSettings, setShowSettings] = useState(false)
  const [showTasksDrawer, setShowTasksDrawer] = useState(false)
  const [activeUpgradeCount, setActiveUpgradeCount] = useState(0)
  const [refreshKey, setRefreshKey] = useState(0)

  const handleTabSwitch = (nextTab: ViewTab) => {
    setTab(nextTab)
    localStorage.setItem('agent_fleet_active_tab', nextTab)
  }

  // Poll an in-flight scan
  useEffect(() => {
    if (!runId) {
      setProgress(null)
      return
    }
    let alive = true
    const poll = async () => {
      try {
        const run = await api.scan(runId)
        if (!alive) return
        setProgress({
          done: run.live.done ?? run.succeeded + run.failed,
          total: run.total,
          ok: run.live.ok ?? run.succeeded,
          failed: run.live.failed ?? run.failed
        })
        if (run.status !== 'running') {
          setRunId(null)
          setProgress(null)
          setRefreshKey((k) => k + 1)
          setToast(t('toast.scanFinished', { ok: run.succeeded, failed: run.failed }))
        }
      } catch {
        /* keep trying */
      }
    }
    poll()
    const tInterval = setInterval(poll, 1500)
    return () => {
      alive = false
      clearInterval(tInterval)
    }
  }, [runId, t])

  const scanAll = useCallback(async () => {
    setBusy(true)
    try {
      const res = await api.startScan(null)
      setRunId(res.run_id)
      setToast(t('toast.scanningCount', { count: res.total }))
    } catch (e: any) {
      setToast(String(e.message || e))
    } finally {
      setBusy(false)
    }
  }, [t])

  useEffect(() => {
    if (!toast) return
    const timer = setTimeout(() => setToast(null), 4000)
    return () => clearTimeout(timer)
  }, [toast])

  // Poll background upgrade tasks to maintain active count and trigger auto-refresh on completion
  useEffect(() => {
    let alive = true
    let prevActiveCount = 0

    const checkUpgrades = async () => {
      try {
        const res = await api.listUpgradeTasks()
        if (!alive) return
        const active = (res.tasks || []).filter(
          (u) => u.status === 'running' || u.status === 'pending'
        )
        setActiveUpgradeCount(active.length)

        // If an active upgrade just finished, refresh data automatically
        if (prevActiveCount > 0 && active.length < prevActiveCount) {
          setRefreshKey((k) => k + 1)
          const justFinished = (res.tasks || []).find(
            (u) => u.status === 'completed' && u.ok
          )
          if (justFinished) {
            setToast(
              language === 'zh'
                ? `🎉 ${justFinished.agent} (${justFinished.host}) 升级成功！`
                : `🎉 Upgrade succeeded for ${justFinished.agent} (${justFinished.host})!`
            )
          }
        }
        prevActiveCount = active.length
      } catch {
        /* ignore */
      }
    }

    checkUpgrades()
    const timer = setInterval(checkUpgrades, 2500)
    return () => {
      alive = false
      clearInterval(timer)
    }
  }, [language])

  return (
    <div>
      <header className="app-header">
        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          <div className="brand-logo" onClick={() => handleTabSwitch('machines')}>
            <span style={{ fontSize: 16 }}>⚡</span>
            <span style={{ fontWeight: 700, fontSize: 15, letterSpacing: 0.4 }}>Agent Fleet</span>
          </div>

          <nav className="tabs" style={{ marginLeft: 6 }}>
            <button
              className={tab === 'machines' ? 'active' : ''}
              onClick={() => handleTabSwitch('machines')}
            >
              🖥️ {t('nav.machines')}
            </button>
            <button
              className={tab === 'agents' ? 'active' : ''}
              onClick={() => handleTabSwitch('agents')}
            >
              🤖 {t('nav.agents')}
            </button>
            <button
              className={tab === 'matrix' ? 'active' : ''}
              onClick={() => handleTabSwitch('matrix')}
            >
              🎛️ {t('nav.matrix')}
            </button>
          </nav>
        </div>

        <div className="header-actions" style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 10 }}>
          {progress ? (
            <div className="scan-bar">
              <div className="bar">
                <div
                  style={{
                    width: Math.round((progress.done / Math.max(1, progress.total)) * 100) + '%'
                  }}
                />
              </div>
              <span className="small">
                {t('scan.progress', {
                  done: progress.done,
                  total: progress.total,
                  ok: progress.ok,
                  failed: progress.failed
                })}
              </span>
            </div>
          ) : null}

          {/* Tasks button */}
          <button
            className={'btn small ' + (activeUpgradeCount > 0 ? 'primary ' : '')}
            onClick={() => setShowTasksDrawer(true)}
            title={language === 'zh' ? '查看后台任务中心' : 'Task Center'}
            style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
          >
            {activeUpgradeCount > 0 ? (
              <>
                <span className="spinner small" />
                <span>{language === 'zh' ? `任务 (${activeUpgradeCount})` : `Tasks (${activeUpgradeCount})`}</span>
              </>
            ) : (
              <>
                <span>📋</span>
                <span>{language === 'zh' ? '任务' : 'Tasks'}</span>
              </>
            )}
          </button>

          <button
            className="btn primary small"
            onClick={scanAll}
            disabled={busy || !!runId}
            title={language === 'zh' ? '扫描所有已纳管主机' : 'Scan all fleet machines'}
          >
            {runId ? t('nav.scanning') : `🔄 ${t('nav.scanAll')}`}
          </button>

          <button
            className="btn small"
            onClick={() => setShowSettings(true)}
            title={language === 'zh' ? '管理设置 (资产清单、定时、导出)' : 'Settings'}
            style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
          >
            <span>⚙️</span>
            <span>{language === 'zh' ? '设置' : 'Settings'}</span>
          </button>
        </div>
      </header>

      {toast && (
        <div className="container" style={{ paddingBottom: 0, paddingTop: 12 }}>
          <div className="err" style={{ borderColor: 'var(--accent)', background: 'rgba(76, 154, 255, 0.12)', color: 'var(--text)' }}>
            {toast}
          </div>
        </div>
      )}

      <main className="container">
        {tab === 'machines' && <MachinesView refreshSignal={refreshKey} />}
        {tab === 'agents' && <AgentsView refreshSignal={refreshKey} />}
        {tab === 'matrix' && <Matrix refreshSignal={refreshKey} />}
      </main>

      <SettingsModal
        isOpen={showSettings}
        onClose={() => setShowSettings(false)}
        onDataChanged={() => setRefreshKey((k) => k + 1)}
      />

      <TasksDrawer
        isOpen={showTasksDrawer}
        onClose={() => setShowTasksDrawer(false)}
        onDataChanged={() => setRefreshKey((k) => k + 1)}
      />
    </div>
  )
}
