import React, { useState, useMemo } from 'react'

export type ToolCategory =
  | 'command'
  | 'file_edit'
  | 'file_read'
  | 'web_search'
  | 'mcp'
  | 'generic'

export interface ToolOutputStatus {
  isError: boolean
  isSuccess: boolean
  code?: number | null
  label: string
}

// Extract shell command string
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
    if (input.command && typeof input.command === 'string') return input.command
    if (input.cmd && typeof input.cmd === 'string') return input.cmd
    if (input.code) {
      const mBash = String(input.code).match(/Bash\(\{\s*command:\s*(["'])([\s\S]*?)\1\s*\}\)/)
      if (mBash) {
        try {
          return JSON.parse(`"${mBash[2]}"`)
        } catch {
          return mBash[2]
        }
      }
      return typeof input.code === 'string' ? input.code : JSON.stringify(input.code)
    }
  }
  return null
}

// Extract file path info
export function extractFilePath(input: any): string | null {
  if (!input) return null
  if (typeof input === 'string') {
    return input.startsWith('/') || input.startsWith('./') || input.includes('/') ? input : null
  }
  if (typeof input === 'object') {
    return (
      input.AbsolutePath ||
      input.TargetFile ||
      input.file_path ||
      input.filePath ||
      input.path ||
      input.changes?.[0]?.path ||
      null
    )
  }
  return null
}

// Extract web URL or search query
export function extractWebTarget(input: any): { url?: string; query?: string; domain?: string } | null {
  if (!input) return null
  if (typeof input === 'string') {
    if (input.startsWith('http://') || input.startsWith('https://')) {
      return { url: input }
    }
    return null
  }
  if (typeof input === 'object') {
    const url = input.url || input.Url || input.uri || input.link
    const query = input.query || input.search_query || input.q
    const domain = input.domain || input.site
    if (url || query) {
      return { url, query, domain }
    }
  }
  return null
}

// Classify tool by name and payload
export function classifyTool(toolName: string, input: any): ToolCategory {
  const name = (toolName || '').toLowerCase()

  // 1. Command Execution
  if (
    [
      'bash',
      'run_command',
      'exec_command',
      'execute_command',
      'shell',
      'terminal',
      'commandexecution',
      'repl',
      'exec',
      'cmd',
      'powershell',
      'zsh',
      'sh'
    ].some((c) => name.includes(c)) ||
    (input && typeof input === 'object' && (input.CommandLine || (input.command && !name.includes('sql'))))
  ) {
    return 'command'
  }

  // 2. File Modifications & Diffs
  if (
    [
      'replace_file_content',
      'write_to_file',
      'edit_file',
      'file_edit',
      'edit',
      'patch',
      'filechange',
      'apply_diff',
      'create_file',
      'overwrite_file',
      'str_replace_editor',
      'insert_code'
    ].some((f) => name.includes(f)) ||
    (input &&
      typeof input === 'object' &&
      ((input.TargetContent !== undefined && input.ReplacementContent !== undefined) ||
        (input.old_string !== undefined && input.new_string !== undefined) ||
        (input.TargetFile && input.CodeContent !== undefined) ||
        input.patch !== undefined ||
        input.diff !== undefined))
  ) {
    return 'file_edit'
  }

  // 3. File Reading
  if (
    ['view_file', 'read_file', 'cat', 'filereadtool', 'read', 'open_file', 'readfile', 'head', 'tail'].some(
      (r) => name.includes(r)
    ) ||
    (input && typeof input === 'object' && input.AbsolutePath && !input.TargetContent && !input.CodeContent)
  ) {
    return 'file_read'
  }

  // 4. Web & Search
  if (
    [
      'search_web',
      'web_search',
      'read_url_content',
      'read_url',
      'fetch',
      'browser',
      'web',
      'google_search',
      'browse',
      'curl',
      'http_request'
    ].some((w) => name.includes(w)) ||
    (input && typeof input === 'object' && (input.query !== undefined || input.url !== undefined || input.Url !== undefined))
  ) {
    return 'web_search'
  }

  // 5. MCP Tools
  if (name.includes('mcp') || name.startsWith('call_mcp') || (input && input.ServerName && input.ToolName)) {
    return 'mcp'
  }

  // 6. Generic tools
  return 'generic'
}

// Parse exit code and success/error status from tool output string
export function parseToolOutputStatus(output?: string | null, explicitCode?: number | null): ToolOutputStatus {
  if (explicitCode !== undefined && explicitCode !== null) {
    if (explicitCode === 0) {
      return { isSuccess: true, isError: false, code: 0, label: 'Exit code 0 (Success)' }
    }
    return { isSuccess: false, isError: true, code: explicitCode, label: `Exit code ${explicitCode} (Error)` }
  }

  if (!output || typeof output !== 'string') {
    return { isSuccess: true, isError: false, code: null, label: 'Success' }
  }

  // Check exit code patterns
  const mExit = output.match(/(?:exited with code|exit code:?|returncode:?|code:?)\s*(\d+)/i)
  if (mExit) {
    const code = parseInt(mExit[1], 10)
    if (code === 0) {
      return { isSuccess: true, isError: false, code: 0, label: 'Exit code 0 (Success)' }
    }
    return { isSuccess: false, isError: true, code, label: `Exit code ${code} (Error)` }
  }

  // Check common error keywords
  const isErr =
    output.includes('Traceback (most recent call last):') ||
    output.includes('npm ERR!') ||
    output.includes('Command failed:') ||
    output.includes('command not found') ||
    output.includes('Permission denied') ||
    output.includes('FAILED') ||
    /\b(SyntaxError|TypeError|ValueError|AssertionError|ReferenceError)\b/.test(output)

  if (isErr) {
    return { isSuccess: false, isError: true, code: null, label: 'Error' }
  }

  return { isSuccess: true, isError: false, code: null, label: 'Success' }
}

// Copy Button Component
export function CopyButton({
  text,
  label = 'Copy',
  copiedLabel = 'Copied!',
  title,
  className = ''
}: {
  text: string
  label?: string
  copiedLabel?: string
  title?: string
  className?: string
}) {
  const [copied, setCopied] = useState(false)

  const handleCopy = (e: React.MouseEvent) => {
    e.stopPropagation()
    navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <button
      className={`tool-copy-btn ${copied ? 'copied' : ''} ${className}`}
      onClick={handleCopy}
      title={title || label}
      type="button"
    >
      {copied ? `✓ ${copiedLabel}` : `📋 ${label}`}
    </button>
  )
}

// Status Badge Component
export function ToolStatusBadge({ status }: { status: ToolOutputStatus }) {
  if (status.isError) {
    return <span className="tool-status-badge error">✕ {status.label}</span>
  }
  if (status.isSuccess) {
    return <span className="tool-status-badge success">✓ {status.label}</span>
  }
  return <span className="tool-status-badge info">⚡ {status.label}</span>
}

// Tool Output Box with collapsible line preview & copy
export function ToolOutputBox({
  output,
  status,
  defaultExpanded = false
}: {
  output: string
  status?: ToolOutputStatus
  defaultExpanded?: boolean
}) {
  const [expanded, setExpanded] = useState(defaultExpanded)
  const lines = useMemo(() => (output || '').split('\n'), [output])
  const totalLines = lines.length
  const PREVIEW_LIMIT = 8
  const isLong = totalLines > PREVIEW_LIMIT

  const detectedStatus = useMemo(
    () => status || parseToolOutputStatus(output),
    [status, output]
  )

  const displayContent = useMemo(() => {
    if (!isLong || expanded) return output
    return lines.slice(0, PREVIEW_LIMIT).join('\n')
  }, [isLong, expanded, output, lines])

  return (
    <div className="tool-output-box">
      <div className="tool-output-header">
        <div className="tool-output-header-left">
          <ToolStatusBadge status={detectedStatus} />
          <span className="tool-output-lines-count">
            {totalLines} {totalLines === 1 ? 'line' : 'lines'}
            {isLong && !expanded && ` (showing ${PREVIEW_LIMIT})`}
          </span>
        </div>
        <div className="tool-output-header-right">
          {isLong && (
            <button
              className="tool-expand-btn"
              onClick={() => setExpanded(!expanded)}
              type="button"
            >
              {expanded ? '▲ Collapse' : `▼ Show all ${totalLines} lines`}
            </button>
          )}
          <CopyButton text={output} label="Copy Output" />
        </div>
      </div>

      <div className="tool-output-pre-wrapper">
        <pre className="tool-output-pre">{displayContent}</pre>
        {isLong && !expanded && <div className="tool-output-fade" />}
      </div>
    </div>
  )
}

// Highlight bash syntax helper
function renderHighlightedCommand(cmd: string) {
  // Regex token matching binaries, flags, strings, operators
  const tokens: (string | JSX.Element)[] = []
  const tokenRegex = /(["'][^"']*["']|--[a-zA-Z0-9_-]+|-[a-zA-Z0-9]+|\|\||&&|\||;|>|>>|\b(?:git|npm|pnpm|yarn|cargo|docker|python|python3|pytest|node|cat|ls|grep|find|curl|chmod|cp|mv|rm|mkdir|echo|export|cd|sed|awk)\b)/g
  let lastIdx = 0
  let match: RegExpExecArray | null

  while ((match = tokenRegex.exec(cmd)) !== null) {
    if (match.index > lastIdx) {
      tokens.push(cmd.slice(lastIdx, match.index))
    }
    const val = match[0]
    if (val.startsWith('"') || val.startsWith("'")) {
      tokens.push(
        <span key={match.index} className="cmd-token-str">
          {val}
        </span>
      )
    } else if (val.startsWith('-')) {
      tokens.push(
        <span key={match.index} className="cmd-token-flag">
          {val}
        </span>
      )
    } else if (['&&', '||', '|', ';', '>', '>>'].includes(val)) {
      tokens.push(
        <span key={match.index} className="cmd-token-op">
          {val}
        </span>
      )
    } else {
      tokens.push(
        <span key={match.index} className="cmd-token-bin">
          {val}
        </span>
      )
    }
    lastIdx = match.index + val.length
  }

  if (lastIdx < cmd.length) {
    tokens.push(cmd.slice(lastIdx))
  }

  return tokens.length > 0 ? tokens : cmd
}

// 1. Command Execution Card
export function CommandExecutionCard({
  call,
  output
}: {
  call: any
  output?: string | null
}) {
  const cmd = extractToolCommand(call?.input) || ''
  const cwd = call?.input?.Cwd || call?.input?.cwd || call?.input?.dir || null
  const persistent = call?.input?.RunPersistent
  const toolName = call?.name || 'run_command'
  const status = output ? parseToolOutputStatus(output) : undefined

  return (
    <div className="tool-unified-card tool-cmd-card">
      <div className="tool-card-header">
        <div className="tool-header-left">
          <span className="tool-type-icon">💻</span>
          <span className="tool-name-badge">{toolName}</span>
          {cwd && (
            <span className="tool-cwd-badge" title={`Working directory: ${cwd}`}>
              📁 {String(cwd).replace(/^\/Users\/[^/]+/, '~')}
            </span>
          )}
          {persistent && <span className="tool-badge-pill">persistent</span>}
        </div>
        <div className="tool-header-right">
          <CopyButton text={cmd} label="Copy command" />
        </div>
      </div>

      <div className="tool-cmd-body">
        <span className="tool-cmd-prompt">$</span>
        <code className="tool-cmd-code">{renderHighlightedCommand(cmd)}</code>
      </div>

      {output && <ToolOutputBox output={output} status={status} />}
    </div>
  )
}

// 2. File Modifications & Diffs Card
export function FileEditCard({
  call,
  output
}: {
  call: any
  output?: string | null
}) {
  const [viewMode, setViewMode] = useState<'unified' | 'split'>('unified')
  const input = call?.input || {}
  const filePath = extractFilePath(input) || 'unknown file'
  const toolName = call?.name || 'edit_file'

  // Extract Diff inputs
  const targetContent = input.TargetContent ?? input.old_string ?? input.old_content ?? null
  const replacementContent = input.ReplacementContent ?? input.new_string ?? input.new_content ?? null
  const codeContent = input.CodeContent ?? input.content ?? null
  const patchContent = input.patch ?? input.diff ?? null

  const startLine = input.StartLine ?? input.start_line
  const endLine = input.EndLine ?? input.end_line
  const isOverwrite = input.Overwrite === true
  const isAppend = input.Append === true
  const instruction = input.Instruction || input.Description || input.description || input.toolSummary

  // Split path for readability
  const slashIdx = filePath.lastIndexOf('/')
  const dirPart = slashIdx !== -1 ? filePath.slice(0, slashIdx + 1) : ''
  const baseName = slashIdx !== -1 ? filePath.slice(slashIdx + 1) : filePath

  // Calculate lines for stats
  const targetLines = useMemo(
    () => (targetContent ? targetContent.split('\n') : []),
    [targetContent]
  )
  const replacementLines = useMemo(
    () => (replacementContent ? replacementContent.split('\n') : []),
    [replacementContent]
  )

  const hasBeforeAfter = targetContent !== null && replacementContent !== null

  return (
    <div className="tool-unified-card tool-file-card">
      {/* File Header */}
      <div className="tool-card-header">
        <div className="tool-header-left">
          <span className="tool-type-icon">📝</span>
          <span className="tool-name-badge">{toolName}</span>
          <span
            className={`tool-action-badge ${
              hasBeforeAfter ? 'edit' : isOverwrite ? 'overwrite' : isAppend ? 'append' : 'create'
            }`}
          >
            {hasBeforeAfter ? 'Edit' : isOverwrite ? 'Overwrite' : isAppend ? 'Append' : 'Write'}
          </span>
          {startLine && endLine && (
            <span className="tool-range-badge">
              L{startLine}-L{endLine}
            </span>
          )}
          {hasBeforeAfter && (
            <div className="diff-stat-badge">
              <span className="diff-stat-del">-{targetLines.length}</span>
              <span className="diff-stat-add">+{replacementLines.length}</span>
            </div>
          )}
        </div>
        <div className="tool-header-right">
          {hasBeforeAfter && (
            <div className="diff-toggle-group">
              <button
                className={`diff-toggle-btn ${viewMode === 'unified' ? 'active' : ''}`}
                onClick={() => setViewMode('unified')}
                type="button"
              >
                Unified
              </button>
              <button
                className={`diff-toggle-btn ${viewMode === 'split' ? 'active' : ''}`}
                onClick={() => setViewMode('split')}
                type="button"
              >
                Split
              </button>
            </div>
          )}
          <CopyButton text={filePath} label="Copy Path" />
        </div>
      </div>

      {/* Prominent File Path Bar */}
      <div className="tool-file-path-bar">
        <span className="tool-file-icon">📄</span>
        <span className="tool-file-path-text" title={filePath}>
          <span className="tool-file-dir">{dirPart}</span>
          <strong className="tool-file-base">{baseName}</strong>
        </span>
      </div>

      {/* Instruction Pill */}
      {instruction && (
        <div className="tool-card-instruction">
          <span className="tool-instruction-icon">💬</span>
          <span className="tool-instruction-text">{instruction}</span>
        </div>
      )}

      {/* Diff / Code Body */}
      {hasBeforeAfter ? (
        viewMode === 'unified' ? (
          <div className="tool-diff-container">
            {targetLines.map((line: string, idx: number) => (
              <div key={`del-${idx}`} className="diff-line diff-del">
                <span className="diff-lineno">
                  {startLine ? startLine + idx : ''}
                </span>
                <span className="diff-gutter">-</span>
                <span className="diff-text">{line}</span>
              </div>
            ))}
            {replacementLines.map((line: string, idx: number) => (
              <div key={`add-${idx}`} className="diff-line diff-add">
                <span className="diff-lineno">
                  {startLine ? startLine + idx : ''}
                </span>
                <span className="diff-gutter">+</span>
                <span className="diff-text">{line}</span>
              </div>
            ))}
          </div>
        ) : (
          <div className="split-diff-grid">
            <div className="split-diff-col">
              <div className="split-diff-col-header del">
                Original (-{targetLines.length} lines)
              </div>
              <div className="tool-diff-container">
                {targetLines.map((line: string, idx: number) => (
                  <div key={idx} className="diff-line diff-del">
                    <span className="diff-gutter">-</span>
                    <span className="diff-text">{line}</span>
                  </div>
                ))}
              </div>
            </div>
            <div className="split-diff-col">
              <div className="split-diff-col-header add">
                Replacement (+{replacementLines.length} lines)
              </div>
              <div className="tool-diff-container">
                {replacementLines.map((line: string, idx: number) => (
                  <div key={idx} className="diff-line diff-add">
                    <span className="diff-gutter">+</span>
                    <span className="diff-text">{line}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )
      ) : patchContent ? (
        <div className="tool-diff-container">
          {patchContent.split('\n').map((line: string, idx: number) => {
            const isAdd = line.startsWith('+') && !line.startsWith('+++')
            const isDel = line.startsWith('-') && !line.startsWith('---')
            const isHunk = line.startsWith('@@')
            return (
              <div
                key={idx}
                className={`diff-line ${isAdd ? 'diff-add' : isDel ? 'diff-del' : isHunk ? 'diff-hunk' : 'diff-ctx'}`}
              >
                <span className="diff-gutter">{isAdd ? '+' : isDel ? '-' : ' '}</span>
                <span className="diff-text">{line}</span>
              </div>
            )
          })}
        </div>
      ) : codeContent ? (
        <div className="tool-code-preview-block">
          <div className="tool-code-preview-header">
            <span>+{codeContent.split('\n').length} lines content</span>
            <CopyButton text={codeContent} label="Copy Code" />
          </div>
          <pre className="tool-code-pre">{codeContent}</pre>
        </div>
      ) : null}

      {/* Paired Output */}
      {output && <ToolOutputBox output={output} />}
    </div>
  )
}

// 3. File Reading Card
export function FileReadCard({
  call,
  output
}: {
  call: any
  output?: string | null
}) {
  const input = call?.input || {}
  const filePath = extractFilePath(input) || 'unknown file'
  const toolName = call?.name || 'view_file'
  const startLine = input.StartLine ?? input.start_line ?? input.offset
  const endLine = input.EndLine ?? input.end_line ?? input.limit
  const summary = input.toolAction || input.toolSummary || input.description

  const slashIdx = filePath.lastIndexOf('/')
  const dirPart = slashIdx !== -1 ? filePath.slice(0, slashIdx + 1) : ''
  const baseName = slashIdx !== -1 ? filePath.slice(slashIdx + 1) : filePath

  return (
    <div className="tool-unified-card tool-read-card">
      <div className="tool-card-header">
        <div className="tool-header-left">
          <span className="tool-type-icon">📖</span>
          <span className="tool-name-badge">{toolName}</span>
          {startLine !== undefined && endLine !== undefined ? (
            <span className="tool-range-badge">
              L{startLine}-L{endLine}
            </span>
          ) : startLine !== undefined ? (
            <span className="tool-range-badge">From L{startLine}</span>
          ) : null}
          {summary && <span className="tool-summary-tag">{summary}</span>}
        </div>
        <div className="tool-header-right">
          <CopyButton text={filePath} label="Copy Path" />
        </div>
      </div>

      <div className="tool-file-path-bar">
        <span className="tool-file-icon">📄</span>
        <span className="tool-file-path-text" title={filePath}>
          <span className="tool-file-dir">{dirPart}</span>
          <strong className="tool-file-base">{baseName}</strong>
        </span>
      </div>

      {output && <ToolOutputBox output={output} defaultExpanded={false} />}
    </div>
  )
}

// 4. Web & Search Card
export function WebSearchCard({
  call,
  output
}: {
  call: any
  output?: string | null
}) {
  const input = call?.input || {}
  const webTarget = extractWebTarget(input)
  const toolName = call?.name || 'web_search'
  const summary = input.toolAction || input.toolSummary

  return (
    <div className="tool-unified-card tool-web-card">
      <div className="tool-card-header">
        <div className="tool-header-left">
          <span className="tool-type-icon">🌐</span>
          <span className="tool-name-badge">{toolName}</span>
          {summary && <span className="tool-summary-tag">{summary}</span>}
        </div>
        <div className="tool-header-right">
          {webTarget?.url && <CopyButton text={webTarget.url} label="Copy URL" />}
          {webTarget?.query && !webTarget.url && (
            <CopyButton text={webTarget.query} label="Copy Query" />
          )}
        </div>
      </div>

      {webTarget?.query && (
        <div className="tool-search-box">
          <span className="tool-search-icon">🔍</span>
          <span className="tool-search-query">{webTarget.query}</span>
          {webTarget.domain && (
            <span className="tool-search-domain">site:{webTarget.domain}</span>
          )}
        </div>
      )}

      {webTarget?.url && (
        <div className="tool-url-box">
          <span className="tool-url-icon">🔗</span>
          <a
            href={webTarget.url}
            target="_blank"
            rel="noopener noreferrer"
            className="tool-url-link"
          >
            {webTarget.url}
          </a>
        </div>
      )}

      {output && <ToolOutputBox output={output} />}
    </div>
  )
}

// 5. MCP Tool Card
export function McpToolCard({
  call,
  output
}: {
  call: any
  output?: string | null
}) {
  const input = call?.input || {}
  const serverName = input.ServerName || input.server_name || input.server || 'mcp'
  const toolName = input.ToolName || input.tool_name || input.tool || call?.name || 'tool'
  const args = input.Arguments || input.arguments || input.args || {}

  return (
    <div className="tool-unified-card tool-mcp-card">
      <div className="tool-card-header">
        <div className="tool-header-left">
          <span className="tool-type-icon">🔌</span>
          <span className="tool-name-badge">MCP</span>
          <span className="tool-mcp-server-badge">{serverName}</span>
          <span className="tool-mcp-tool-badge">→ {toolName}</span>
        </div>
      </div>

      {/* Render parameters as key-value pill cards */}
      <GenericParamPills input={args} />

      {output && <ToolOutputBox output={output} />}
    </div>
  )
}

// Key-Value Pill renderer for parameters
export function GenericParamPills({ input }: { input: any }) {
  if (!input || (typeof input === 'object' && Object.keys(input).length === 0)) {
    return null
  }

  if (typeof input !== 'object') {
    return (
      <div className="tool-params-grid">
        <div className="tool-param-pill">
          <span className="tool-param-key">input:</span>
          <span className="tool-param-val">{String(input)}</span>
        </div>
      </div>
    )
  }

  const entries = Object.entries(input).filter(([k]) => {
    // Hide large duplicated metadata fields if rendered elsewhere
    return !['toolAction', 'toolSummary'].includes(k)
  })

  if (entries.length === 0) return null

  return (
    <div className="tool-params-grid">
      {entries.map(([key, value]) => {
        const valType = typeof value
        if (value === null || value === undefined) {
          return (
            <div key={key} className="tool-param-pill">
              <span className="tool-param-key">{key}:</span>
              <span className="tool-param-val null">null</span>
            </div>
          )
        }
        if (valType === 'boolean') {
          return (
            <div key={key} className="tool-param-pill">
              <span className="tool-param-key">{key}:</span>
              <span className={`tool-param-val boolean ${value ? 'true' : 'false'}`}>
                {value ? 'true' : 'false'}
              </span>
            </div>
          )
        }
        if (valType === 'number') {
          return (
            <div key={key} className="tool-param-pill">
              <span className="tool-param-key">{key}:</span>
              <span className="tool-param-val number">{String(value)}</span>
            </div>
          )
        }
        if (valType === 'string') {
          const str = String(value)
          if (str.length <= 80 && !str.includes('\n')) {
            return (
              <div key={key} className="tool-param-pill">
                <span className="tool-param-key">{key}:</span>
                <span className="tool-param-val string">{str}</span>
              </div>
            )
          }
          return (
            <div key={key} className="tool-param-pill long">
              <span className="tool-param-key">{key}:</span>
              <div className="tool-param-long-box">
                <pre className="tool-param-pre">{str}</pre>
              </div>
            </div>
          )
        }
        if (Array.isArray(value)) {
          if (value.length === 0) {
            return (
              <div key={key} className="tool-param-pill">
                <span className="tool-param-key">{key}:</span>
                <span className="tool-param-val">[]</span>
              </div>
            )
          }
          if (value.every((v) => typeof v === 'string' || typeof v === 'number')) {
            return (
              <div key={key} className="tool-param-pill">
                <span className="tool-param-key">{key}:</span>
                <span className="tool-param-val">[{value.join(', ')}]</span>
              </div>
            )
          }
        }
        // Complex object fallback
        return (
          <div key={key} className="tool-param-pill long">
            <span className="tool-param-key">{key}:</span>
            <div className="tool-param-long-box">
              <pre className="tool-param-pre">{JSON.stringify(value, null, 2)}</pre>
            </div>
          </div>
        )
      })}
    </div>
  )
}

// 6. Generic Tool Card
export function GenericToolCard({
  call,
  output
}: {
  call: any
  output?: string | null
}) {
  const name = call?.name || 'tool'
  const summary = call?.input?.toolSummary || call?.input?.toolAction || call?.input?.description

  return (
    <div className="tool-unified-card tool-generic-card">
      <div className="tool-card-header">
        <div className="tool-header-left">
          <span className="tool-type-icon">🛠️</span>
          <span className="tool-name-badge">{name}</span>
          {summary && <span className="tool-summary-tag">{summary}</span>}
        </div>
      </div>

      <GenericParamPills input={call?.input} />

      {output && <ToolOutputBox output={output} />}
    </div>
  )
}

// Master Unified Tool Call & Output View
export function ToolCallView({
  call,
  output
}: {
  call: any
  output?: string | null
}) {
  // If call is missing and only output is provided (orphan output)
  if (!call && output) {
    return (
      <div className="tool-unified-card tool-standalone-output">
        <ToolOutputBox output={output} />
      </div>
    )
  }

  const category = classifyTool(call?.name || '', call?.input)

  switch (category) {
    case 'command':
      return <CommandExecutionCard call={call} output={output} />
    case 'file_edit':
      return <FileEditCard call={call} output={output} />
    case 'file_read':
      return <FileReadCard call={call} output={output} />
    case 'web_search':
      return <WebSearchCard call={call} output={output} />
    case 'mcp':
      return <McpToolCard call={call} output={output} />
    case 'generic':
    default:
      return <GenericToolCard call={call} output={output} />
  }
}

// Backward-compatible alias
export function ToolCallItemView({ call }: { call: any }) {
  return <ToolCallView call={call} output={null} />
}

// Backward-compatible alias
export function ToolResultView({ content }: { content: string }) {
  return <ToolOutputBox output={content} />
}

export default ToolCallView
