"""XML deconstructor, thinking extractor, and agent harness wrapper stripper.

Systematically extracts thinking blocks, deconstructs XML-formatted tool calls
into structured objects, and strips internal agent harness wrappers across
Claude Code, Codex, Antigravity / Gemini, OpenCode, Qoder, CodeBuddy, Pi, and Hermes.
"""

from __future__ import annotations

import json
import re
from typing import Any


# --------------------------------------------------------------------------
# 1. Model Thinking Extraction
# --------------------------------------------------------------------------

# Tags indicating model reasoning/thinking/scratchpad
THINKING_TAGS = ("thinking", "thought", "antthinking", "reasoning", "scratchpad")

# Regex to find thinking blocks, e.g. <thinking>...</thinking>, <thought>...</thought>
# Case-insensitive, multiline (DOTALL), non-greedy to handle multiple blocks.
THINKING_PATTERN = re.compile(
    r"<(?P<tag>" + "|".join(THINKING_TAGS) + r")(?:\s+[^>]*)?>(?P<content>.*?)</(?P=tag)>",
    re.DOTALL | re.IGNORECASE,
)

# Handle unclosed thinking tag at end of text (e.g. streaming or truncated text)
UNCLOSED_THINKING_PATTERN = re.compile(
    r"<(?P<tag>" + "|".join(THINKING_TAGS) + r")(?:\s+[^>]*)?>(?P<content>[\s\S]*)$",
    re.IGNORECASE,
)


def extract_thinking_from_text(text: str) -> tuple[str, str | None]:
    """Extract model thinking/reasoning blocks from text.

    Detects <thinking>, <thought>, <antthinking>, <reasoning>, <scratchpad> tags.
    Returns:
        (clean_text, thinking)
        - clean_text: Text with all thinking tags and their contents removed.
        - thinking: The extracted thinking content (joined by double newline if multiple),
                    or None if no thinking was found.
    """
    if not text:
        return ("", None)

    thinking_blocks: list[str] = []

    # First pass: find closed thinking blocks
    def _collect_and_remove(match: re.Match[str]) -> str:
        content = match.group("content").strip()
        if content:
            thinking_blocks.append(content)
        return ""

    cleaned = THINKING_PATTERN.sub(_collect_and_remove, text)

    # Check for unclosed thinking block at the end (only if tag was opened and not closed)
    unclosed_match = UNCLOSED_THINKING_PATTERN.search(cleaned)
    if unclosed_match:
        content = unclosed_match.group("content").strip()
        if content:
            thinking_blocks.append(content)
        cleaned = cleaned[: unclosed_match.start()]

    # Clean up residual multiple newlines
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()

    thinking = "\n\n".join(thinking_blocks) if thinking_blocks else None
    return (cleaned, thinking)


# --------------------------------------------------------------------------
# 2. Model XML Tool Call Deconstruction
# --------------------------------------------------------------------------


def _parse_value(val: str) -> Any:
    """Intelligently parse parameter string into bool, int, float, json, or str."""
    val_str = val.strip()
    lower = val_str.lower()
    if lower == "true":
        return True
    if lower == "false":
        return False
    if lower in ("null", "none"):
        return None

    # Check int
    if re.fullmatch(r"-?\d+", val_str):
        try:
            return int(val_str)
        except ValueError:
            pass

    # Check float
    if re.fullmatch(r"-?\d+\.\d+", val_str):
        try:
            return float(val_str)
        except ValueError:
            pass

    # Check JSON object or array
    if (val_str.startswith("{") and val_str.endswith("}")) or (
        val_str.startswith("[") and val_str.endswith("]")
    ):
        try:
            return json.loads(val_str)
        except Exception:
            pass

    return val_str


def _parse_xml_child_tags(inner_text: str) -> dict[str, Any] | None:
    """Parse XML child elements like <path>foo</path><content>bar</content> into a dict."""
    # Look for tags: <key>value</key>
    tag_matches = list(re.finditer(r"<([a-zA-Z0-9_\-]+)(?:\s+[^>]*)?>(.*?)</\1>", inner_text, re.DOTALL))
    if not tag_matches:
        return None

    result: dict[str, Any] = {}
    for m in tag_matches:
        k = m.group(1).strip()
        v = m.group(2)
        # Check if value has further nested tags
        nested = _parse_xml_child_tags(v)
        if nested:
            result[k] = nested
        else:
            result[k] = _parse_value(v)
    return result


def _parse_tool_arguments(inner_text: str) -> Any:
    """Extract tool arguments from inner XML block text."""
    trimmed = inner_text.strip()
    if not trimmed:
        return {}

    # 1. Parameter / arg tags: <parameter name="foo">bar</parameter> or <arg name="foo">bar</arg>
    param_matches = list(
        re.finditer(
            r"<(?:parameter|param|arg)\s+name=[\"']([^\"']+)[\"'][^>]*>(.*?)</(?:parameter|param|arg)>",
            trimmed,
            re.DOTALL | re.IGNORECASE,
        )
    )
    if param_matches:
        params: dict[str, Any] = {}
        for m in param_matches:
            k = m.group(1).strip()
            v = m.group(2)
            params[k] = _parse_value(v)
        return params

    # 2. Check for child <arguments>...</arguments>
    m_args = re.search(r"<arguments(?:\s+[^>]*)?>(.*?)</arguments>", trimmed, re.DOTALL | re.IGNORECASE)
    if m_args:
        args_text = m_args.group(1).strip()
        try:
            return json.loads(args_text)
        except Exception:
            parsed = _parse_xml_child_tags(args_text)
            if parsed is not None:
                return parsed
            return args_text

    # 3. Direct JSON string
    if (trimmed.startswith("{") and trimmed.endswith("}")) or (
        trimmed.startswith("[") and trimmed.endswith("]")
    ):
        try:
            return json.loads(trimmed)
        except Exception:
            pass

    # 4. XML key-value children: <path>foo</path><content>bar</content>
    parsed_children = _parse_xml_child_tags(trimmed)
    if parsed_children is not None:
        return parsed_children

    # 5. Raw text
    return trimmed


def _parse_opencode_entries(entries_content: str) -> Any:
    """Parse OpenCode <entries> block into structured list or dict."""
    entry_matches = list(
        re.finditer(r"<entry(?:\s+[^>]*)?>(.*?)</entry>", entries_content, re.DOTALL | re.IGNORECASE)
    )
    if entry_matches:
        entries: list[Any] = []
        for em in entry_matches:
            c = em.group(1).strip()
            c_dict = _parse_xml_child_tags(c)
            if c_dict:
                entries.append(c_dict)
            else:
                entries.append(_parse_value(c))
        return entries

    # If no <entry> tags, check for lines of text
    lines = [line.strip() for line in entries_content.strip().splitlines() if line.strip()]
    return lines if lines else entries_content.strip()


def extract_xml_tool_calls(text: str) -> tuple[str, list[dict[str, Any]]]:
    """Detect XML-style tool calls generated by models and deconstruct into structured objects.

    Supported patterns:
    - <invoke name="...">...</invoke>
    - <antcall name="...">...</antcall>
    - <write_file><path>...</path><content>...</content></write_file> (also create_file, edit_file)
    - OpenCode directory XML: <path>...</path><type>directory</type><entries>...</entries>
    - <function_calls>...</function_calls> (wrapper around multiple calls)
    - <call name="...">...</call>
    - <tool_call>...</tool_call>

    Returns:
        (clean_text, tool_calls)
        - clean_text: Text with tool call XML tags removed.
        - tool_calls: List of structured dicts {"name": tool_name, "input": tool_input}.
    """
    if not text:
        return ("", [])

    tool_calls: list[dict[str, Any]] = []
    cleaned = text

    # --- 1. Handle <function_calls>...</function_calls> wrapper ---
    def _handle_function_calls(m: re.Match[str]) -> str:
        fc_content = m.group(1)
        # Recursively extract any invoke / call inside function_calls
        _, sub_calls = extract_xml_tool_calls(fc_content)
        tool_calls.extend(sub_calls)
        return ""

    cleaned = re.sub(
        r"<function_calls(?:\s+[^>]*)?>(.*?)</function_calls>",
        _handle_function_calls,
        cleaned,
        flags=re.DOTALL | re.IGNORECASE,
    )

    # --- 2. OpenCode directory XML: <path>...</path><type>directory</type><entries>...</entries> ---
    # Can optionally be wrapped in <directory>...</directory>
    opencode_dir_re = re.compile(
        r"(?:<directory(?:\s+[^>]*)?>\s*)?"
        r"<path>(?P<path>.*?)</path>\s*"
        r"<type>\s*directory\s*</type>\s*"
        r"<entries>(?P<entries>.*?)</entries>"
        r"(?:\s*</directory>)?",
        re.DOTALL | re.IGNORECASE,
    )

    def _handle_opencode_dir(m: re.Match[str]) -> str:
        path = m.group("path").strip()
        entries_raw = m.group("entries")
        parsed_entries = _parse_opencode_entries(entries_raw)
        tool_calls.append(
            {
                "name": "directory",
                "input": {
                    "path": path,
                    "type": "directory",
                    "entries": parsed_entries,
                },
            }
        )
        return ""

    cleaned = opencode_dir_re.sub(_handle_opencode_dir, cleaned)

    # Also handle alternate order of tags in directory XML (<type> then <path> etc)
    opencode_dir_alt_re = re.compile(
        r"<directory(?:\s+[^>]*)?>(?P<inner>.*?)</directory>",
        re.DOTALL | re.IGNORECASE,
    )

    def _handle_opencode_dir_alt(m: re.Match[str]) -> str:
        inner = m.group("inner")
        parsed = _parse_xml_child_tags(inner)
        if parsed and ("path" in parsed or "entries" in parsed):
            tool_calls.append(
                {
                    "name": "directory",
                    "input": parsed,
                }
            )
            return ""
        return m.group(0)

    cleaned = opencode_dir_alt_re.sub(_handle_opencode_dir_alt, cleaned)

    # --- 3. <write_file>, <create_file>, <edit_file> tags ---
    file_tool_re = re.compile(
        r"<(?P<tag>write_file|create_file|edit_file)(?P<attrs>\s+[^>]*)?>(?P<inner>.*?)</(?P=tag)>",
        re.DOTALL | re.IGNORECASE,
    )

    def _handle_file_tool(m: re.Match[str]) -> str:
        tag_name = m.group("tag").lower()
        attrs = m.group("attrs") or ""
        inner = m.group("inner")

        tool_input: dict[str, Any] = {}
        # Check path in attribute: path="..."
        m_attr_path = re.search(r'path=[\'"]([^\'"]+)[\'"]', attrs, re.IGNORECASE)
        if m_attr_path:
            tool_input["path"] = m_attr_path.group(1).strip()
            tool_input["content"] = inner.strip()
        else:
            parsed = _parse_xml_child_tags(inner)
            if parsed:
                tool_input.update(parsed)
            else:
                tool_input["content"] = inner.strip()

        tool_calls.append({"name": tag_name, "input": tool_input})
        return ""

    cleaned = file_tool_re.sub(_handle_file_tool, cleaned)

    # --- 4. <invoke name="...">...</invoke>, <antcall name="...">...</antcall>, <call name="...">...</call> ---
    invoke_re = re.compile(
        r"<(?P<tag>invoke|antcall|call)(?P<attrs>\s+[^>]*)?>(?P<inner>.*?)</(?P=tag)>",
        re.DOTALL | re.IGNORECASE,
    )

    def _handle_invoke(m: re.Match[str]) -> str:
        attrs = m.group("attrs") or ""
        inner = m.group("inner")

        # Determine tool name from attribute
        m_name = re.search(r'(?:name|tool|action)=[\'"]([^\'"]+)[\'"]', attrs, re.IGNORECASE)
        tool_name = m_name.group(1).strip() if m_name else ""

        # Or from child <name>...</name>
        if not tool_name:
            m_child_name = re.search(r"<name(?:\s+[^>]*)?>(.*?)</name>", inner, re.DOTALL | re.IGNORECASE)
            if m_child_name:
                tool_name = m_child_name.group(1).strip()
                inner = inner[: m_child_name.start()] + inner[m_child_name.end() :]

        if not tool_name:
            tool_name = "tool"

        tool_input = _parse_tool_arguments(inner)
        tool_calls.append({"name": tool_name, "input": tool_input})
        return ""

    cleaned = invoke_re.sub(_handle_invoke, cleaned)

    # --- 5. Generic <tool_call>...</tool_call> ---
    tool_call_re = re.compile(
        r"<tool_call(?:\s+[^>]*)?>(?P<inner>.*?)</tool_call>",
        re.DOTALL | re.IGNORECASE,
    )

    def _handle_generic_tool_call(m: re.Match[str]) -> str:
        inner = m.group("inner").strip()
        # Could be JSON: {"name": ..., "arguments": ...}
        if inner.startswith("{") and inner.endswith("}"):
            try:
                data = json.loads(inner)
                if isinstance(data, dict):
                    name = data.get("name") or data.get("tool") or "tool"
                    inp = data.get("input") or data.get("arguments") or data.get("args") or {}
                    tool_calls.append({"name": name, "input": inp})
                    return ""
            except Exception:
                pass

        parsed = _parse_xml_child_tags(inner)
        if parsed and "name" in parsed:
            name = str(parsed.pop("name"))
            inp = parsed.get("arguments") or parsed.get("input") or parsed
            tool_calls.append({"name": name, "input": inp})
            return ""

        return ""

    cleaned = tool_call_re.sub(_handle_generic_tool_call, cleaned)

    # Clean up whitespace
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return (cleaned, tool_calls)


# --------------------------------------------------------------------------
# 3. System & Harness Wrapper Stripping
# --------------------------------------------------------------------------

# Full list of tags whose entire contents should be stripped
HARNESS_TAGS_TO_STRIP = (
    "ADDITIONAL_METADATA",
    "CONTEXT_SUMMARY",
    "context_summary",
    "system-reminder",
    "collaboration_mode",
    "environment_context",
    "permissions instructions",
    "permissions_instructions",
    "skills_instructions",
    "skills-instructions",
    "multi_agent_mode",
    "local-command-caveat",
)


def strip_agent_harness_wrappers(text: str) -> str:
    """Strip agent harness wrappers, internal metadata tags, and IDE context prompts.

    Supported wrappers:
    - Antigravity / Gemini:
        <USER_REQUEST>, <ADDITIONAL_METADATA>, <CONTEXT_SUMMARY>, <system-reminder>,
        <collaboration_mode>, <environment_context>, <permissions instructions>,
        <skills_instructions>, <multi_agent_mode>, <untrusted_tool_result>.
    - Claude Code:
        <local-command-caveat>, <local-command-stdout>, <local-command-stderr>,
        <command-name>, <command-args>, <command-message>.
    - Codex / IDE prompts:
        "# Context from my IDE setup:", "my request for codex:", etc.
    """
    if not text:
        return ""

    t = str(text)

    # 1. Antigravity / Gemini <USER_REQUEST>: unwrap content if present
    # Keep the user request text intact while stripping the enclosing tags
    t = re.sub(r"</?USER_REQUEST>\s*", "", t, flags=re.IGNORECASE)

    # 2. Strip tags whose content is internal harness metadata
    for tag in HARNESS_TAGS_TO_STRIP:
        pattern = re.compile(rf"<{re.escape(tag)}(?:\s+[^>]*)?>.*?</{re.escape(tag)}>\s*", re.DOTALL | re.IGNORECASE)
        t = pattern.sub("", t)

    # 3. Antigravity / Gemini <untrusted_tool_result>: unwrap content
    def _unwrap_untrusted_tool_result(m: re.Match[str]) -> str:
        inner = m.group(1).strip()
        # Remove standard boilerplate header if present
        inner = re.sub(r"^The following content was retrieved[^\n]*\n+", "", inner)
        return inner.strip()

    t = re.sub(
        r"<untrusted_tool_result(?:\s+[^>]*)?>(.*?)</untrusted_tool_result>\s*",
        _unwrap_untrusted_tool_result,
        t,
        flags=re.DOTALL | re.IGNORECASE,
    )

    # 4. Claude Code / CodeBuddy command tags
    m_cmd = re.search(r"<command-name>\s*(.*?)\s*</command-name>", t, re.IGNORECASE)
    if m_cmd:
        cmd = m_cmd.group(1).strip()
        msg = ""
        m_msg = re.search(r"<command-message>\s*(.*?)\s*</command-message>", t, re.IGNORECASE)
        if m_msg:
            msg = m_msg.group(1).strip()
        cmd_str = f"⚡ Command: {cmd}" + (f" ({msg})" if msg and msg != cmd else "")
        # Replace the command-name block with the formatted indicator
        t = re.sub(r"<command-name>\s*.*?\s*</command-name>", cmd_str, t, flags=re.IGNORECASE, count=1)

    t = re.sub(r"<command-args>.*?</command-args>\s*", "", t, flags=re.DOTALL | re.IGNORECASE)
    t = re.sub(r"<command-message>.*?</command-message>\s*", "", t, flags=re.DOTALL | re.IGNORECASE)

    # Claude Code <local-command-stdout> and <local-command-stderr>: unwrap content
    t = re.sub(r"</?local-command-stdout>\s*", "", t, flags=re.IGNORECASE)
    t = re.sub(r"</?local-command-stderr>\s*", "", t, flags=re.IGNORECASE)

    # 5. Codex / IDE setup prompt wrappers
    # E.g.
    # # Context from my IDE setup:
    # - Active file: ...
    # my request for codex:
    # <actual user request>
    t = re.sub(
        r"#+\s*Context\s+from\s+my\s+IDE\s+setup:.*?(?:(?:my\s+request\s+for\s+codex\s*:?)|(?:\n(?=[^\s#\-*])))",
        "",
        t,
        flags=re.DOTALL | re.IGNORECASE,
    )
    # Standalone "my request for codex:" marker
    t = re.sub(r"^\s*my\s+request\s+for\s+codex\s*:?\s*", "", t, flags=re.IGNORECASE | re.MULTILINE)
    # Standalone "# Context from my IDE setup:" if nothing followed
    t = re.sub(r"#+\s*Context\s+from\s+my\s+IDE\s+setup:.*?(?=\n\n|\Z)", "", t, flags=re.DOTALL | re.IGNORECASE)

    # 6. Hermes model switch note
    t = re.sub(r"\[Note:\s*model was just switched from.*?\]\s*", "", t, flags=re.DOTALL | re.IGNORECASE)

    # 7. Clean up any lingering or broken wrapper tags
    all_tag_names = list(HARNESS_TAGS_TO_STRIP) + [
        "USER_REQUEST",
        "untrusted_tool_result",
        "local-command-stdout",
        "local-command-stderr",
        "command-name",
        "command-args",
        "command-message",
    ]
    orphan_re = re.compile(
        rf"</?(?:{'|'.join(re.escape(name) for name in all_tag_names)})(?:\s+[^>]*)?>",
        re.IGNORECASE,
    )
    t = orphan_re.sub("", t)

    # 8. Clean excess blank lines and strip ends
    t = re.sub(r"\r\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


# --------------------------------------------------------------------------
# 4. Comprehensive Message Deconstruction Pipeline
# --------------------------------------------------------------------------


def deconstruct_message(text: str) -> dict[str, Any]:
    """Execute complete deconstruction pipeline on an agent message.

    1. Extract model thinking / reasoning blocks.
    2. Deconstruct XML tool calls into structured objects.
    3. Strip internal agent harness wrappers and metadata.

    Returns:
        {
            "content": str,          # Clean user-facing text
            "thinking": str | None,  # Extracted model thinking
            "tool_calls": list[dict] # Structured tool calls [{"name": ..., "input": ...}]
        }
    """
    clean_text, thinking = extract_thinking_from_text(text)
    clean_text, tool_calls = extract_xml_tool_calls(clean_text)
    clean_text = strip_agent_harness_wrappers(clean_text)

    return {
        "content": clean_text.strip(),
        "thinking": thinking,
        "tool_calls": tool_calls,
    }
