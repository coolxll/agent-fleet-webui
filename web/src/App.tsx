import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import MachinesView from './views/MachinesView'
import AgentsView from './views/AgentsView'
import Matrix from './views/Matrix'
import SessionsView from './views/SessionsView'
import QuotasView from './views/QuotasView'
import SettingsModal from './components/SettingsModal'
import TasksDrawer from './components/TasksDrawer'
import { useI18n } from './i18n'
import { UpgradesProvider, useUpgrades } from './context/UpgradesContext'

type ViewTab = 'machines' | 'agents' | 'quotas' | 'matrix' | 'sessions'

function AppShell({
  toast,
  setToast
}: {
  toast: string | null
  setToast: (msg: string | null) => void
}) {
  const { t, language } = useI18n()
  const {
    activeCount,
    tasksDrawerOpen,
    setTasksDrawerOpen,
    openTasksDrawer,
    refreshSignal,
    triggerRefresh
  } = useUpgrades()

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
  const [showSettings, setShowSettings] = useState(false)

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
          triggerRefresh()
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
  }, [runId, t, triggerRefresh, setToast])

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
  }, [t, setToast])

  useEffect(() => {
    if (!toast) return
    const timer = setTimeout(() => setToast(null), 4000)
    return () => clearTimeout(timer)
  }, [toast, setToast])

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
              className={tab === 'quotas' ? 'active' : ''}
              onClick={() => handleTabSwitch('quotas')}
            >
              🪙 {t('nav.quotas')}
            </button>
            <button
              className={tab === 'matrix' ? 'active' : ''}
              onClick={() => handleTabSwitch('matrix')}
            >
              🎛️ {t('nav.matrix')}
            </button>
            <button
              className={tab === 'sessions' ? 'active' : ''}
              onClick={() => handleTabSwitch('sessions')}
            >
              💬 {t('nav.sessions')}
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
            className={'btn small ' + (activeCount > 0 ? 'primary ' : '')}
            onClick={openTasksDrawer}
            title={language === 'zh' ? '查看后台任务中心' : 'Task Center'}
            style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
          >
            {activeCount > 0 ? (
              <>
                <span className="spinner small" />
                <span>{language === 'zh' ? `任务 (${activeCount})` : `Tasks (${activeCount})`}</span>
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
        {tab === 'machines' && <MachinesView refreshSignal={refreshSignal} />}
        {tab === 'agents' && <AgentsView refreshSignal={refreshSignal} />}
        {tab === 'quotas' && <QuotasView refreshSignal={refreshSignal} />}
        {tab === 'matrix' && <Matrix refreshSignal={refreshSignal} />}
        {tab === 'sessions' && <SessionsView setToast={setToast} />}
      </main>

      <SettingsModal
        isOpen={showSettings}
        onClose={() => setShowSettings(false)}
        onDataChanged={triggerRefresh}
      />

      <TasksDrawer
        isOpen={tasksDrawerOpen}
        onClose={() => setTasksDrawerOpen(false)}
        onDataChanged={triggerRefresh}
      />
    </div>
  )
}

export default function App() {
  const [toast, setToast] = useState<string | null>(null)

  const showToast = useCallback((msg: string) => {
    setToast(msg)
  }, [])

  return (
    <UpgradesProvider onToast={showToast}>
      <AppShell toast={toast} setToast={setToast} />
    </UpgradesProvider>
  )
}

