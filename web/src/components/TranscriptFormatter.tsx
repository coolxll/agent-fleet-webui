import { useState } from 'react'
import AgentIcon from './AgentIcon'
import { useI18n } from '../i18n'

// Extract command string from various tool call input schemas
export function extractToolCommand(input: any): string | null {
  if (!input) return null
  if (typeof input === 'string') {
    const mBash = input.match(/Bash\(\{\s*command:\s*(["'])([\s\S]*?)\1\s*\}\)/)
    if (mBash) {
      try {
        return JSON.parse(`"${mBash[2]}"`)
      } catch {
        return mBash[2]
      }
    }
    return input
  }
  if (typeof input === 'object') {
    if (input.CommandLine) {
      const c = input.CommandLine
      return typeof c === 'string' && c.startsWith('"') && c.endsWith('"') ? c.slice(1, -1) : c
    }
    if (input.command) return input.command
    if (input.code) {
      const mBash = String(input.code).match(/Bash\(\{\s*command:\s*(["'])([\s\S]*?)\1\s*\}\)/)
      if (mBash) {
        try {
          return JSON.parse(`"${mBash[2]}"`)
        } catch {
          return mBash[2]
        }
      }
      return input.code
    }
  }
  return null
}

// Extract file path from various tool call input schemas
export function extractFilePath(input: any): string | null {
  if (!input || typeof input !== 'object') return null
  return (
    input.AbsolutePath ||
    input.TargetFile ||
    input.file_path ||
    input.path ||
    input.changes?.[0]?.path ||
    null
  )
}

// Extract web URL or search query
export function extractWebTarget(input: any): { url?: string; query?: string } | null {
  if (!input || typeof input !== 'object') return null
  if (input.url) return { url: input.url }
  if (input.query) return { query: input.query }
  return null
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

// Tool Call Item View
export function ToolCallItemView({ call }: { call: any }) {
  const [copied, setCopied] = useState(false)
  const name = call.name || 'tool'
  const isCmd = [
    'bash',
    'run_command',
    'exec_command',
    'shell',
    'terminal',
    'commandexecution',
    'repl'
  ].some((c) => name.toLowerCase().includes(c))
  const isFile = ['file', 'read', 'view', 'write', 'edit', 'patch'].some((f) =>
    name.toLowerCase().includes(f)
  )
  const isWeb = ['browser', 'web', 'fetch', 'url', 'search'].some((w) =>
    name.toLowerCase().includes(w)
  )

  const cmd = extractToolCommand(call.input)
  const filePath = extractFilePath(call.input)
  const webTarget = extractWebTarget(call.input)

  const handleCopy = (str: string) => {
    navigator.clipboard.writeText(str)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="transcript-tool-item">
      <div className="transcript-tool-header">
        <span className="transcript-tool-badge">
          {isCmd ? '💻' : isFile ? '📄' : isWeb ? '🌐' : '🛠️'} {name}
        </span>
        {cmd && (
          <button
            className="transcript-tool-copy"
            onClick={() => handleCopy(cmd)}
            title="Copy command"
          >
            {copied ? '✓ Copied' : '📋 Copy'}
          </button>
        )}
      </div>

      {isCmd && cmd ? (
        <div className="transcript-tool-cmd-box">
          <span className="transcript-tool-prompt">$</span>
          <pre className="transcript-tool-cmd-text">{cmd}</pre>
        </div>
      ) : isFile && filePath ? (
        <div className="transcript-tool-file-box">
          <span className="transcript-tool-file-icon">📄</span>
          <code className="transcript-tool-file-path">{filePath}</code>
        </div>
      ) : isWeb && webTarget ? (
        <div className="transcript-tool-web-box">
          {webTarget.url && <div className="transcript-web-line">🔗 {webTarget.url}</div>}
          {webTarget.query && (
            <div className="transcript-web-line">
              🔍 <code>{webTarget.query}</code>
            </div>
          )}
        </div>
      ) : (
        <pre className="transcript-tool-raw-args">
          {typeof call.input === 'string'
            ? call.input
            : JSON.stringify(call.input, null, 2)}
        </pre>
      )}
    </div>
  )
}

// Tool Result / Output Box
export function ToolResultView({ content }: { content: string }) {
  const [copied, setCopied] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const lines = content.split('\n')
  const isLong = lines.length > 8

  const handleCopy = () => {
    navigator.clipboard.writeText(content)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const displayContent = isLong && !expanded ? lines.slice(0, 8).join('\n') + '\n...' : content

  return (
    <div className="transcript-tool-result">
      <div className="transcript-tool-result-header">
        <span className="transcript-tool-result-label">
          ⚡ Output ({lines.length} lines)
        </span>
        <div style={{ display: 'flex', gap: 6 }}>
          {isLong && (
            <button
              className="transcript-tool-toggle"
              onClick={() => setExpanded(!expanded)}
            >
              {expanded ? '▲ Collapse' : '▼ Expand'}
            </button>
          )}
          <button className="transcript-tool-copy" onClick={handleCopy}>
            {copied ? '✓ Copied' : '📋 Copy'}
          </button>
        </div>
      </div>
      <pre className="transcript-tool-result-body">{displayContent}</pre>
    </div>
  )
}

// Single Turn View
export function TranscriptTurnRow({
  turn,
  agentId,
  formatTime
}: {
  turn: any
  agentId: string
  formatTime: (ts: any) => string
}) {
  const { t } = useI18n()
  const role = turn.role || 'assistant'
  const isUser = role === 'user'
  const isTool = role === 'tool'

  if (isTool) {
    return (
      <div className="transcript-message-row tool">
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

          {turn.tool_calls && turn.tool_calls.map((call: any, cIdx: number) => (
            <ToolCallItemView key={cIdx} call={call} />
          ))}

          {turn.content && <ToolResultView content={turn.content} />}
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

        {/* Tool Calls List */}
        {turn.tool_calls && turn.tool_calls.length > 0 && (
          <details className="transcript-tools-details">
            <summary>
              🛠️ {t('sessions.toolCall')} ({turn.tool_calls.length})
            </summary>
            <div className="transcript-tools-content">
              {turn.tool_calls.map((call: any, cIdx: number) => (
                <ToolCallItemView key={cIdx} call={call} />
              ))}
            </div>
          </details>
        )}

        {/* Formatted Message Content */}
        {turn.content && <FormattedMessageContent content={turn.content} />}
      </div>
    </div>
  )
}
