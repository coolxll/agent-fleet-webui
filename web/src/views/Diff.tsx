import { useEffect, useState } from 'react'
import { api, type CompareCandidate, type DiffChange } from '../api'
import AgentIcon, { agentLabel } from '../components/AgentIcon'
import { useI18n } from '../i18n'

type Mode = 'machines' | 'snapshots'

export default function Diff() {
  const { t } = useI18n()
  const [agents, setAgents] = useState<{ id: string; name: string }[]>([])
  const [runtimes, setRuntimes] = useState<any[]>([])
  const [mode, setMode] = useState<Mode>('machines')

  // machine mode state
  const [agentId, setAgentId] = useState('')
  const [candidates, setCandidates] = useState<CompareCandidate[]>([])
  const [left, setLeft] = useState('')
  const [right, setRight] = useState('')
  const [instDiff, setInstDiff] = useState<any>(null)

  // snapshot mode state
  const [runtimeId, setRuntimeId] = useState('')
  const [snapshots, setSnapshots] = useState<any[]>([])
  const [snapA, setSnapA] = useState('')
  const [snapB, setSnapB] = useState('')
  const [snapDiff, setSnapDiff] = useState<any>(null)

  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    Promise.all([api.agents(), api.runtimes()]).then(([a, r]) => {
      setAgents(a.agents)
      setRuntimes(r.runtimes)
      if (a.agents.length) setAgentId(a.agents[0].id)
      if (r.runtimes.length) setRuntimeId(r.runtimes[0].id)
    })
  }, [])

  useEffect(() => {
    if (!agentId) return
    api
      .compare(agentId)
      .then((r) => {
        setCandidates(r.candidates)
        setLeft('')
        setRight('')
        setInstDiff(null)
      })
      .catch((e: any) => setError(String(e.message || e)))
  }, [agentId])

  useEffect(() => {
    if (!runtimeId) return
    api
      .snapshots(runtimeId)
      .then((r) => {
        setSnapshots(r.snapshots)
        setSnapA('')
        setSnapB('')
        setSnapDiff(null)
      })
      .catch((e: any) => setError(String(e.message || e)))
  }, [runtimeId])

  const compareMachines = async () => {
    if (!left || !right) return
    setError(null)
    try {
      setInstDiff(await api.diffInstallations(left, right))
    } catch (e: any) {
      setError(String(e.message || e))
    }
  }

  const compareSnaps = async () => {
    if (!snapA || !snapB) return
    setError(null)
    try {
      setSnapDiff(await api.diffSnapshots(snapA, snapB))
    } catch (e: any) {
      setError(String(e.message || e))
    }
  }

  return (
    <div>
      <div className="panel">
        <h2>{t('diff.title')}</h2>
        <div className="body filters">
          <div className="tabs" style={{ marginLeft: 0 }}>
            <button className={mode === 'machines' ? 'active' : ''} onClick={() => setMode('machines')}>
              {t('diff.tabMachines')}
            </button>
            <button
              className={mode === 'snapshots' ? 'active' : ''}
              onClick={() => setMode('snapshots')}
            >
              {t('diff.tabSnapshots')}
            </button>
          </div>
        </div>
      </div>

      {error ? <div className="err">{error}</div> : null}

      {mode === 'machines' ? (
        <>
          <div className="panel">
            <h2>{t('diff.agent')}</h2>
            <div className="body filters">
              <select value={agentId} onChange={(e) => setAgentId(e.target.value)}>
                {agents.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </select>
              <span className="muted small agent-cell">
                <AgentIcon agent={agentId} size={17} />
                {t('diff.installationsOf', { count: candidates.length, agent: agentLabel(agentId) })}
              </span>
            </div>
          </div>

          <div className="panel">
            <h2>{t('diff.pickTwo')}</h2>
            <div className="body filters">
              <select value={left} onChange={(e) => setLeft(e.target.value)}>
                <option value="">{t('diff.machineAPlaceholder')}</option>
                {candidates.map((c) => (
                  <option key={c.installation_id} value={c.installation_id}>
                    {c.host}/{c.runtime_key} ({c.kind}) v{c.version || '?'}
                  </option>
                ))}
              </select>
              <select value={right} onChange={(e) => setRight(e.target.value)}>
                <option value="">{t('diff.machineBPlaceholder')}</option>
                {candidates.map((c) => (
                  <option key={c.installation_id} value={c.installation_id}>
                    {c.host}/{c.runtime_key} ({c.kind}) v{c.version || '?'}
                  </option>
                ))}
              </select>
              <button
                className="btn primary"
                disabled={!left || !right || left === right}
                onClick={compareMachines}
              >
                {t('diff.compareBtn')}
              </button>
            </div>
          </div>

          {instDiff ? <ChangesTable diff={instDiff} /> : null}
        </>
      ) : (
        <>
          <div className="panel">
            <h2>{t('diff.runtime')}</h2>
            <div className="body filters">
              <select value={runtimeId} onChange={(e) => setRuntimeId(e.target.value)}>
                {runtimes.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.host}/{r.key} ({r.kind})
                  </option>
                ))}
              </select>
              <span className="muted small">{t('diff.snapshotsCount', { count: snapshots.length })}</span>
            </div>
          </div>

          <div className="panel">
            <h2>{t('diff.pickTwoSnaps')}</h2>
            <div className="body filters">
              <select value={snapA} onChange={(e) => setSnapA(e.target.value)}>
                <option value="">{t('diff.snapAPlaceholder')}</option>
                {snapshots.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.created_at} ({s.agents_found} agents)
                  </option>
                ))}
              </select>
              <select value={snapB} onChange={(e) => setSnapB(e.target.value)}>
                <option value="">{t('diff.snapBPlaceholder')}</option>
                {snapshots.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.created_at} ({s.agents_found} agents)
                  </option>
                ))}
              </select>
              <button
                className="btn primary"
                disabled={!snapA || !snapB || snapA === snapB}
                onClick={compareSnaps}
              >
                {t('diff.compareBtn')}
              </button>
            </div>
          </div>

          {snapDiff ? (
            snapDiff.identical ? (
              <div className="panel">
                <div className="body muted small">{t('diff.noDiffSnapshots')}</div>
              </div>
            ) : (
              Object.entries(snapDiff.per_agent).map(([agent, changes]) => (
                <div className="panel" key={agent}>
                  <h2>
                    <span className="agent-cell">
                      <AgentIcon agent={agent} size={17} />
                      {agentLabel(agent)}
                    </span>
                    <span className="tag">{t('diff.changesCount', { count: (changes as DiffChange[]).length })}</span>
                  </h2>
                  <div className="body">
                    <Changes changes={changes as DiffChange[]} />
                  </div>
                </div>
              ))
            )
          ) : null}
        </>
      )}
    </div>
  )
}

function ChangesTable({ diff }: { diff: any }) {
  const { t } = useI18n()
  if (diff.identical) {
    return (
      <div className="panel">
        <div className="body muted small">
          {t('diff.identicallyConfigured', {
            hostA: diff.a.runtime.host,
            keyA: diff.a.runtime.key,
            hostB: diff.b.runtime.host,
            keyB: diff.b.runtime.key
          })}
        </div>
      </div>
    )
  }
  const label = (side: any) =>
    side.runtime
      ? side.runtime.host + '/' + side.runtime.key + (side.runtime.collect_user ? ' @ ' + side.runtime.collect_user : '')
      : side.id
  return (
    <div className="panel">
      <h2>
        {t('diff.differences')}
        <span className="tag">{t('diff.changesCount', { count: diff.changes.length })}</span>
        {!diff.same_agent ? <span className="tag warn">{t('diff.differentAgents')}</span> : null}
      </h2>
      <div className="body">
        <table>
          <thead>
            <tr>
              <th>{t('diff.field')}</th>
              <th>A: {label(diff.a)}</th>
              <th>B: {label(diff.b)}</th>
            </tr>
          </thead>
          <tbody>
            {diff.changes.map((c: DiffChange, i: number) => (
              <tr key={i}>
                <td className="mono small">{c.path}</td>
                <td className="mono small">{fmt(c.a, c.kind === 'added', t)}</td>
                <td className="mono small">{fmt(c.b, c.kind === 'removed', t)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function Changes({ changes }: { changes: DiffChange[] }) {
  const { t } = useI18n()
  return (
    <table>
      <thead>
        <tr>
          <th>{t('diff.field')}</th>
          <th>{t('diff.old')}</th>
          <th>{t('diff.new')}</th>
        </tr>
      </thead>
      <tbody>
        {changes.map((c, i) => (
          <tr key={i}>
            <td className="mono small">{c.path}</td>
            <td className="mono small">{fmt(c.a, c.kind === 'added', t)}</td>
            <td className="mono small">{fmt(c.b, c.kind === 'removed', t)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function fmt(value: any, absent: boolean, t: (key: any) => string) {
  if (absent) return <span className="muted">{t('diff.notPresent')}</span>
  if (value === null || value === undefined) return <span className="muted">null</span>
  return String(value)
}
