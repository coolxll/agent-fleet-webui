/**
 * Agent marks.
 *
 * Hand-drawn geometric glyphs, one per agent, kept inline so the UI has no
 * external image requests and works offline. Each mark is distinct in shape
 * AND colour so the matrix and lists stay scannable.
 */

type Props = { agent: string; size?: number; title?: string }

const COLORS: Record<string, string> = {
  codex: '#10a37f',
  claude: '#d97757',
  opencode: '#7c8aff',
  pi: '#f0b429',
  hermes: '#a371f7',
  agy: '#4285f4',
  codebuddy: '#00b96f'
}

const LABELS: Record<string, string> = {
  codex: 'Codex CLI',
  claude: 'Claude Code',
  opencode: 'OpenCode',
  pi: 'Pi Coding Agent',
  hermes: 'Hermes Agent',
  agy: 'Antigravity (AGY)',
  codebuddy: 'CodeBuddy'
}

export function agentLabel(agent: string): string {
  return LABELS[agent] || agent
}

export function agentColor(agent: string): string {
  return COLORS[agent] || '#8b95a6'
}

function Codex() {
  // terminal window with a prompt
  return (
    <>
      <rect x="2.5" y="4" width="19" height="16" rx="2.5" fill="none" stroke="currentColor" strokeWidth="1.8" />
      <path d="M7 9.5l3 2.5-3 2.5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M12.5 14.5h4.5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </>
  )
}

function Claude() {
  // starburst
  const pts: string[] = []
  for (let i = 0; i < 12; i++) {
    const a = (i * Math.PI) / 6
    const r = i % 2 === 0 ? 9 : 4.2
    pts.push((12 + r * Math.cos(a)).toFixed(2) + ',' + (12 + r * Math.sin(a)).toFixed(2))
  }
  return <polygon points={pts.join(' ')} fill="currentColor" />
}

function OpenCode() {
  // split circle
  return (
    <>
      <circle cx="12" cy="12" r="8.5" fill="none" stroke="currentColor" strokeWidth="1.8" />
      <path d="M12 3.5a8.5 8.5 0 000 17z" fill="currentColor" />
    </>
  )
}

function Pi() {
  // pi letterform drawn with strokes
  return (
    <>
      <path d="M6 6.5v11" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
      <path d="M18 6.5v11" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
      <path d="M4.5 6.5h15" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
      <path d="M12 6.5c0 3.5-1.5 6-3.5 8.5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </>
  )
}

function Agy() {
  // orbital ring with a node: Antigravity's agent-in-the-loop mark
  return (
    <>
      <circle cx="12" cy="12" r="3.2" fill="currentColor" />
      <ellipse cx="12" cy="12" rx="9" ry="4.2" fill="none" stroke="currentColor" strokeWidth="1.7" transform="rotate(-28 12 12)" />
    </>
  )
}

function CodeBuddy() {
  // paired chevrons: a reviewer and a writer working on one file
  return (
    <>
      <path d="M4 8l5 4-5 4" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M11 8l5 4-5 4" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" opacity="0.55" />
    </>
  )
}

function Hermes() {
  // winged helm
  return (
    <>
      <path d="M4 19h16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
      <path d="M12 19V9" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
      <path d="M12 9c-2.2-2.6-4.8-3.6-7.4-3.2 1.1 2.7 3.4 4.3 6.2 4.6" fill="currentColor" />
      <path d="M12 9c2.2-2.6 4.8-3.6 7.4-3.2-1.1 2.7-3.4 4.3-6.2 4.6" fill="currentColor" opacity="0.55" />
    </>
  )
}

const SHAPES: Record<string, () => JSX.Element> = {
  codex: Codex,
  claude: Claude,
  opencode: OpenCode,
  pi: Pi,
  hermes: Hermes,
  agy: Agy,
  codebuddy: CodeBuddy
}

export default function AgentIcon({ agent, size = 16, title }: Props) {
  const Shape = SHAPES[agent]
  const color = agentColor(agent)
  return (
    <span
      className="agent-icon"
      title={title || agentLabel(agent)}
      style={{ width: size, height: size, color, display: 'inline-flex', verticalAlign: '-3px' }}
    >
      {Shape ? (
        <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true" focusable="false">
          <Shape />
        </svg>
      ) : (
        <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true">
          <circle cx="12" cy="12" r="8" fill="none" stroke="currentColor" strokeWidth="1.8" />
          <text x="12" y="16" textAnchor="middle" fontSize="10" fill="currentColor">
            ?
          </text>
        </svg>
      )}
    </span>
  )
}
