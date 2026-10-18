import { useEffect, useState } from 'react'
import { api } from '../api'
import AgentIcon, { agentColor } from '../components/AgentIcon'

export default function MachinesView({ onOpenMachine }: { onOpenMachine: (id: string) => void }) {
  const [hosts, setHosts] = useState<any[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    const load = () =>
      api
        .hosts()
        .then((r) => alive && setHosts(r.hosts))
        .catch((e: any) => alive && setError(String(e.message || e)))
    load()
    const t = setInterval(load, 15000)
    return () => {
      alive = false
      clearInterval(t)
    }
  }, [])

  if (error) return <div className="err">{error}</div>
  if (!hosts.length) return <div className="muted">No machines yet — press Re-sync inventory.</div>

  return (
    <div className="machine-grid">
      {hosts.map((h) => {
        const rts = h.runtimes || []
        const online = rts.some((r: any) => r.reachable === true)
        const offline = rts.length > 0 && rts.every((r: any) => r.reachable === false)
        const agents: string[] = Array.from(
          new Set(rts.flatMap((r: any) => (r.agents as string[]) || []))
        )
        const total = rts.reduce((n: number, r: any) => n + (r.agents_installed || 0), 0)
        const stale = rts.some((r: any) => r.stale)
        return (
          <div className="machine-card" key={h.id} onClick={() => onOpenMachine(h.id)}>
            <div className="machine-card-head">
              <span className={'dot ' + (online ? 'on' : offline ? 'off' : 'unk')} />
              <span className="machine-name">{h.name}</span>
              {stale ? <span className="tag warn">stale</span> : null}
              {!h.enabled ? <span className="tag unknown">excluded</span> : null}
            </div>
            <div className="machine-os muted small">
              {rts.map((r: any) => r.os_name || r.kind).filter(Boolean).join(' · ') || h.hostname || '—'}
            </div>
            <div className="machine-agents">
              {agents.map((a: string) => (
                <span
                  key={a}
                  className="machine-agent-dot"
                  style={{ color: agentColor(a) }}
                  title={a}
                >
                  <AgentIcon agent={a} size={20} />
                </span>
              ))}
              {agents.length === 0 ? <span className="muted small">no agents</span> : null}
            </div>
            <div className="machine-foot muted small">
              {total} installation{total === 1 ? '' : 's'} · {rts.length} runtime
              {rts.length === 1 ? '' : 's'}
            </div>
          </div>
        )
      })}
    </div>
  )
}
