import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { api, type UpgradeTask } from '../api'
import { useI18n } from '../i18n'

export type UpgradeTargetMeta = {
  agent: string
  host?: string | null
  currentVersion?: string | null
  latestVersion?: string | null
  runtimeKey?: string | null
  packageName?: string | null
  releaseUrl?: string | null
  installationId?: string | null
  runtimeId?: string | null
}

type UpgradesContextType = {
  tasks: UpgradeTask[]
  activeTasks: UpgradeTask[]
  activeCount: number
  isUpgrading: (instId?: string | null) => boolean
  triggerUpgrade: (instId: string, meta?: UpgradeTargetMeta) => Promise<UpgradeTask | void>
  tasksDrawerOpen: boolean
  setTasksDrawerOpen: (open: boolean) => void
  openTasksDrawer: () => void
  refreshSignal: number
  triggerRefresh: () => void
}

const UpgradesContext = createContext<UpgradesContextType | null>(null)

export function UpgradesProvider({
  children,
  onToast
}: {
  children: React.ReactNode
  onToast?: (msg: string) => void
}) {
  const { language } = useI18n()
  const [tasks, setTasks] = useState<UpgradeTask[]>([])
  const [optimisticIds, setOptimisticIds] = useState<Set<string>>(new Set())
  const [tasksDrawerOpen, setTasksDrawerOpen] = useState(false)
  const [refreshSignal, setRefreshSignal] = useState(0)
  const prevActiveCountRef = useRef(0)

  const activeTasks = tasks.filter((u) => u.status === 'running' || u.status === 'pending')
  const activeCount = activeTasks.length + Array.from(optimisticIds).filter(
    (id) => !activeTasks.some((t) => t.installation_id === id)
  ).length

  const triggerRefresh = useCallback(() => {
    setRefreshSignal((s) => s + 1)
  }, [])

  const openTasksDrawer = useCallback(() => {
    setTasksDrawerOpen(true)
  }, [])

  const checkTasks = useCallback(async () => {
    try {
      const res = await api.listUpgradeTasks()
      const newTasks = res.tasks || []
      setTasks(newTasks)

      // Prune optimistic IDs that are now accounted for
      setOptimisticIds((prev) => {
        if (prev.size === 0) return prev
        const next = new Set(prev)
        for (const task of newTasks) {
          if (next.has(task.installation_id)) {
            next.delete(task.installation_id)
          }
        }
        return next
      })

      const currentActive = newTasks.filter(
        (u) => u.status === 'running' || u.status === 'pending'
      )

      if (prevActiveCountRef.current > 0 && currentActive.length < prevActiveCountRef.current) {
        // One or more tasks just finished!
        triggerRefresh()
        const justFinished = newTasks.find(
          (u) => u.status === 'completed' && u.ok
        )
        if (justFinished && onToast) {
          onToast(
            language === 'zh'
              ? `🎉 ${justFinished.agent} (${justFinished.host}) 升级成功！`
              : `🎉 Upgrade succeeded for ${justFinished.agent} (${justFinished.host})!`
          )
        }
      }
      prevActiveCountRef.current = currentActive.length
    } catch {
      // Ignore background fetch error
    }
  }, [triggerRefresh, onToast, language])

  useEffect(() => {
    checkTasks()
    const timer = setInterval(checkTasks, 2500)
    return () => clearInterval(timer)
  }, [checkTasks])

  const isUpgrading = useCallback(
    (instId?: string | null): boolean => {
      if (!instId) return false
      if (optimisticIds.has(instId)) return true
      return activeTasks.some((t) => t.installation_id === instId)
    },
    [optimisticIds, activeTasks]
  )

  const triggerUpgrade = useCallback(
    async (instId: string, meta?: UpgradeTargetMeta) => {
      if (isUpgrading(instId)) {
        openTasksDrawer()
        return
      }

      // Mark optimistically
      setOptimisticIds((prev) => new Set(prev).add(instId))

      const agentName = meta?.agent || 'Agent'
      const hostName = meta?.host ? ` (${meta.host})` : ''
      if (onToast) {
        onToast(
          language === 'zh'
            ? `⚡ 已开始后台升级 ${agentName}${hostName}，可随时在右上角任务中心查看`
            : `⚡ Upgrade started in background for ${agentName}${hostName}`
        )
      }

      try {
        const task = await api.upgradeInstallation(instId)
        // Immediately refresh tasks
        checkTasks()
        return task
      } catch (e: any) {
        setOptimisticIds((prev) => {
          const next = new Set(prev)
          next.delete(instId)
          return next
        })
        const errText = String(e?.message || e)
        if (onToast) {
          onToast(
            language === 'zh'
              ? `❌ 启动升级失败: ${errText}`
              : `❌ Failed to start upgrade: ${errText}`
          )
        }
      }
    },
    [isUpgrading, openTasksDrawer, onToast, language, checkTasks]
  )

  return (
    <UpgradesContext.Provider
      value={{
        tasks,
        activeTasks,
        activeCount,
        isUpgrading,
        triggerUpgrade,
        tasksDrawerOpen,
        setTasksDrawerOpen,
        openTasksDrawer,
        refreshSignal,
        triggerRefresh
      }}
    >
      {children}
    </UpgradesContext.Provider>
  )
}

export function useUpgrades() {
  const ctx = useContext(UpgradesContext)
  if (!ctx) {
    throw new Error('useUpgrades must be used within an UpgradesProvider')
  }
  return ctx
}
