import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  api,
  SessionSummary,
  SessionTranscriptResponse
} from '../api'
import AgentIcon from '../components/AgentIcon'
import { TranscriptTurnRow } from '../components/TranscriptFormatter'
import { useI18n } from '../i18n'

export type ViewMode = 'timeline' | 'project' | 'machine' | 'agent'

export interface SessionGroup {
  id: string
  title: string
  subtitle?: string
  icon?: string
  items: SessionSummary[]
  hosts: string[]
  projects: string[]
  agents: string[]
  hasPinned: boolean
  latestUpdatedAt: number
}

export default function SessionsView({
  setToast
}: {
  setToast: (msg: string | null) => void
}) {
  const { t } = useI18n()

  const [sessions, setSessions] = useState<SessionSummary[]>([])
  const [total, setTotal] = useState(0)
  const [facets, setFacets] = useState<{
    agents: string[]
    hosts: string[]
    projects: string[]
  }>({ agents: [], hosts: [], projects: [] })

  const [loading, setLoading] = useState(false)
  const [syncing, setSyncing] = useState(false)

  // Filters
  const [searchQuery, setSearchQuery] = useState('')
  const [filterHost, setFilterHost] = useState('')
  const [filterAgent, setFilterAgent] = useState('')
  const [filterProject, setFilterProject] = useState('')
  const [filterPinned, setFilterPinned] = useState(false)

  // View mode: timeline | project | machine | agent
  const [viewMode, setViewMode] = useState<ViewMode>(() => {
    try {
      const saved = localStorage.getItem('fleet_session_view_mode')
      if (saved === 'timeline' || saved === 'project' || saved === 'machine' || saved === 'agent') {
        return saved
      }
    } catch {}
    return 'project' // 默认推荐按项目归类，极大提升找寻效率
  })

  const handleSelectViewMode = (mode: ViewMode) => {
    setViewMode(mode)
    try {
      localStorage.setItem('fleet_session_view_mode', mode)
    } catch {}
  }

  // Collapsible groups state
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>({})

  // Selected session & transcript
  const [selectedSession, setSelectedSession] = useState<SessionSummary | null>(null)
  const [transcript, setTranscript] = useState<SessionTranscriptResponse | null>(null)
  const [loadingTranscript, setLoadingTranscript] = useState(false)
  const [copied, setCopied] = useState(false)

  // Fetch session list (limit 2000 for instant and full multi-device aggregation)
  const loadSessions = useCallback(async () => {
    setLoading(true)
    try {
      const res = await api.listSessions({
        q: searchQuery || undefined,
        host_name: filterHost || undefined,
        agent_id: filterAgent || undefined,
        project: filterProject || undefined,
        pinned_only: filterPinned || undefined,
        limit: 2000
      })
      setSessions(res.items)
      setTotal(res.total)
      setFacets(res.facets)

      // Keep selection or pick first
      if (res.items.length > 0) {
        setSelectedSession((curr) => {
          if (!curr) return res.items[0]
          const match = res.items.find((s) => s.id === curr.id)
          return match || res.items[0]
        })
      } else {
        setSelectedSession(null)
      }
    } catch (e: any) {
      setToast(String(e.message || e))
    } finally {
      setLoading(false)
    }
  }, [searchQuery, filterHost, filterAgent, filterProject, filterPinned, setToast])

  useEffect(() => {
    loadSessions()
  }, [loadSessions])

  // Load transcript when selected session changes
  useEffect(() => {
    if (!selectedSession) {
      setTranscript(null)
      return
    }
    let alive = true
    setLoadingTranscript(true)
    api
      .getSessionTranscript(selectedSession.id)
      .then((res) => {
        if (!alive) return
        setTranscript(res)
      })
      .catch((e: any) => {
        if (!alive) return
        setToast(String(e.message || e))
      })
      .finally(() => {
        if (alive) setLoadingTranscript(false)
      })
    return () => {
      alive = false
    }
  }, [selectedSession, setToast])

  // Multi-dimensional Grouping
  const groups = useMemo<SessionGroup[]>(() => {
    if (viewMode === 'timeline') return []

    const map = new Map<string, SessionGroup>()

    for (const item of sessions) {
      let groupKey = ''
      let groupTitle = ''
      let groupSubtitle = ''
      let groupIcon = ''

      if (viewMode === 'project') {
        groupKey = item.project_name || 'workspace'
        groupTitle = item.project_name || t('sessions.noProjectName')
        groupSubtitle = item.project_path || ''
        groupIcon = '📁'
      } else if (viewMode === 'machine') {
        groupKey = item.host_name || 'unknown'
        groupTitle = item.host_name || 'unknown'
        groupSubtitle = ''
        groupIcon = '🖥️'
      } else if (viewMode === 'agent') {
        groupKey = item.agent_id || 'unknown'
        groupTitle = item.agent_id || 'unknown'
        groupSubtitle = ''
        groupIcon = '🤖'
      }

      let grp = map.get(groupKey)
      if (!grp) {
        grp = {
          id: groupKey,
          title: groupTitle,
          subtitle: groupSubtitle,
          icon: groupIcon,
          items: [],
          hosts: [],
          projects: [],
          agents: [],
          hasPinned: false,
          latestUpdatedAt: 0
        }
        map.set(groupKey, grp)
      }

      grp.items.push(item)
      if (item.host_name && !grp.hosts.includes(item.host_name)) {
        grp.hosts.push(item.host_name)
      }
      if (item.project_name && !grp.projects.includes(item.project_name)) {
        grp.projects.push(item.project_name)
      }
      if (item.agent_id && !grp.agents.includes(item.agent_id)) {
        grp.agents.push(item.agent_id)
      }
      if (item.is_pinned) {
        grp.hasPinned = true
      }
      const itemTs = item.updated_at ? new Date(item.updated_at).getTime() : 0
      if (itemTs > grp.latestUpdatedAt) {
        grp.latestUpdatedAt = itemTs
      }
    }

    // Sort groups: pinned first, then latest active timestamp desc
    return Array.from(map.values()).sort((a, b) => {
      if (a.hasPinned && !b.hasPinned) return -1
      if (!a.hasPinned && b.hasPinned) return 1
      return b.latestUpdatedAt - a.latestUpdatedAt
    })
  }, [sessions, viewMode, t])

  // Default expand first few groups
  useEffect(() => {
    if (viewMode === 'timeline') return
    setExpandedGroups((prev) => {
      const next = { ...prev }
      groups.forEach((g, idx) => {
        if (next[g.id] === undefined) {
          next[g.id] = idx < 5 // default expand top 5
        }
      })
      return next
    })
  }, [groups, viewMode])

  const toggleGroup = (groupId: string) => {
    setExpandedGroups((prev) => ({
      ...prev,
      [groupId]: !prev[groupId]
    }))
  }

  const expandAll = () => {
    const next: Record<string, boolean> = {}
    groups.forEach((g) => {
      next[g.id] = true
    })
    setExpandedGroups(next)
  }

  const collapseAll = () => {
    const next: Record<string, boolean> = {}
    groups.forEach((g) => {
      next[g.id] = false
    })
    setExpandedGroups(next)
  }

  // Sync sessions across fleet
  const handleSync = async () => {
    setSyncing(true)
    try {
      const res = await api.syncSessions()
      setToast(
        t('sessions.syncedToast', {
          total: res.total_sessions,
          runtimes: res.runtimes_scanned
        })
      )
      await loadSessions()
    } catch (e: any) {
      setToast(String(e.message || e))
    } finally {
      setSyncing(false)
    }
  }

  // Toggle pin
  const handleTogglePin = async (sid: string, e?: React.MouseEvent) => {
    if (e) e.stopPropagation()
    try {
      const res = await api.toggleSessionPin(sid)
      setSessions((prev) =>
        prev.map((s) => (s.id === sid ? { ...s, is_pinned: res.is_pinned } : s))
      )
      if (selectedSession && selectedSession.id === sid) {
        setSelectedSession({ ...selectedSession, is_pinned: res.is_pinned })
      }
    } catch (e: any) {
      setToast(String(e.message || e))
    }
  }

  // Copy resume command
  const handleCopyCommand = (cmd: string) => {
    navigator.clipboard.writeText(cmd)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
    setToast(t('sessions.copied'))
  }

  // Export transcript as Markdown
  const handleExportMarkdown = () => {
    if (!selectedSession || !transcript) return
    const lines: string[] = []
    lines.push(`# ${selectedSession.title}`)
    lines.push(`- **Agent:** ${selectedSession.agent_id}`)
    lines.push(`- **Machine:** ${selectedSession.host_name}`)
    lines.push(`- **Project:** ${selectedSession.project_path || selectedSession.project_name}`)
    lines.push(`- **Updated:** ${selectedSession.updated_at || 'unknown'}`)
    lines.push('')
    lines.push('---')
    lines.push('')

    for (const turn of transcript.turns) {
      const roleName = turn.role === 'user' ? 'User' : 'Assistant'
      lines.push(`### 💬 ${roleName}`)
      if (turn.thinking) {
        lines.push('```thinking')
        lines.push(turn.thinking)
        lines.push('```')
      }
      if (turn.tool_calls && turn.tool_calls.length > 0) {
        lines.push('```tools')
        lines.push(JSON.stringify(turn.tool_calls, null, 2))
        lines.push('```')
      }
      lines.push(turn.content)
      lines.push('')
    }

    const blob = new Blob([lines.join('\n')], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `session-${selectedSession.agent_id}-${selectedSession.session_key.slice(0, 8)}.md`
    a.click()
    URL.revokeObjectURL(url)
  }

  // Format relative time helper
  const formatTime = (iso: string | null) => {
    if (!iso) return ''
    try {
      const d = new Date(iso)
      return d.toLocaleDateString(undefined, {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
      })
    } catch {
      return iso
    }
  }

  // Switch view and focus
  const switchViewAndFilter = (mode: ViewMode, filterVal?: string) => {
    handleSelectViewMode(mode)
    if (filterVal) {
      if (mode === 'machine') setFilterHost(filterVal)
      if (mode === 'project') setFilterProject(filterVal)
      if (mode === 'agent') setFilterAgent(filterVal)
    }
  }

  // Render individual session card
  const renderSessionCard = (item: SessionSummary, inGroup = false) => {
    const isSelected = selectedSession?.id === item.id
    return (
      <div
        key={item.id}
        className={`session-card ${inGroup ? 'in-group' : ''} ${isSelected ? 'active' : ''}`}
        onClick={() => setSelectedSession(item)}
      >
        <div className="session-card-top">
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
            <AgentIcon agent={item.agent_id} size={15} />
            {/* Context-aware badges to minimize clutter */}
            {viewMode !== 'agent' && (
              <span className="session-badge agent">{item.agent_id}</span>
            )}
            {viewMode !== 'machine' && (
              <span className="session-badge host">{item.host_name}</span>
            )}
            {viewMode !== 'project' && item.project_name && (
              <span className="session-badge project" title={item.project_path || ''}>
                {item.project_name}
              </span>
            )}
          </div>

          <button
            className="session-star-btn"
            onClick={(e) => handleTogglePin(item.id, e)}
            title={item.is_pinned ? t('sessions.unpin') : t('sessions.pin')}
          >
            {item.is_pinned ? '⭐' : '☆'}
          </button>
        </div>

        <div className="session-card-title">{item.title}</div>

        <div className="session-card-footer">
          <span>{t('sessions.messagesCount', { count: item.message_count })}</span>
          <span>{formatTime(item.updated_at)}</span>
        </div>
      </div>
    )
  }

  const isSearching = searchQuery.trim().length > 0

  return (
    <div className="container" style={{ paddingBottom: 20 }}>
      {/* Top Filter and Action Bar */}
      <div className="sessions-topbar">
        <div className="sessions-search-group">
          <span style={{ fontSize: 16 }}>🔍</span>
          <input
            type="text"
            className="sessions-search-input"
            placeholder={t('sessions.searchPlaceholder')}
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
          {searchQuery && (
            <button className="sessions-clear-btn" onClick={() => setSearchQuery('')}>
              ✕
            </button>
          )}
        </div>

        <div className="sessions-filters-group">
          {/* Host filter */}
          <select
            className="session-select"
            value={filterHost}
            onChange={(e) => setFilterHost(e.target.value)}
          >
            <option value="">🖥️ {t('sessions.allHosts')}</option>
            {facets.hosts.map((h) => (
              <option key={h} value={h}>
                {h}
              </option>
            ))}
          </select>

          {/* Agent filter */}
          <select
            className="session-select"
            value={filterAgent}
            onChange={(e) => setFilterAgent(e.target.value)}
          >
            <option value="">🤖 {t('sessions.allAgents')}</option>
            {facets.agents.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>

          {/* Project filter */}
          <select
            className="session-select"
            value={filterProject}
            onChange={(e) => setFilterProject(e.target.value)}
          >
            <option value="">📁 {t('sessions.allProjects')}</option>
            {facets.projects.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>

          {/* Pinned filter toggle */}
          <button
            className={`btn ${filterPinned ? 'primary' : ''}`}
            onClick={() => setFilterPinned(!filterPinned)}
            title={t('sessions.pinnedOnly')}
          >
            ⭐ {t('sessions.pinnedOnly')}
          </button>

          {/* Sync Button */}
          <button
            className="btn primary"
            onClick={handleSync}
            disabled={syncing}
            style={{ marginLeft: 'auto' }}
          >
            {syncing ? `🔄 ${t('sessions.syncing')}` : `🔄 ${t('sessions.sync')}`}
          </button>
        </div>
      </div>

      {/* Main Split Layout */}
      <div className="sessions-split-layout">
        {/* Left: Master List */}
        <div className="sessions-master-panel">
          {/* Panel Header */}
          <div className="sessions-panel-header">
            <span style={{ fontWeight: 600, fontSize: 13, color: 'var(--text)' }}>
              {t('sessions.title')}
            </span>
            <span style={{ fontSize: 12, color: 'var(--muted)' }}>
              {viewMode === 'project'
                ? `${groups.length} ${t('sessions.projectsCount', { count: groups.length }).split(' ')[1] || 'projects'} · ${total}`
                : viewMode === 'machine'
                ? `${groups.length} ${t('sessions.machinesCount', { count: groups.length }).split(' ')[1] || 'machines'} · ${total}`
                : viewMode === 'agent'
                ? `${groups.length} ${t('sessions.agentsCount', { count: groups.length }).split(' ')[1] || 'agents'} · ${total}`
                : `${total} ${t('sessions.sessionsTotal', { count: total })}`}
            </span>
          </div>

          {/* View Mode Switcher */}
          <div className="sessions-mode-bar">
            <div className="sessions-segmented-control">
              <button
                className={`sessions-mode-tab ${viewMode === 'project' ? 'active' : ''}`}
                onClick={() => handleSelectViewMode('project')}
                title={t('sessions.viewMode.byProject')}
              >
                <span>📁</span>
                <span>{t('sessions.viewMode.byProject')}</span>
              </button>
              <button
                className={`sessions-mode-tab ${viewMode === 'machine' ? 'active' : ''}`}
                onClick={() => handleSelectViewMode('machine')}
                title={t('sessions.viewMode.byMachine')}
              >
                <span>🖥️</span>
                <span>{t('sessions.viewMode.byMachine')}</span>
              </button>
              <button
                className={`sessions-mode-tab ${viewMode === 'timeline' ? 'active' : ''}`}
                onClick={() => handleSelectViewMode('timeline')}
                title={t('sessions.viewMode.timeline')}
              >
                <span>🕒</span>
                <span>{t('sessions.viewMode.timeline')}</span>
              </button>
              <button
                className={`sessions-mode-tab ${viewMode === 'agent' ? 'active' : ''}`}
                onClick={() => handleSelectViewMode('agent')}
                title={t('sessions.viewMode.byAgent')}
              >
                <span>🤖</span>
                <span>{t('sessions.viewMode.byAgent')}</span>
              </button>
            </div>
          </div>

          {/* Group Expand/Collapse Actions (Only when in grouped modes) */}
          {viewMode !== 'timeline' && groups.length > 0 && (
            <div className="sessions-group-actions-bar">
              <span>
                {groups.length}{' '}
                {viewMode === 'project'
                  ? t('sessions.projectsCount', { count: groups.length })
                  : viewMode === 'machine'
                  ? t('sessions.machinesCount', { count: groups.length })
                  : t('sessions.agentsCount', { count: groups.length })}
              </span>
              <div style={{ display: 'flex', gap: 8 }}>
                <button className="sessions-text-btn" onClick={expandAll}>
                  ▾ {t('sessions.expandAll')}
                </button>
                <button className="sessions-text-btn" onClick={collapseAll}>
                  ▴ {t('sessions.collapseAll')}
                </button>
              </div>
            </div>
          )}

          {/* Scrollable Session List */}
          <div className="sessions-list-scroll">
            {loading && sessions.length === 0 ? (
              <div style={{ padding: 24, textAlign: 'center', color: 'var(--muted)' }}>
                {t('common.loading')}
              </div>
            ) : sessions.length === 0 ? (
              <div style={{ padding: 32, textAlign: 'center', color: 'var(--muted)' }}>
                {t('sessions.empty')}
              </div>
            ) : viewMode === 'timeline' ? (
              // Timeline flat view
              sessions.map((item) => renderSessionCard(item, false))
            ) : (
              // Grouped Accordion View (By Project / Machine / Agent)
              groups.map((group) => {
                const isExpanded = isSearching ? true : !!expandedGroups[group.id]

                return (
                  <div key={group.id} className="session-group-container">
                    {/* Collapsible Group Header */}
                    <div
                      className={`session-group-header ${group.hasPinned ? 'has-pinned' : ''}`}
                      onClick={() => toggleGroup(group.id)}
                      title={group.subtitle || group.title}
                    >
                      <div className="session-group-left">
                        <span className={`session-group-chevron ${isExpanded ? 'expanded' : ''}`}>
                          ▶
                        </span>
                        <span style={{ fontSize: 14 }}>{group.icon}</span>
                        <span className="session-group-title">{group.title}</span>
                        {group.hasPinned && <span title="Contains pinned session">⭐</span>}
                      </div>

                      <div className="session-group-right">
                        {/* If in project view, show distinct host badges */}
                        {viewMode === 'project' && group.hosts.length > 0 && (
                          <div style={{ display: 'flex', gap: 3 }}>
                            {group.hosts.slice(0, 2).map((h) => (
                              <span
                                key={h}
                                className="session-badge host"
                                style={{ fontSize: 10, padding: '1px 4px' }}
                                title={`Runs on ${h}`}
                              >
                                {h}
                              </span>
                            ))}
                            {group.hosts.length > 2 && (
                              <span
                                className="session-badge host"
                                style={{ fontSize: 10, padding: '1px 4px' }}
                                title={group.hosts.join(', ')}
                              >
                                +{group.hosts.length - 2}
                              </span>
                            )}
                          </div>
                        )}

                        {/* Agents mini icons */}
                        <div className="session-group-agents">
                          {group.agents.map((ag) => (
                            <AgentIcon key={ag} agent={ag} size={13} />
                          ))}
                        </div>

                        {/* Sessions count badge */}
                        <span className="session-group-badge-count">{group.items.length}</span>
                      </div>
                    </div>

                    {/* Group Items Body */}
                    {isExpanded && (
                      <div className="session-group-body">
                        {group.items.map((item) => renderSessionCard(item, true))}
                      </div>
                    )}
                  </div>
                )
              })
            )}
          </div>
        </div>

        {/* Right: Detail & Transcript Viewer */}
        <div className="sessions-detail-panel">
          {!selectedSession ? (
            <div className="sessions-empty-detail">
              <span style={{ fontSize: 36, opacity: 0.6 }}>💬</span>
              <p style={{ marginTop: 12, color: 'var(--muted)', fontSize: 14 }}>
                {t('sessions.selectPrompt')}
              </p>
            </div>
          ) : (
            <div className="session-viewer-wrapper">
              {/* Header Bar */}
              <div className="session-viewer-header">
                <div>
                  {/* Interactive Breadcrumb Bar */}
                  <div className="session-breadcrumb-bar">
                    <button
                      className="session-breadcrumb-btn"
                      onClick={() => switchViewAndFilter('machine', selectedSession.host_name)}
                      title={`Filter by machine: ${selectedSession.host_name}`}
                    >
                      🖥️ {selectedSession.host_name}
                    </button>
                    <span className="session-breadcrumb-sep">/</span>
                    <button
                      className="session-breadcrumb-btn"
                      onClick={() =>
                        switchViewAndFilter(
                          'project',
                          selectedSession.project_name || selectedSession.project_path || ''
                        )
                      }
                      title={`Filter by project: ${selectedSession.project_name}`}
                    >
                      📁 {selectedSession.project_name || 'workspace'}
                    </button>
                    <span className="session-breadcrumb-sep">/</span>
                    <button
                      className="session-breadcrumb-btn"
                      onClick={() => switchViewAndFilter('agent', selectedSession.agent_id)}
                      title={`Filter by agent: ${selectedSession.agent_id}`}
                    >
                      <AgentIcon agent={selectedSession.agent_id} size={14} />
                      <span>{selectedSession.agent_id}</span>
                    </button>
                  </div>

                  <h2 className="session-viewer-title">{selectedSession.title}</h2>
                </div>

                <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
                  <button
                    className="btn"
                    onClick={() => handleTogglePin(selectedSession.id)}
                    title={selectedSession.is_pinned ? t('sessions.unpin') : t('sessions.pin')}
                  >
                    {selectedSession.is_pinned
                      ? '⭐ ' + t('sessions.pinnedOnly')
                      : '☆ ' + t('sessions.pin')}
                  </button>

                  <button
                    className="btn"
                    onClick={handleExportMarkdown}
                    disabled={!transcript || transcript.turns.length === 0}
                    title={t('sessions.exportMarkdown')}
                  >
                    📥 {t('sessions.exportMarkdown')}
                  </button>
                </div>
              </div>

              {/* Terminal Resume Box */}
              <div className="session-resume-banner">
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                  <span style={{ fontSize: 14 }}>💻</span>
                  <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--accent)' }}>
                    {t('sessions.resumeCmd')}
                  </span>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <code className="session-resume-code">{selectedSession.resume_command}</code>
                  <button
                    className="btn"
                    onClick={() => handleCopyCommand(selectedSession.resume_command)}
                    style={{ flexShrink: 0 }}
                  >
                    {copied ? `✓ ${t('sessions.copied')}` : `📋 ${t('sessions.copyCmd')}`}
                  </button>
                </div>
              </div>

              {/* Privacy Shield Banner */}
              {transcript && transcript.has_secrets_masked && (
                <div className="session-privacy-banner">
                  <span>🔒</span>
                  <span>{t('sessions.maskedCount', { count: transcript.masked_count })}</span>
                </div>
              )}

              {/* Transcript Chat Flow */}
              <div className="session-transcript-flow">
                {loadingTranscript ? (
                  <div style={{ padding: 40, textAlign: 'center', color: 'var(--muted)' }}>
                    {t('common.loading')}
                  </div>
                ) : !transcript || transcript.turns.length === 0 ? (
                  <div style={{ padding: 40, textAlign: 'center', color: 'var(--muted)' }}>
                    {t('sessions.empty')}
                  </div>
                ) : (
                  transcript.turns.map((turn, idx) => (
                    <TranscriptTurnRow
                      key={idx}
                      turn={turn}
                      agentId={selectedSession.agent_id}
                      formatTime={formatTime}
                    />
                  ))
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
