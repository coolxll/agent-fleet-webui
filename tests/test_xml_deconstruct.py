"""Unit tests for XML deconstructor, thinking extractor, and harness wrapper stripping."""

from __future__ import annotations

import pytest

from agent_fleet.services.xml_deconstruct import (
    deconstruct_message,
    extract_thinking_from_text,
    extract_xml_tool_calls,
    strip_agent_harness_wrappers,
)


# ==========================================================================
# 1. Model Thinking Extraction Tests
# ==========================================================================


def test_extract_thinking_basic():
    raw = "<thinking>Analyzing user question</thinking>\nHere is the answer."
    clean, thinking = extract_thinking_from_text(raw)
    assert clean == "Here is the answer."
    assert thinking == "Analyzing user question"


@pytest.mark.parametrize(
    "tag",
    ["thought", "antthinking", "reasoning", "scratchpad"],
)
def test_extract_thinking_all_tag_variants(tag: str):
    raw = f"<{tag}>Reasoning process step 1</{tag}>\nResult is 42."
    clean, thinking = extract_thinking_from_text(raw)
    assert clean == "Result is 42."
    assert thinking == "Reasoning process step 1"


def test_extract_thinking_case_insensitive_and_attributes():
    raw = '<THINKING id="plan" step="1">Consider edge cases</THINKING>Done.'
    clean, thinking = extract_thinking_from_text(raw)
    assert clean == "Done."
    assert thinking == "Consider edge cases"


def test_extract_thinking_multiple_blocks():
    raw = (
        "<thought>First, check database schema</thought>\n"
        "Middle content explaining action.\n"
        "<reasoning>Next, apply index optimization</reasoning>\n"
        "Final conclusion."
    )
    clean, thinking = extract_thinking_from_text(raw)
    assert clean == "Middle content explaining action.\n\nFinal conclusion."
    assert thinking == "First, check database schema\n\nNext, apply index optimization"


def test_extract_thinking_with_nested_xml_and_code():
    raw = (
        "<thinking>\n"
        "Let's look at the component:\n"
        "<div className=\"container\">\n"
        "  <span>Hello</span>\n"
        "</div>\n"
        "</thinking>\n"
        "The component renders a container."
    )
    clean, thinking = extract_thinking_from_text(raw)
    assert clean == "The component renders a container."
    assert "<span>Hello</span>" in thinking
    assert "<div className=\"container\">" in thinking


def test_extract_thinking_empty_or_whitespace():
    raw = "<thinking>   </thinking>\nJust text."
    clean, thinking = extract_thinking_from_text(raw)
    assert clean == "Just text."
    assert thinking is None


def test_extract_thinking_no_tags():
    raw = "Just a standard assistant message with no thinking tags."
    clean, thinking = extract_thinking_from_text(raw)
    assert clean == raw
    assert thinking is None


def test_extract_thinking_unclosed_tag_at_end():
    raw = "Working on it...\n<thinking>Thinking in progress"
    clean, thinking = extract_thinking_from_text(raw)
    assert clean == "Working on it..."
    assert thinking == "Thinking in progress"


# ==========================================================================
# 2. Model XML Tool Call Deconstruction Tests
# ==========================================================================


def test_extract_invoke_with_parameters():
    raw = (
        "Let me run tests.\n"
        '<invoke name="bash">\n'
        '  <parameter name="command">pytest tests/</parameter>\n'
        '  <parameter name="timeout">30</parameter>\n'
        '  <parameter name="verbose">true</parameter>\n'
        "</invoke>\n"
        "Awaiting results."
    )
    clean, tools = extract_xml_tool_calls(raw)
    assert clean == "Let me run tests.\n\nAwaiting results."
    assert len(tools) == 1
    assert tools[0]["name"] == "bash"
    assert tools[0]["input"] == {
        "command": "pytest tests/",
        "timeout": 30,
        "verbose": True,
    }


def test_extract_invoke_with_param_or_arg_tags():
    raw = (
        '<invoke name="fetch_url">\n'
        '  <arg name="url">https://example.com/api</arg>\n'
        '  <param name="retry">false</param>\n'
        "</invoke>"
    )
    clean, tools = extract_xml_tool_calls(raw)
    assert clean == ""
    assert len(tools) == 1
    assert tools[0]["name"] == "fetch_url"
    assert tools[0]["input"] == {
        "url": "https://example.com/api",
        "retry": False,
    }


def test_extract_antcall_with_args():
    raw = (
        '<antcall name="run_command">\n'
        '  <arg name="CommandLine">npm run build</arg>\n'
        '  <arg name="Cwd">/workspace/web</arg>\n'
        "</antcall>"
    )
    clean, tools = extract_xml_tool_calls(raw)
    assert clean == ""
    assert len(tools) == 1
    assert tools[0]["name"] == "run_command"
    assert tools[0]["input"] == {
        "CommandLine": "npm run build",
        "Cwd": "/workspace/web",
    }


def test_extract_write_file_tags():
    raw = (
        "I will create the configuration file:\n"
        "<write_file>\n"
        "  <path>config.json</path>\n"
        '  <content>{"debug": true}</content>\n'
        "</write_file>\n"
        "File created."
    )
    clean, tools = extract_xml_tool_calls(raw)
    assert clean == "I will create the configuration file:\n\nFile created."
    assert len(tools) == 1
    assert tools[0]["name"] == "write_file"
    assert tools[0]["input"]["path"] == "config.json"
    assert tools[0]["input"]["content"] == '{"debug": true}' or tools[0]["input"]["content"] == {"debug": True}


def test_extract_write_file_with_path_attribute():
    raw = '<write_file path="/src/index.js">console.log("hello");</write_file>'
    clean, tools = extract_xml_tool_calls(raw)
    assert clean == ""
    assert len(tools) == 1
    assert tools[0]["name"] == "write_file"
    assert tools[0]["input"] == {
        "path": "/src/index.js",
        "content": 'console.log("hello");',
    }


def test_extract_opencode_directory_xml():
    raw = (
        "Directory listing:\n"
        "<path>/workspace/src</path>\n"
        "<type>directory</type>\n"
        "<entries>\n"
        "  <entry>\n"
        "    <path>index.ts</path>\n"
        "    <type>file</type>\n"
        "  </entry>\n"
        "  <entry>\n"
        "    <path>components</path>\n"
        "    <type>directory</type>\n"
        "  </entry>\n"
        "</entries>\n"
        "Listing complete."
    )
    clean, tools = extract_xml_tool_calls(raw)
    assert clean == "Directory listing:\n\nListing complete."
    assert len(tools) == 1
    assert tools[0]["name"] == "directory"
    inp = tools[0]["input"]
    assert inp["path"] == "/workspace/src"
    assert inp["type"] == "directory"
    assert isinstance(inp["entries"], list)
    assert len(inp["entries"]) == 2
    assert inp["entries"][0] == {"path": "index.ts", "type": "file"}
    assert inp["entries"][1] == {"path": "components", "type": "directory"}


def test_extract_opencode_directory_wrapped_in_directory_tag():
    raw = (
        "<directory>\n"
        "  <path>/workspace/docs</path>\n"
        "  <type>directory</type>\n"
        "  <entries>\n"
        "    <entry>guide.md</entry>\n"
        "    <entry>api.md</entry>\n"
        "  </entries>\n"
        "</directory>"
    )
    clean, tools = extract_xml_tool_calls(raw)
    assert clean == ""
    assert len(tools) == 1
    assert tools[0]["name"] == "directory"
    inp = tools[0]["input"]
    assert inp["path"] == "/workspace/docs"
    assert inp["entries"] == ["guide.md", "api.md"]


def test_extract_function_calls_wrapper_multiple():
    raw = (
        "<function_calls>\n"
        '<invoke name="read_file">\n'
        '  <parameter name="path">a.txt</parameter>\n'
        "</invoke>\n"
        '<invoke name="read_file">\n'
        '  <parameter name="path">b.txt</parameter>\n'
        "</invoke>\n"
        "</function_calls>"
    )
    clean, tools = extract_xml_tool_calls(raw)
    assert clean == ""
    assert len(tools) == 2
    assert tools[0]["name"] == "read_file"
    assert tools[0]["input"] == {"path": "a.txt"}
    assert tools[1]["name"] == "read_file"
    assert tools[1]["input"] == {"path": "b.txt"}


def test_extract_invoke_with_json_payload():
    raw = (
        '<invoke name="update_user">\n'
        '{"id": 123, "active": true, "roles": ["admin"]}\n'
        "</invoke>"
    )
    clean, tools = extract_xml_tool_calls(raw)
    assert clean == ""
    assert len(tools) == 1
    assert tools[0]["name"] == "update_user"
    assert tools[0]["input"] == {
        "id": 123,
        "active": True,
        "roles": ["admin"],
    }


def test_extract_generic_tool_call():
    raw = (
        "<tool_call>\n"
        '{"name": "calculator", "arguments": {"expr": "2 + 2"}}\n'
        "</tool_call>"
    )
    clean, tools = extract_xml_tool_calls(raw)
    assert clean == ""
    assert len(tools) == 1
    assert tools[0]["name"] == "calculator"
    assert tools[0]["input"] == {"expr": "2 + 2"}


# ==========================================================================
# 3. System & Harness Wrapper Stripping Tests
# ==========================================================================


def test_strip_antigravity_gemini_user_request():
    raw = (
        "<USER_REQUEST>\n"
        "Please fix the failing authentication unit tests.\n"
        "</USER_REQUEST>"
    )
    clean = strip_agent_harness_wrappers(raw)
    assert clean == "Please fix the failing authentication unit tests."


def test_strip_antigravity_metadata_and_reminders():
    raw = (
        "<ADDITIONAL_METADATA>\n"
        '{"session_type": "interactive", "model_quota": 99}\n'
        "</ADDITIONAL_METADATA>\n"
        "<system-reminder priority=\"high\">Remember user instructions</system-reminder>\n"
        "<collaboration_mode>multi_agent</collaboration_mode>\n"
        "<environment_context>OS=Darwin Arch=arm64</environment_context>\n"
        "<permissions instructions>Allow workspace edits</permissions instructions>\n"
        "<skills_instructions>Use python-testing skill</skills_instructions>\n"
        "<multi_agent_mode>enabled</multi_agent_mode>\n"
        "<USER_REQUEST>\n"
        "Deploy the web service\n"
        "</USER_REQUEST>\n"
        "<context_summary>Previous turns discussed architecture</context_summary>"
    )
    clean = strip_agent_harness_wrappers(raw)
    assert clean == "Deploy the web service"
    assert "ADDITIONAL_METADATA" not in clean
    assert "system-reminder" not in clean
    assert "collaboration_mode" not in clean
    assert "skills_instructions" not in clean


def test_strip_untrusted_tool_result():
    raw = (
        '<untrusted_tool_result source="search">\n'
        "The following content was retrieved from the remote web search:\n\n"
        "FastAPI version 0.115 is the latest release.\n"
        "</untrusted_tool_result>"
    )
    clean = strip_agent_harness_wrappers(raw)
    assert clean == "FastAPI version 0.115 is the latest release."
    assert "The following content was retrieved" not in clean
    assert "untrusted_tool_result" not in clean


def test_strip_claude_code_command_tags():
    raw = (
        "<local-command-caveat>Command executed locally</local-command-caveat>\n"
        "<command-name>git diff --stat</command-name>\n"
        "<command-args>[\"--stat\"]</command-args>\n"
        "<command-message>Checking changed files</command-message>\n"
        "<local-command-stdout>\n"
        " 2 files changed, 45 insertions(+)\n"
        "</local-command-stdout>"
    )
    clean = strip_agent_harness_wrappers(raw)
    assert "⚡ Command: git diff --stat (Checking changed files)" in clean
    assert "2 files changed, 45 insertions(+)" in clean
    assert "<local-command-caveat>" not in clean
    assert "<command-args>" not in clean
    assert "</local-command-stdout>" not in clean


def test_strip_claude_code_stderr():
    raw = (
        "<local-command-stderr>\n"
        "error: pathspec 'unknown' did not match any file(s) known to git\n"
        "</local-command-stderr>"
    )
    clean = strip_agent_harness_wrappers(raw)
    assert clean == "error: pathspec 'unknown' did not match any file(s) known to git"
    assert "<local-command-stderr>" not in clean


def test_strip_codex_ide_setup_prompt():
    raw = (
        "# Context from my IDE setup:\n"
        "- Active file: /Users/lynn/project/src/main.py\n"
        "- Selection: lines 10-25\n"
        "- Workspace root: /Users/lynn/project\n\n"
        "my request for codex:\n"
        "Add input validation to this function."
    )
    clean = strip_agent_harness_wrappers(raw)
    assert clean == "Add input validation to this function."
    assert "Context from my IDE setup" not in clean
    assert "my request for codex" not in clean


def test_strip_codex_standalone_marker():
    raw = "My request for Codex:\nPlease explain this algorithm."
    clean = strip_agent_harness_wrappers(raw)
    assert clean == "Please explain this algorithm."


def test_strip_hermes_switch_note():
    raw = (
        "[Note: model was just switched from gpt-4 to claude-3-5-sonnet]\n"
        "I am ready to help you."
    )
    clean = strip_agent_harness_wrappers(raw)
    assert clean == "I am ready to help you."


# ==========================================================================
# 4. Comprehensive Pipeline Tests (deconstruct_message)
# ==========================================================================


def test_deconstruct_message_complete_assistant_turn():
    raw = (
        "<thinking>\n"
        "The user wants to list files in src. I will invoke directory listing.\n"
        "</thinking>\n"
        "<system-reminder>Respect permissions</system-reminder>\n"
        "Here are the directory contents:\n"
        "<path>/workspace/src</path>\n"
        "<type>directory</type>\n"
        "<entries>\n"
        "  <entry>main.py</entry>\n"
        "  <entry>utils.py</entry>\n"
        "</entries>\n"
        "Let me know if you want to inspect any file."
    )
    res = deconstruct_message(raw)
    assert res["thinking"] == "The user wants to list files in src. I will invoke directory listing."
    assert len(res["tool_calls"]) == 1
    assert res["tool_calls"][0]["name"] == "directory"
    assert res["tool_calls"][0]["input"]["path"] == "/workspace/src"
    assert res["tool_calls"][0]["input"]["entries"] == ["main.py", "utils.py"]
    assert "system-reminder" not in res["content"]
    assert "Here are the directory contents:\n\nLet me know if you want to inspect any file." == res["content"]


def test_deconstruct_message_user_turn_with_harness():
    raw = (
        "<ADDITIONAL_METADATA>client=vscode</ADDITIONAL_METADATA>\n"
        "<USER_REQUEST>\n"
        "Please refactor the db connection pooling.\n"
        "</USER_REQUEST>"
    )
    res = deconstruct_message(raw)
    assert res["content"] == "Please refactor the db connection pooling."
    assert res["thinking"] is None
    assert res["tool_calls"] == []


def test_deconstruct_message_empty_text():
    res = deconstruct_message("")
    assert res["content"] == ""
    assert res["thinking"] is None
    assert res["tool_calls"] == []
