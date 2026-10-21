/**
 * Agent marks.
 *
 * Hand-drawn geometric glyphs, one per agent, kept inline so the UI has no
 * external image requests and works offline. Each mark is distinct in shape
 * AND colour so the matrix and lists stay scannable.
 */

import { useState } from 'react'
import { getFaviconCandidates } from '../utils/favicon'

type Props = { agent: string; size?: number; title?: string }

const COLORS: Record<string, string> = {
  codex: '#10a37f',
  claude: '#d97757',
  opencode: '#7c8aff',
  pi: '#f0b429',
  hermes: '#a371f7',
  agy: '#4285f4',
  codebuddy: '#00b96f',
  workbuddy: '#00b96f',
  trae: '#32d6a0',
  qoder: '#2adb5c',
  qodercn: '#2adb5c',
  herdr: '#60a5fa',
  paseo: '#a855f7'
}

const LABELS: Record<string, string> = {
  codex: 'Codex CLI',
  claude: 'Claude Code',
  opencode: 'OpenCode',
  pi: 'Pi Coding Agent',
  hermes: 'Hermes Agent',
  agy: 'Antigravity (AGY)',
  codebuddy: 'CodeBuddy',
  workbuddy: 'WorkBuddy',
  trae: 'Trae CN',
  qoder: 'Qoder',
  qodercn: 'QoderCN',
  herdr: 'Herdr',
  paseo: 'Paseo'
}

const AGENT_OFFICIAL_URLS: Record<string, string> = {
  codex: 'https://openai.com',
  claude: 'https://claude.ai',
  opencode: 'https://opencode.ai',
  pi: 'https://pi.ai',
  hermes: 'https://nousresearch.com',
  agy: 'https://deepmind.google',
  codebuddy: 'https://codebuddy.cn',
  workbuddy: 'https://codebuddy.cn',
  trae: 'https://www.trae.cn',
  qoder: 'https://qoder.com.cn',
  qodercn: 'https://qoder.com.cn',
  herdr: 'https://herdr.dev',
  paseo: 'https://paseo.sh'
}

const AGENT_LOCAL_ICONS: Record<string, string> = {
  codex: '/icons/codex.png',
  claude: '/icons/claude.png',
  opencode: '/icons/opencode.png',
  pi: '/icons/pi.png',
  hermes: '/icons/hermes.png',
  agy: '/icons/agy.png',
  codebuddy: '/icons/codebuddy.svg',
  workbuddy: '/icons/codebuddy.svg',
  qoder: '/icons/qoder.svg',
  qodercn: '/icons/qoder.svg',
  herdr: '/icons/herdr.svg',
  paseo: '/icons/paseo.svg'
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

function Trae() {
  return (
    <>
      <path d="M4 5h16v4H14v11h-4V9H4z" fill="currentColor" />
      <path d="M17 12h3v8h-3z" fill="currentColor" opacity="0.5" />
    </>
  )
}

function Qoder() {
  // shield with code brackets
  return (
    <>
      <path d="M12 3l7.5 3.5v6.5c0 4.8-3.3 8.3-7.5 9.8-4.2-1.5-7.5-5-7.5-9.8V6.5L12 3z" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" />
      <path d="M9.5 10.5l-2 1.5 2 1.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M14.5 10.5l2 1.5-2 1.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M13 9.5l-2 5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </>
  )
}

function Herdr() {
  return (
    <>
      <rect x="3" y="4" width="18" height="16" rx="2" fill="none" stroke="currentColor" strokeWidth="1.8" />
      <path d="M7 9l3 3-3 3" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M13 15h4" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
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
  codebuddy: CodeBuddy,
  workbuddy: CodeBuddy,
  trae: Trae,
  qoder: Qoder,
  qodercn: Qoder,
  herdr: Herdr
}

export default function AgentIcon({ agent, size = 16, title }: Props) {
  const Shape = SHAPES[agent]
  const color = agentColor(agent)

  const officialUrl = AGENT_OFFICIAL_URLS[agent]
  const localIcon = AGENT_LOCAL_ICONS[agent]
  const candidates = officialUrl || localIcon ? getFaviconCandidates(officialUrl || '', localIcon) : []

  const [candidateIndex, setCandidateIndex] = useState(0)
  const [imgFailed, setImgFailed] = useState(false)

  const handleImgError = () => {
    if (candidateIndex + 1 < candidates.length) {
      setCandidateIndex((i) => i + 1)
    } else {
      setImgFailed(true)
    }
  }

  const currentIconUrl = !imgFailed && candidateIndex < candidates.length ? candidates[candidateIndex] : null

  return (
    <span
      className="agent-icon"
      title={title || agentLabel(agent)}
      style={{
        width: size,
        height: size,
        color,
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        verticalAlign: '-3px'
      }}
    >
      {currentIconUrl ? (
        <img
          key={currentIconUrl}
          src={currentIconUrl}
          alt={agentLabel(agent)}
          width={size}
          height={size}
          onError={handleImgError}
          style={{
            width: size,
            height: size,
            objectFit: 'contain',
            borderRadius: 3,
            display: 'block'
          }}
        />
      ) : Shape ? (
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
