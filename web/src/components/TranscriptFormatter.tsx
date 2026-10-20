import { useState, useMemo } from 'react'
import AgentIcon from './AgentIcon'
import { useI18n } from '../i18n'
import ToolCallView, {
  ToolCallItemView,
  ToolResultView,
  ToolOutputBox,
  extractToolCommand,
  extractFilePath,
  extractWebTarget
} from './ToolCallView'

// Re-export helpers so existing usages are preserved
export {
  extractToolCommand,
  extractFilePath,
  extractWebTarget,
  ToolCallView,
  ToolCallItemView,
  ToolResultView,
  ToolOutputBox
}

// Markdown Code Card with Copy Button
export function CodeCard({ lang, code }: { lang: string; code: string }) {
  const [copied, setCopied] = useState(false)

  const handleCopy = () => {
    navigator.clipboard.writeText(code)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="transcript-code-card">
      <div className="transcript-code-header">
        <span className="transcript-code-lang">{lang || 'code'}</span>
        <button className="transcript-code-copy" onClick={handleCopy} title="Copy code">
          {copied ? '✓ Copied' : '📋 Copy'}
        </button>
      </div>
      <pre className="transcript-code-pre">
        <code>{code}</code>
      </pre>
    </div>
  )
}

// Inline Markdown & Paragraph Formatter
function ParagraphFormatter({ text }: { text: string }) {
  const lines = text.split('\n')
  return (
    <div className="transcript-paragraph-block">
      {lines.map((line, lIdx) => {
        // Detect bullet lines
        const isBullet = line.trimStart().startsWith('- ') || line.trimStart().startsWith('* ')
        const isHeader = line.startsWith('### ') || line.startsWith('## ') || line.startsWith('# ')

        return (
          <div
            key={lIdx}
            className={`transcript-text-line ${isBullet ? 'bullet' : ''} ${isHeader ? 'header' : ''}`}
          >
            {renderInlineMarkdown(line)}
          </div>
        )
      })}
    </div>
  )
}

// Render bold **text** and inline `code`
function renderInlineMarkdown(line: string) {
  const parts: (string | JSX.Element)[] = []
  const tokenRegex = /(`[^`]+`|\*\*[^*]+\*\*)/g
  let lastIndex = 0
  let match: RegExpExecArray | null

  while ((match = tokenRegex.exec(line)) !== null) {
    if (match.index > lastIndex) {
      parts.push(line.slice(lastIndex, match.index))
    }
    const token = match[0]
    if (token.startsWith('`') && token.endsWith('`')) {
      parts.push(
        <code key={match.index} className="transcript-inline-code">
          {token.slice(1, -1)}
        </code>
      )
    } else if (token.startsWith('**') && token.endsWith('**')) {
      parts.push(
        <strong key={match.index} className="transcript-inline-bold">
          {token.slice(2, -2)}
        </strong>
      )
    }
    lastIndex = match.index + token.length
  }
  if (lastIndex < line.length) {
    parts.push(line.slice(lastIndex))
  }
  return parts.length > 0 ? parts : line
}

// Clean Formatted Message Body
export function FormattedMessageContent({ content }: { content: string }) {
  if (!content) return null

  // Strip residual XML tags
  let text = content
    .replace(/<USER_REQUEST>\s*/g, '')
    .replace(/<\/USER_REQUEST>\s*/g, '')
    .replace(/<ADDITIONAL_METADATA>[\s\S]*?<\/ADDITIONAL_METADATA>\s*/g, '')
    .replace(/<context_summary>[\s\S]*?<\/context_summary>\s*/g, '')
    .replace(/<system-reminder[\s\S]*?<\/system-reminder>\s*/g, '')
    .replace(/<collaboration_mode>[\s\S]*?<\/collaboration_mode>\s*/g, '')
    .replace(/\[Note:\s*model was just switched from[\s\S]*?\]\s*/g, '')
    .replace(/<untrusted_tool_result[^>]*>[\s\S]*?<\/untrusted_tool_result>\s*/g, '')
    .trim()

  // Handle command tags: <command-name> or ⚡ Command:
  const isCommand = text.startsWith('⚡ Command:') || text.includes('<command-name>')
  if (isCommand) {
    const cleanCmd = text
      .replace(/<command-name>\s*(.*?)\s*<\/command-name>/g, '⚡ Command: $1')
      .replace(/<command-message>[\s\S]*?<\/command-message>/g, '')
      .replace(/<command-args>[\s\S]*?<\/command-args>/g, '')
      .trim()
    return (
      <div className="transcript-cmd-badge">
        <span className="transcript-cmd-tag">{cleanCmd}</span>
      </div>
    )
  }

  // Split into code blocks and normal markdown text
  const parts: { type: 'text' | 'code'; lang?: string; content: string }[] = []
  const codeBlockRegex = /```([a-zA-Z0-9_-]*)\n([\s\S]*?)```/g
  let lastIndex = 0
  let match: RegExpExecArray | null

  while ((match = codeBlockRegex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push({ type: 'text', content: text.slice(lastIndex, match.index) })
    }
    parts.push({ type: 'code', lang: match[1] || 'code', content: match[2].trimEnd() })
    lastIndex = match.index + match[0].length
  }
  if (lastIndex < text.length) {
    parts.push({ type: 'text', content: text.slice(lastIndex) })
  }

  return (
    <div className="transcript-content-body">
      {parts.map((p, idx) => {
        if (p.type === 'code') {
          return <CodeCard key={idx} lang={p.lang || 'code'} code={p.content} />
        }
        return <ParagraphFormatter key={idx} text={p.content} />
      })}
    </div>
  )
}

// Single Turn View
export function TranscriptTurnRow({
  turn,
  agentId,
  formatTime,
  pairedOutput
}: {
  turn: any
  agentId: string
  formatTime: (ts: any) => string
  pairedOutput?: string
}) {
  const { t } = useI18n()
  const role = turn.role || 'assistant'
  const isUser = role === 'user'
  const isTool = role === 'tool'

  if (isTool) {
    const hasCalls = turn.tool_calls && turn.tool_calls.length > 0
    const hasContent = !!turn.content || !!pairedOutput

    return (
      <div
        className={`transcript-message-row tool ${hasCalls ? 'has-call' : ''} ${
          hasContent ? 'has-output' : ''
        }`}
      >
        <div className="transcript-avatar tool-avatar">⚙️</div>
        <div className="transcript-bubble tool-bubble">
          <div className="transcript-bubble-meta">
            <span style={{ fontWeight: 600, fontSize: 12, color: 'var(--accent)' }}>
              Tool Execution
            </span>
            {turn.timestamp && (
              <span style={{ fontSize: 11, color: 'var(--muted)' }}>
                {formatTime(turn.timestamp)}
              </span>
            )}
          </div>

          {/* Render Tool Calls paired with execution output */}
          {hasCalls ? (
            turn.tool_calls.map((call: any, cIdx: number) => {
              // Pair output with the last tool call in this turn
              const callOutput =
                cIdx === turn.tool_calls.length - 1 ? (turn.content || pairedOutput) : null
              return <ToolCallView key={cIdx} call={call} output={callOutput} />
            })
          ) : hasContent ? (
            <ToolCallView call={null} output={turn.content || pairedOutput} />
          ) : null}
        </div>
      </div>
    )
  }

  return (
    <div className={`transcript-message-row ${isUser ? 'user' : 'assistant'}`}>
      <div className="transcript-avatar">
        {isUser ? '👤' : <AgentIcon agent={agentId} size={16} />}
      </div>

      <div className="transcript-bubble">
        <div className="transcript-bubble-meta">
          <span style={{ fontWeight: 600, fontSize: 12 }}>
            {isUser ? 'User' : agentId}
          </span>
          {turn.timestamp && (
            <span style={{ fontSize: 11, color: 'var(--muted)' }}>
              {formatTime(turn.timestamp)}
            </span>
          )}
        </div>

        {/* Collapsible Thinking Block */}
        {turn.thinking && (
          <details className="transcript-thinking-details">
            <summary>💭 {t('sessions.thinking')}</summary>
            <div className="transcript-thinking-content">{turn.thinking}</div>
          </details>
        )}

        {/* Tool Calls List (Modern Rich Cards) */}
        {turn.tool_calls && turn.tool_calls.length > 0 && (
          <div className="transcript-assistant-tools">
            <div className="transcript-tools-section-title">
              🛠️ {t('sessions.toolCall')} ({turn.tool_calls.length})
            </div>
            {turn.tool_calls.map((call: any, cIdx: number) => (
              <ToolCallView
                key={cIdx}
                call={call}
                output={cIdx === turn.tool_calls.length - 1 ? pairedOutput : null}
              />
            ))}
          </div>
        )}

        {/* Formatted Message Content */}
        {turn.content && <FormattedMessageContent content={turn.content} />}
      </div>
    </div>
  )
}

// Preprocessed Turn List that pairs adjacent Tool Calls & Outputs seamlessly
export function TranscriptTurnList({
  turns,
  agentId,
  formatTime
}: {
  turns: any[]
  agentId: string
  formatTime: (ts: any) => string
}) {
  const processed = useMemo(() => {
    if (!turns || turns.length === 0) return []
    const result: { turn: any; pairedOutput?: string }[] = []
    const skipIndices = new Set<number>()

    for (let i = 0; i < turns.length; i++) {
      if (skipIndices.has(i)) continue
      const curr = turns[i]
      const next = turns[i + 1]

      const currHasCalls = curr.tool_calls && curr.tool_calls.length > 0
      const currNoContent = !curr.content || curr.content.trim() === ''
      const nextIsToolOutput =
        next &&
        next.role === 'tool' &&
        next.content &&
        (!next.tool_calls || next.tool_calls.length === 0)

      if (currHasCalls && currNoContent && nextIsToolOutput) {
        result.push({ turn: curr, pairedOutput: next.content })
        skipIndices.add(i + 1)
        continue
      }

      result.push({ turn: curr })
    }
    return result
  }, [turns])

  return (
    <>
      {processed.map((item, idx) => (
        <TranscriptTurnRow
          key={idx}
          turn={item.turn}
          agentId={agentId}
          formatTime={formatTime}
          pairedOutput={item.pairedOutput}
        />
      ))}
    </>
  )
}
