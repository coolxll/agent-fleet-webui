/**
 * XML deconstructor, thinking extractor, and agent harness wrapper stripper.
 *
 * Frontend defensive utility for parsing and cleaning transcripts across
 * Claude Code, Codex, Antigravity / Gemini, OpenCode, Qoder, CodeBuddy, Pi, and Hermes.
 */

export interface DeconstructedToolCall {
  name: string
  input: Record<string, unknown> | unknown[] | string | number | boolean | null
}

export interface DeconstructedMessage {
  content: string
  thinking: string | null
  tool_calls: DeconstructedToolCall[]
}

// --------------------------------------------------------------------------
// 1. Model Thinking Extraction
// --------------------------------------------------------------------------

const THINKING_TAGS = ['thinking', 'thought', 'antthinking', 'reasoning', 'scratchpad']
const THINKING_REGEX = new RegExp(
  `<(?:${THINKING_TAGS.join('|')})(?:\\s+[^>]*)?>([\\s\\S]*?)<\\/(?:${THINKING_TAGS.join('|')})>`,
  'gi',
)
const UNCLOSED_THINKING_REGEX = new RegExp(
  `<(?:${THINKING_TAGS.join('|')})(?:\\s+[^>]*)?>([\\s\\S]*)$`,
  'i',
)

/**
 * Extract model thinking/reasoning blocks from text.
 *
 * Detects <thinking>, <thought>, <antthinking>, <reasoning>, <scratchpad> tags.
 * Returns [cleanText, thinkingContent | null].
 */
export function extractThinkingFromText(text: string): [string, string | null] {
  if (!text) {
    return ['', null]
  }

  const thinkingBlocks: string[] = []

  // Replace closed thinking blocks
  let cleaned = text.replace(THINKING_REGEX, (_match, content: string) => {
    const trimmed = content.trim()
    if (trimmed) {
      thinkingBlocks.push(trimmed)
    }
    return ''
  })

  // Check for unclosed thinking block at the end
  const unclosed = UNCLOSED_THINKING_REGEX.exec(cleaned)
  if (unclosed) {
    const trimmed = unclosed[1].trim()
    if (trimmed) {
      thinkingBlocks.push(trimmed)
    }
    cleaned = cleaned.slice(0, unclosed.index)
  }

  cleaned = cleaned.replace(/\n{3,}/g, '\n\n').trim()
  const thinking = thinkingBlocks.length > 0 ? thinkingBlocks.join('\n\n') : null
  return [cleaned, thinking]
}

export const extract_thinking_from_text = extractThinkingFromText

// --------------------------------------------------------------------------
// 2. Model XML Tool Call Deconstruction
// --------------------------------------------------------------------------

function parseValue(val: string): unknown {
  const trimmed = val.trim()
  const lower = trimmed.toLowerCase()
  if (lower === 'true') return true
  if (lower === 'false') return false
  if (lower === 'null' || lower === 'none') return null

  // Integer
  if (/^-?\d+$/.test(trimmed)) {
    const num = Number.parseInt(trimmed, 10)
    if (!Number.isNaN(num)) return num
  }

  // Float
  if (/^-?\d+\.\d+$/.test(trimmed)) {
    const num = Number.parseFloat(trimmed)
    if (!Number.isNaN(num)) return num
  }

  // JSON object or array
  if (
    (trimmed.startsWith('{') && trimmed.endsWith('}')) ||
    (trimmed.startsWith('[') && trimmed.endsWith(']'))
  ) {
    try {
      return JSON.parse(trimmed)
    } catch {
      // fallback to string
    }
  }

  return trimmed
}

function parseXmlChildTags(innerText: string): Record<string, unknown> | null {
  const tagRegex = /<([a-zA-Z0-9_\-]+)(?:\s+[^>]*)?>([\s\S]*?)<\/\1>/g
  const matches = [...innerText.matchAll(tagRegex)]
  if (matches.length === 0) return null

  const result: Record<string, unknown> = {}
  for (const match of matches) {
    const key = match[1].trim()
    const value = match[2]
    const nested = parseXmlChildTags(value)
    if (nested) {
      result[key] = nested
    } else {
      result[key] = parseValue(value)
    }
  }
  return result
}

function parseToolArguments(innerText: string): unknown {
  const trimmed = innerText.trim()
  if (!trimmed) return {}

  // 1. Parameter / arg tags
  const paramRegex = /<(?:parameter|param|arg)\s+name=["']([^"']+)["'][^>]*>([\s\S]*?)<\/(?:parameter|param|arg)>/gi
  const paramMatches = [...trimmed.matchAll(paramRegex)]
  if (paramMatches.length > 0) {
    const params: Record<string, unknown> = {}
    for (const m of paramMatches) {
      params[m[1].trim()] = parseValue(m[2])
    }
    return params
  }

  // 2. Arguments wrapper
  const argsMatch = /<arguments(?:\s+[^>]*)?>([\s\S]*?)<\/arguments>/i.exec(trimmed)
  if (argsMatch) {
    const argsText = argsMatch[1].trim()
    try {
      return JSON.parse(argsText)
    } catch {
      const parsed = parseXmlChildTags(argsText)
      if (parsed) return parsed
      return argsText
    }
  }

  // 3. Direct JSON string
  if (
    (trimmed.startsWith('{') && trimmed.endsWith('}')) ||
    (trimmed.startsWith('[') && trimmed.endsWith(']'))
  ) {
    try {
      return JSON.parse(trimmed)
    } catch {
      // continue
    }
  }

  // 4. XML key-value children
  const parsedChildren = parseXmlChildTags(trimmed)
  if (parsedChildren) {
    return parsedChildren
  }

  // 5. Raw text
  return trimmed
}

function parseOpenCodeEntries(entriesContent: string): unknown {
  const entryRegex = /<entry(?:\s+[^>]*)?>([\s\S]*?)<\/entry>/gi
  const matches = [...entriesContent.matchAll(entryRegex)]
  if (matches.length > 0) {
    return matches.map((m) => {
      const c = m[1].trim()
      const cDict = parseXmlChildTags(c)
      return cDict || parseValue(c)
    })
  }

  const lines = entriesContent
    .trim()
    .split('\n')
    .map((l) => l.trim())
    .filter(Boolean)
  return lines.length > 0 ? lines : entriesContent.trim()
}

/**
 * Detect XML-style tool calls generated by models and deconstruct into structured objects.
 *
 * Supported patterns:
 * - <invoke name="...">...</invoke>
 * - <antcall name="...">...</antcall>
 * - <write_file><path>...</path><content>...</content></write_file>
 * - OpenCode directory XML: <path>...</path><type>directory</type><entries>...</entries>
 * - <function_calls>...</function_calls>
 */
export function extractXmlToolCalls(text: string): [string, DeconstructedToolCall[]] {
  if (!text) {
    return ['', []]
  }

  const toolCalls: DeconstructedToolCall[] = []
  let cleaned = text

  // 1. Function calls wrapper
  const fcRegex = /<function_calls(?:\s+[^>]*)?>([\s\S]*?)<\/function_calls>/gi
  cleaned = cleaned.replace(fcRegex, (_m, fcContent: string) => {
    const [, subCalls] = extractXmlToolCalls(fcContent)
    toolCalls.push(...subCalls)
    return ''
  })

  // 2. OpenCode directory XML
  const opencodeDirRegex =
    /(?:<directory(?:\s+[^>]*)?>\s*)?<path>([\s\S]*?)<\/path>\s*<type>\s*directory\s*<\/type>\s*<entries>([\s\S]*?)<\/entries>(?:\s*<\/directory>)?/gi
  cleaned = cleaned.replace(opencodeDirRegex, (_m, pathStr: string, entriesRaw: string) => {
    toolCalls.push({
      name: 'directory',
      input: {
        path: pathStr.trim(),
        type: 'directory',
        entries: parseOpenCodeEntries(entriesRaw),
      },
    })
    return ''
  })

  // Directory tag wrapper
  const dirWrapperRegex = /<directory(?:\s+[^>]*)?>([\s\S]*?)<\/directory>/gi
  cleaned = cleaned.replace(dirWrapperRegex, (match, inner: string) => {
    const parsed = parseXmlChildTags(inner)
    if (parsed && ('path' in parsed || 'entries' in parsed)) {
      toolCalls.push({
        name: 'directory',
        input: parsed,
      })
      return ''
    }
    return match
  })

  // 3. File write / create / edit tools
  const fileToolRegex = /<(write_file|create_file|edit_file)(\s+[^>]*)?>([\s\S]*?)<\/\1>/gi
  cleaned = cleaned.replace(fileToolRegex, (_match, tagName: string, attrs: string, inner: string) => {
    const name = tagName.toLowerCase()
    const inputObj: Record<string, unknown> = {}
    const attrPath = /path=["']([^"']+)["']/i.exec(attrs || '')
    if (attrPath) {
      inputObj.path = attrPath[1].trim()
      inputObj.content = inner.trim()
    } else {
      const parsed = parseXmlChildTags(inner)
      if (parsed) {
        Object.assign(inputObj, parsed)
      } else {
        inputObj.content = inner.trim()
      }
    }
    toolCalls.push({ name, input: inputObj })
    return ''
  })

  // 4. <invoke>, <antcall>, <call>
  const invokeRegex = /<(invoke|antcall|call)(\s+[^>]*)?>([\s\S]*?)<\/\1>/gi
  cleaned = cleaned.replace(invokeRegex, (_match, _tag: string, attrs: string, inner: string) => {
    let toolName = ''
    const nameAttr = /(?:name|tool|action)=["']([^"']+)["']/i.exec(attrs || '')
    if (nameAttr) {
      toolName = nameAttr[1].trim()
    } else {
      const childName = /<name(?:\s+[^>]*)?>([\s\S]*?)<\/name>/i.exec(inner)
      if (childName) {
        toolName = childName[1].trim()
        inner = inner.slice(0, childName.index) + inner.slice(childName.index + childName[0].length)
      }
    }

    if (!toolName) toolName = 'tool'
    const toolInput = parseToolArguments(inner)
    toolCalls.push({ name: toolName, input: toolInput as any })
    return ''
  })

  // 5. Generic <tool_call>
  const genericTcRegex = /<tool_call(?:\s+[^>]*)?>([\s\S]*?)<\/tool_call>/gi
  cleaned = cleaned.replace(genericTcRegex, (_match, inner: string) => {
    const trimmed = inner.trim()
    if (trimmed.startsWith('{') && trimmed.endsWith('}')) {
      try {
        const data = JSON.parse(trimmed)
        if (data && typeof data === 'object') {
          const name = data.name || data.tool || 'tool'
          const inp = data.input ?? data.arguments ?? data.args ?? {}
          toolCalls.push({ name, input: inp })
          return ''
        }
      } catch {
        // continue
      }
    }

    const parsed = parseXmlChildTags(trimmed)
    if (parsed && 'name' in parsed) {
      const name = String(parsed.name)
      delete parsed.name
      const inp = parsed.arguments ?? parsed.input ?? parsed
      toolCalls.push({ name, input: inp as any })
      return ''
    }
    return ''
  })

  cleaned = cleaned.replace(/\n{3,}/g, '\n\n').trim()
  return [cleaned, toolCalls]
}

export const extract_xml_tool_calls = extractXmlToolCalls

// --------------------------------------------------------------------------
// 3. System & Harness Wrapper Stripping
// --------------------------------------------------------------------------

const HARNESS_TAGS_TO_STRIP = [
  'ADDITIONAL_METADATA',
  'CONTEXT_SUMMARY',
  'context_summary',
  'system-reminder',
  'collaboration_mode',
  'environment_context',
  'permissions instructions',
  'permissions_instructions',
  'skills_instructions',
  'skills-instructions',
  'multi_agent_mode',
  'local-command-caveat',
]

/**
 * Strip agent harness wrappers, internal metadata tags, and IDE context prompts.
 */
export function stripAgentHarnessWrappers(text: string): string {
  if (!text) return ''

  let t = text

  // 1. Unwrap USER_REQUEST tags
  t = t.replace(/<\/?USER_REQUEST>\s*/gi, '')

  // 2. Strip metadata harness tags completely
  for (const tag of HARNESS_TAGS_TO_STRIP) {
    const pattern = new RegExp(`<${tag}(?:\\s+[^>]*)?>[\\s\\S]*?<\\/${tag}>\\s*`, 'gi')
    t = t.replace(pattern, '')
  }

  // 3. Untrusted tool result unwrap
  t = t.replace(/<untrusted_tool_result(?:\s+[^>]*)?>([\s\S]*?)<\/untrusted_tool_result>\s*/gi, (_m, inner: string) => {
    let clean = inner.trim()
    clean = clean.replace(/^The following content was retrieved[^\n]*\n+/i, '')
    return clean.trim()
  })

  // 4. Claude Code / CodeBuddy command tags
  const cmdMatch = /<command-name>\s*([\s\S]*?)\s*<\/command-name>/i.exec(t)
  if (cmdMatch) {
    const cmd = cmdMatch[1].trim()
    const msgMatch = /<command-message>\s*([\s\S]*?)\s*<\/command-message>/i.exec(t)
    const msg = msgMatch ? msgMatch[1].trim() : ''
    const cmdStr = `⚡ Command: ${cmd}${msg && msg !== cmd ? ` (${msg})` : ''}`
    t = t.replace(/<command-name>\s*[\s\S]*?\s*<\/command-name>/i, cmdStr)
  }
  t = t.replace(/<command-args>[\s\S]*?<\/command-args>\s*/gi, '')
  t = t.replace(/<command-message>[\s\S]*?<\/command-message>\s*/gi, '')

  // Unwrap local-command-stdout and stderr
  t = t.replace(/<\/?local-command-stdout>\s*/gi, '')
  t = t.replace(/<\/?local-command-stderr>\s*/gi, '')

  // 5. Codex / IDE setup prompt wrappers
  t = t.replace(
    /#+\s*Context\s+from\s+my\s+IDE\s+setup:[\s\S]*?(?:(?:my\s+request\s+for\s+codex\s*:?)|(?:\n(?=[^\s#\-*])))/gi,
    '',
  )
  t = t.replace(/^\s*my\s+request\s+for\s+codex\s*:?\s*/gim, '')
  t = t.replace(/#+\s*Context\s+from\s+my\s+IDE\s+setup:[\s\S]*?(?=\n\n|$)/gi, '')

  // 6. Hermes model switch note
  t = t.replace(/\[Note:\s*model was just switched from[\s\S]*?\]\s*/gi, '')

  // 7. Clean up any orphan or broken wrapper tags
  const allTagNames = [
    ...HARNESS_TAGS_TO_STRIP,
    'USER_REQUEST',
    'untrusted_tool_result',
    'local-command-stdout',
    'local-command-stderr',
    'command-name',
    'command-args',
    'command-message',
  ]
  const orphanRegex = new RegExp(`</?(?:${allTagNames.join('|')})(?:\\s+[^>]*)?>`, 'gi')
  t = t.replace(orphanRegex, '')

  // 8. Clean excess whitespace
  t = t.replace(/\r\n/g, '\n')
  t = t.replace(/\n{3,}/g, '\n\n')
  return t.trim()
}

export const strip_agent_harness_wrappers = stripAgentHarnessWrappers

// --------------------------------------------------------------------------
// 4. Comprehensive Message Deconstruction Pipeline
// --------------------------------------------------------------------------

/**
 * Execute complete deconstruction pipeline on an agent message.
 */
export function deconstructMessage(text: string): DeconstructedMessage {
  const [textWithoutThinking, thinking] = extractThinkingFromText(text)
  const [textWithoutTools, toolCalls] = extractXmlToolCalls(textWithoutThinking)
  const content = stripAgentHarnessWrappers(textWithoutTools)

  return {
    content: content.trim(),
    thinking,
    tool_calls: toolCalls,
  }
}

export const deconstruct_message = deconstructMessage
