import json
import pytest
from datetime import datetime
from agent_fleet import models
from agent_fleet.services import session_service
from agent_fleet.remotes.executor import ExecResult

class DummyRunner:
    def __init__(self, stdout="", ok=True):
        self.stdout = stdout
        self._ok = ok

    async def run_script(self, script, timeout):
        return ExecResult(stdout=self.stdout, exit_code=0 if self._ok else 1)


def test_mask_sensitive_text():
    text = "Here is my secret sk-ant-api03-abcdef1234567890abcdef and ghp_1234567890abcdef1234567890"
    masked, count = session_service.mask_sensitive_text(text)
    assert count == 2
    assert "sk-ant-api03-abcdef1234567890abcdef" not in masked
    assert "sk-***MASKED***" in masked
    assert "ghp_***MASKED***" in masked

    text2 = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abcdef1234567890"
    masked2, count2 = session_service.mask_sensitive_text(text2)
    assert count2 >= 1
    assert "***MASKED***" in masked2


def test_parse_timestamp_robust():
    # 1. Linux Codex nanoseconds (9 fractional digits)
    dt1 = session_service.parse_timestamp("2026-08-31T08:56:19.147422875Z")
    assert dt1 is not None
    assert dt1.year == 2026 and dt1.month == 8 and dt1.day == 31
    assert dt1.hour == 8 and dt1.minute == 56

    # 2. Windows Codex ticks (7 fractional digits)
    dt2 = session_service.parse_timestamp("2026-09-01T01:24:06.7029536Z")
    assert dt2 is not None
    assert dt2.year == 2026 and dt2.month == 9 and dt2.day == 1

    # 3. Microseconds (6 digits) and ms (3 digits)
    dt3 = session_service.parse_timestamp("2026-05-23T14:38:56.133067Z")
    assert dt3 is not None
    dt4 = session_service.parse_timestamp("2026-05-27T01:18:30.523Z")
    assert dt4 is not None

    # 4. Epoch milliseconds
    dt5 = session_service.parse_timestamp(1785313262306)
    assert dt5 is not None
    assert dt5.year == 2026

    # 5. PowerShell ASP.NET JSON format
    dt6 = session_service.parse_timestamp("/Date(1785313262306)/")
    assert dt6 is not None
    assert dt6.year == 2026

    # 6. Invalid / None
    assert session_service.parse_timestamp(None) is None
    assert session_service.parse_timestamp("") is None
    assert session_service.parse_timestamp("invalid-date") is None


def test_generate_resume_command():
    s = models.Session(
        agent_id="claude",
        session_key="test-1234",
        project_path="/home/dev/repo",
    )
    cmd_local = session_service.generate_resume_command(s, "local-controller", "local")
    assert cmd_local == "cd '/home/dev/repo' && claude --resume test-1234"

    cmd_remote = session_service.generate_resume_command(s, "linux-srv-01", "ssh")
    assert cmd_remote == 'ssh -t linux-srv-01 "cd \'/home/dev/repo\' && claude --resume test-1234"'

    s_codex = models.Session(
        agent_id="codex",
        session_key="codex-5678",
        project_path="",
    )
    cmd_codex = session_service.generate_resume_command(s_codex, "x13", "ssh")
    assert cmd_codex == 'ssh -t x13 "codex resume codex-5678"'


def test_parse_claude_transcript():
    lines = [
        '{"type":"user","message":{"role":"user","content":[{"type":"text","text":"hello world"}]},"timestamp":"2026-10-18T10:00:00Z"}',
        '{"type":"assistant","message":{"role":"assistant","content":[{"type":"thinking","thinking":"let me think"},{"type":"text","text":"Hi! How can I help?"}]},"timestamp":"2026-10-18T10:00:05Z"}',
    ]
    turns = session_service.parse_raw_transcript_lines("claude", lines)
    assert len(turns) == 2
    assert turns[0]["role"] == "user"
    assert turns[0]["content"] == "hello world"
    assert turns[1]["role"] == "assistant"
    assert turns[1]["thinking"] == "let me think"
    assert turns[1]["content"] == "Hi! How can I help?"


def test_parse_agy_transcript():
    lines = [
        '{"type":"USER_INPUT","source":"USER_INPUT","content":"Fix the build"}',
        '{"type":"PLANNER_RESPONSE","source":"MODEL","thinking":"Investigating issue","content":"I will inspect tests"}',
    ]
    turns = session_service.parse_raw_transcript_lines("agy", lines)
    assert len(turns) == 2
    assert turns[0]["role"] == "user"
    assert turns[0]["content"] == "Fix the build"
    assert turns[1]["role"] == "assistant"
    assert turns[1]["thinking"] == "Investigating issue"
    assert turns[1]["content"] == "I will inspect tests"


@pytest.mark.asyncio
async def test_session_lifecycle_and_listing(fleet_db):
    from agent_fleet.db import session_scope

    with session_scope() as s:
        host = models.Host(name="dev-box", hostname="dev-box.local")
        s.add(host)
        s.flush()
        rt = models.Runtime(
            host_id=host.id,
            key="native",
            kind="linux",
            transport="ssh",
            ssh_host="dev-box.local",
        )
        s.add(rt)
        s.flush()
        ref = session_service.to_ref(rt)

        mock_payload = [
            {
                "session_key": "s-1",
                "agent_id": "claude",
                "title": "Debug memory leak",
                "project_path": "/var/app",
                "project_name": "app",
                "message_count": 5,
                "updated_at": 1776000000000,
            },
            {
                "session_key": "s-2",
                "agent_id": "codex",
                "title": "Refactor database queries",
                "project_path": "/var/db",
                "project_name": "db",
                "message_count": 2,
                "updated_at": 1776001000000,
            },
        ]
        import json
        runner = DummyRunner(stdout="___SESSIONS_JSON___" + json.dumps(mock_payload))

        count = await session_service.scan_sessions_for_runtime(ref, runner, fleet_db, s)
        assert count == 2

        # Query sessions
        res = session_service.list_sessions(s)
        assert res["total"] == 2
        assert len(res["items"]) == 2

        # Filter by agent
        res_claude = session_service.list_sessions(s, agent_id="claude")
        assert res_claude["total"] == 1
        assert res_claude["items"][0]["title"] == "Debug memory leak"

        # Search by keyword
        res_search = session_service.list_sessions(s, q="refactor")
        assert res_search["total"] == 1
        assert res_search["items"][0]["agent_id"] == "codex"

        # Toggle pin
        sid = res["items"][0]["id"]
        pinned = session_service.toggle_pin_session(s, sid)
        assert pinned is True

        res_pinned = session_service.list_sessions(s, pinned_only=True)
        assert res_pinned["total"] == 1
        assert res_pinned["items"][0]["id"] == sid


def test_sessions_api_endpoints(client, fleet_db):
    from agent_fleet.db import session_scope

    with session_scope() as s:
        host = models.Host(name="prod-server", hostname="prod.internal")
        s.add(host)
        s.flush()
        rt = models.Runtime(
            host_id=host.id,
            key="native",
            kind="linux",
            transport="ssh",
            ssh_host="prod.internal",
        )
        s.add(rt)
        s.flush()
        sess = models.Session(
            id="test-session-123",
            runtime_id=rt.id,
            agent_id="claude",
            session_key="key-abc",
            title="Inspect docker logs",
            project_path="/opt/app",
            project_name="app",
            message_count=3,
        )
        s.add(sess)

    # 1. GET /api/sessions
    resp = client.get("/api/sessions")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["id"] == "test-session-123"
    assert "ssh -t prod-server" in data["items"][0]["resume_command"]

    # 2. GET /api/sessions/test-session-123
    resp_one = client.get("/api/sessions/test-session-123")
    assert resp_one.status_code == 200
    assert resp_one.json()["title"] == "Inspect docker logs"

    # 3. POST /api/sessions/test-session-123/pin
    resp_pin = client.post("/api/sessions/test-session-123/pin")
    assert resp_pin.status_code == 200
    assert resp_pin.json()["is_pinned"] is True

    # 4. GET /api/sessions/test-session-123/resume-command
    resp_cmd = client.get("/api/sessions/test-session-123/resume-command")
    assert resp_cmd.status_code == 200
    assert "claude --resume key-abc" in resp_cmd.json()["resume_command"]

    # 5. GET /api/sessions/test-session-123/transcript
    resp_tr = client.get("/api/sessions/test-session-123/transcript")
    assert resp_tr.status_code == 200
    assert len(resp_tr.json()["turns"]) >= 1


@pytest.mark.asyncio
async def test_windows_runtime_is_skipped(fleet_db):
    from agent_fleet.db import session_scope

    with session_scope() as s:
        host = models.Host(name="win-host", hostname="win.internal")
        s.add(host)
        s.flush()
        rt = models.Runtime(
            host_id=host.id,
            key="native",
            kind="windows",
            transport="windows",
        )
        s.add(rt)
        s.flush()
        ref = session_service.to_ref(rt)
        runner = DummyRunner(stdout="___SESSIONS_JSON___[{\"session_key\":\"1\",\"agent_id\":\"codex\"}]")

        count = await session_service.scan_sessions_for_runtime(ref, runner, fleet_db, s)
        assert count == 0


def test_parse_hermes_codebuddy_qoder_transcripts():
    # 1. Hermes
    hermes_lines = [
        json.dumps({"role": "user", "content": "Can you hear me?", "timestamp": "2026-05-23T19:34:47Z"}),
        json.dumps({"role": "assistant", "content": "Yes, I am here!", "timestamp": "2026-05-23T19:34:48Z"}),
    ]
    h_turns = session_service.parse_raw_transcript_lines("hermes", hermes_lines)
    assert len(h_turns) == 2
    assert h_turns[0]["role"] == "user"
    assert h_turns[0]["content"] == "Can you hear me?"
    assert h_turns[1]["role"] == "assistant"

    # 2. CodeBuddy
    cb_lines = [
        json.dumps({"type": "session-meta", "sessionId": "s-123"}),
        json.dumps({
            "type": "message",
            "role": "user",
            "content": [
                {"type": "input_text", "text": "Deploy to staging"},
            ],
            "timestamp": "2026-05-23T19:35:00Z",
        }),
        json.dumps({
            "type": "message",
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "Let me run ansible."},
                {"type": "text", "text": "Deploying now..."},
            ],
            "timestamp": "2026-05-23T19:35:05Z",
        }),
    ]
    cb_turns = session_service.parse_raw_transcript_lines("codebuddy", cb_lines)
    assert len(cb_turns) == 2
    assert cb_turns[0]["role"] == "user"
    assert cb_turns[0]["content"] == "Deploy to staging"
    assert cb_turns[1]["role"] == "assistant"
    assert cb_turns[1]["thinking"] == "Let me run ansible."
    assert cb_turns[1]["content"] == "Deploying now..."

    # 3. Qoder
    q_lines = [
        json.dumps({
            "type": "user",
            "sessionId": "q-1",
            "message": {"role": "user", "content": "Check database index"},
            "timestamp": "2026-05-27T01:18:30Z",
        }),
        json.dumps({
            "type": "assistant",
            "sessionId": "q-1",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "Analyzing postgres queries."},
                    {"type": "text", "text": "Index scan looks healthy."},
                ],
            },
            "timestamp": "2026-05-27T01:18:40Z",
        }),
    ]
    q_turns = session_service.parse_raw_transcript_lines("qoder", q_lines)
    assert len(q_turns) == 2
    assert q_turns[0]["role"] == "user"
    assert q_turns[0]["content"] == "Check database index"
    assert q_turns[1]["role"] == "assistant"
    assert q_turns[1]["thinking"] == "Analyzing postgres queries."
    assert q_turns[1]["content"] == "Index scan looks healthy."


def test_resume_commands_all_agents():
    for agent in ("claude", "codex", "agy", "opencode", "pi", "codebuddy", "hermes", "qoder"):
        sess = models.Session(
            agent_id=agent,
            session_key="test-key",
            project_path="/Users/lynn/test",
        )
        local_cmd = session_service.generate_resume_command(sess, "macbook-air", "local")
        assert f"cd '/Users/lynn/test' && {agent}" in local_cmd
        assert "test-key" in local_cmd

        remote_cmd = session_service.generate_resume_command(sess, "remote-server", "ssh")
        assert remote_cmd.startswith('ssh -t remote-server "')


def test_codebuddy_advanced_types():
    lines = [
        json.dumps({
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "Analyze service latency"}],
            "timestamp": 1785313262000,
        }),
        json.dumps({
            "type": "reasoning",
            "rawContent": [{"type": "reasoning_text", "text": "Thinking about p99 latency spikes."}],
            "timestamp": 1785313263000,
        }),
        json.dumps({
            "type": "function_call",
            "name": "REPL",
            "arguments": {"code": "console.log('ping')"},
            "timestamp": 1785313264000,
        }),
        json.dumps({
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Latency analysis complete: p99 is 42ms."}],
            "timestamp": 1785313265000,
        }),
    ]
    turns = session_service.parse_raw_transcript_lines("codebuddy", lines)
    assert len(turns) == 4
    assert turns[0]["role"] == "user"
    assert turns[0]["content"] == "Analyze service latency"
    assert turns[1]["role"] == "assistant"
    assert turns[1]["thinking"] == "Thinking about p99 latency spikes."
    assert turns[2]["role"] == "tool"
    assert turns[2]["tool_calls"][0]["name"] == "REPL"
    assert turns[3]["role"] == "assistant"
    assert turns[3]["content"] == "Latency analysis complete: p99 is 42ms."


def test_pi_transcript_with_thinking_and_tools():
    lines = [
        json.dumps({
            "type": "message",
            "id": "1",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "Check zoxide configuration"}],
            },
        }),
        json.dumps({
            "type": "message",
            "id": "2",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "Let me inspect the shell setup."},
                    {"type": "text", "text": "Checking zoxide database now."},
                    {"type": "toolCall", "id": "call_1", "name": "bash", "arguments": {"command": "which zoxide"}},
                ],
            },
        }),
        json.dumps({
            "type": "message",
            "id": "3",
            "message": {
                "role": "toolResult",
                "toolCallId": "call_1",
                "toolName": "bash",
                "content": [{"type": "text", "text": "/opt/homebrew/bin/zoxide\n"}],
            },
        }),
    ]
    turns = session_service.parse_raw_transcript_lines("pi", lines)
    assert len(turns) == 3
    assert turns[0]["role"] == "user"
    assert turns[0]["content"] == "Check zoxide configuration"
    assert turns[1]["role"] == "assistant"
    assert turns[1]["thinking"] == "Let me inspect the shell setup."
    assert turns[1]["content"] == "Checking zoxide database now."
    assert turns[1]["tool_calls"][0]["name"] == "bash"
    assert turns[2]["role"] == "tool"
    assert turns[2]["content"] == "/opt/homebrew/bin/zoxide"


def test_codex_rollout_function_call_and_reasoning():
    lines = [
        json.dumps({
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Run automated test suite"}],
            },
        }),
        json.dumps({
            "type": "response_item",
            "payload": {
                "type": "reasoning",
                "summary": ["Reviewing test cases."],
            },
        }),
        json.dumps({
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "bash",
                "arguments": json.dumps({"command": "npm test"}),
                "call_id": "call_abc",
            },
        }),
        json.dumps({
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "output": "12 tests passed",
                "call_id": "call_abc",
            },
        }),
    ]
    turns = session_service.parse_raw_transcript_lines("codex", lines)
    assert len(turns) == 4
    assert turns[0]["role"] == "user"
    assert turns[0]["content"] == "Run automated test suite"
    assert turns[1]["role"] == "assistant"
    assert turns[1]["thinking"] == "Reviewing test cases."
    assert turns[2]["role"] == "tool"
    assert turns[2]["tool_calls"][0]["name"] == "bash"
    assert turns[2]["tool_calls"][0]["input"]["command"] == "npm test"
    assert turns[3]["role"] == "tool"
    assert turns[3]["content"] == "12 tests passed"


def test_clean_transcript_text_removes_xml():
    raw_text = (
        "<USER_REQUEST>\nFix the authentication header\n</USER_REQUEST>\n"
        "<ADDITIONAL_METADATA>some noisy metadata</ADDITIONAL_METADATA>\n"
        "<thought>Internal planning thought</thought>\n"
        "<multi_agent_mode>mode=default</multi_agent_mode>"
    )
    cleaned = session_service.clean_transcript_text(raw_text)
    assert cleaned == "Fix the authentication header"
    assert "<thought>" not in cleaned
    assert "<USER_REQUEST>" not in cleaned
    assert "ADDITIONAL_METADATA" not in cleaned


def test_extract_project_from_slug():
    assert session_service.extract_project_from_slug("Users-lynn-workspace-projects-agent-fleet-webui") == "agent-fleet-webui"
    assert session_service.extract_project_from_slug("Users-lynn-workspace-scratch-computer-playground") == "computer-playground"
    assert session_service.extract_project_from_slug("projects-activate-typora") == "activate-typora"
    assert session_service.extract_project_from_slug("home-dev-workspace-projects-deep-learning-tool") == "deep-learning-tool"
    assert session_service.extract_project_from_slug("home-dev-workspace-my-repo") == "my-repo"
    assert session_service.extract_project_from_slug("workspace-projects-foo-bar") == "foo-bar"
    assert session_service.extract_project_from_slug("workspace-my-app") == "my-app"
    assert session_service.extract_project_from_slug("simple-project") == "simple-project"
    assert session_service.extract_project_from_slug("") == ""
    assert session_service.extract_project_from_slug(None) == ""


def test_is_generic_name_and_path():
    assert session_service.is_generic_name("workspace") is True
    assert session_service.is_generic_name("codex-workspace") is True
    assert session_service.is_generic_name("codebuddy-workspace") is True
    assert session_service.is_generic_name("default") is True
    assert session_service.is_generic_name("projects") is True
    assert session_service.is_generic_name("scratch") is True
    assert session_service.is_generic_name("agent-fleet-webui") is False
    assert session_service.is_generic_name("ChatCodex") is False
    assert session_service.is_generic_name("") is True
    assert session_service.is_generic_name(None) is True

    assert session_service.is_generic_path("/Users/lynn/workspace") is True
    assert session_service.is_generic_path("/Users/lynn/workspace/projects") is True
    assert session_service.is_generic_path("/home/dev/scratch") is True
    assert session_service.is_generic_path("/Users/lynn/workspace/projects/agent-fleet-webui") is False
    assert session_service.is_generic_path(None) is True


def test_aggregate_opencode_parts_with_unicode():
    rows = [
        # Message 1: User message with Chinese characters
        (
            "msg_1",
            1776000000000,
            json.dumps({"role": "user", "time": {"created": 1776000000000}}),
            json.dumps({"type": "text", "text": "你好，请帮我检查并修复构建错误。"}),
        ),
        # Message 2: Assistant message with reasoning, text, tool call, patch, and follow-up text
        (
            "msg_2",
            1776000005000,
            json.dumps({"role": "assistant", "time": {"created": 1776000005000}}),
            json.dumps({"type": "reasoning", "text": "正在检查项目配置文件 package.json 以及构建脚本..."}),
        ),
        (
            "msg_2",
            1776000005000,
            json.dumps({"role": "assistant"}),
            json.dumps({"type": "text", "text": "好的，我开始检查构建配置。"}),
        ),
        (
            "msg_2",
            1776000005000,
            json.dumps({"role": "assistant"}),
            json.dumps({
                "type": "tool",
                "tool": "bash",
                "callID": "call_bash_001",
                "state": {
                    "input": {"command": "npm run build"},
                    "output": "Error: Cannot find module 'vite'",
                },
            }),
        ),
        (
            "msg_2",
            1776000005000,
            json.dumps({"role": "assistant"}),
            json.dumps({
                "type": "patch",
                "hash": "c0ffee1234",
                "files": ["package.json"],
                "diff": "--- a/package.json\n+++ b/package.json\n@@ -10,3 +10,4 @@",
            }),
        ),
        (
            "msg_2",
            1776000005000,
            json.dumps({"role": "assistant"}),
            json.dumps({"type": "text", "text": "修复方案已完成，缺失的依赖已补全。"}),
        ),
    ]

    turns = session_service.aggregate_opencode_parts(rows)
    assert len(turns) == 2

    # Verify user turn
    assert turns[0]["role"] == "user"
    assert turns[0]["content"] == "你好，请帮我检查并修复构建错误。"
    assert turns[0]["timestamp"] == 1776000000000

    # Verify assistant turn
    asst = turns[1]
    assert asst["role"] == "assistant"
    assert asst["thinking"] == "正在检查项目配置文件 package.json 以及构建脚本..."
    assert asst["content"] == "好的，我开始检查构建配置。\n\n修复方案已完成，缺失的依赖已补全。"
    assert asst["timestamp"] == 1776000005000

    assert len(asst["tool_calls"]) == 2
    tool_call = asst["tool_calls"][0]
    assert tool_call["name"] == "bash"
    assert tool_call["id"] == "call_bash_001"
    assert tool_call["input"]["command"] == "npm run build"
    assert tool_call["output"] == "Error: Cannot find module 'vite'"

    patch_call = asst["tool_calls"][1]
    assert patch_call["name"] == "patch"
    assert patch_call["input"]["hash"] == "c0ffee1234"
    assert patch_call["input"]["path"] == "package.json"
    assert "package.json" in patch_call["input"]["files"]
    assert "--- a/package.json" in patch_call["input"]["diff"]

    # Verify UTF-8 serialization with ensure_ascii=False
    serialized = json.dumps(turns, ensure_ascii=False)
    assert "你好，请帮我检查并修复构建错误。" in serialized
    assert "\\u4f60" not in serialized


def test_detect_codex_project_from_rollout(tmp_path):
    rollout_file = tmp_path / "rollout-2026-10-18.jsonl"
    rollout_lines = [
        json.dumps({
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Check status of ChatCodex repository"}],
            },
        }),
        json.dumps({
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "bash",
                "arguments": json.dumps({"command": "cd ChatCodex && git status"}),
            },
        }),
    ]
    rollout_file.write_text("\n".join(rollout_lines), encoding="utf-8")

    name, path = session_service.detect_codex_project_from_rollout(str(rollout_file), base_cwd="/Users/lynn/workspace")
    assert name == "ChatCodex"
    assert name != "codex-workspace"

    # Test detection via file path pattern
    rollout_file2 = tmp_path / "rollout-file-path.jsonl"
    rollout_lines2 = [
        json.dumps({
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "apply_patch",
                "arguments": "*** File: /Users/lynn/workspace/projects/beszel-ui/package.json\n...",
            },
        }),
    ]
    rollout_file2.write_text("\n".join(rollout_lines2), encoding="utf-8")

    name2, path2 = session_service.detect_codex_project_from_rollout(str(rollout_file2), base_cwd="/Users/lynn/workspace")
    assert name2 == "beszel-ui"
    assert name2 != "codex-workspace"

    # Non-existent or empty rollout returns empty, never 'codex-workspace'
    name_empty, path_empty = session_service.detect_codex_project_from_rollout(None, "")
    assert name_empty == ""
    assert name_empty != "codex-workspace"


def test_mask_sensitive_data_nested():
    data = {
        "command": "curl -H 'Authorization: Bearer secret_token_12345678901234567890' https://api.openai.com",
        "env": {
            "API_KEY": "sk-proj-1234567890abcdef1234567890",
            "PORT": 8080,
            "DEBUG": False,
        },
        "headers": [
            "Content-Type: application/json",
            "X-Auth-Token: ghp_1234567890abcdef1234567890",
        ],
        "meta": None,
    }
    masked, count = session_service.mask_sensitive_data(data)
    assert count >= 3
    # Check tokens are masked
    assert "secret_token_12345678901234567890" not in str(masked)
    assert "sk-proj-1234567890abcdef1234567890" not in str(masked)
    assert "ghp_1234567890abcdef1234567890" not in str(masked)
    assert "***MASKED***" in str(masked)
    # Check regular values preserved
    assert masked["env"]["PORT"] == 8080
    assert masked["env"]["DEBUG"] is False
    assert masked["headers"][0] == "Content-Type: application/json"


def test_sanitize_tool_call_and_turn():
    turn = {
        "role": "assistant",
        "content": "Running command to fetch data with ghp_1234567890abcdef1234567890",
        "thinking": "Need to hide my sk-ant-api03-abcdef1234567890abcdef here",
        "tool_calls": [
            {
                "id": "call_1",
                "name": "bash",
                "input": {"cmd": "export GITHUB_TOKEN=ghp_abcdef12345678901234567890 && git push"},
                "output": "Error: Unauthorized for bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abcdef1234567890",
            },
            {
                "id": "call_2",
                "name": "read_file",
                "input": {"path": "/var/log/app.log", "safe": True},
                "output": "status: 200 ok",
            },
        ],
    }

    sanitized, masked_count = session_service.sanitize_turn(turn)
    assert masked_count >= 4
    assert "ghp_1234567890abcdef1234567890" not in sanitized["content"]
    assert "sk-ant-api03-abcdef1234567890abcdef" not in sanitized["thinking"]
    tc1 = sanitized["tool_calls"][0]
    assert "ghp_abcdef12345678901234567890" not in tc1["input"]["cmd"]
    assert "***MASKED***" in tc1["input"]["cmd"]
    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abcdef1234567890" not in tc1["output"]
    assert "***MASKED***" in tc1["output"]

    # Non-sensitive tool params preserved
    tc2 = sanitized["tool_calls"][1]
    assert tc2["input"]["path"] == "/var/log/app.log"
    assert tc2["input"]["safe"] is True
    assert tc2["output"] == "status: 200 ok"


@pytest.mark.asyncio
async def test_cached_transcript_sanitized_on_read(fleet_db):
    from agent_fleet.db import session_scope

    session_id = "test-cached-transcript-sanitize"
    with session_scope() as s:
        host = models.Host(name="dummy-host", hostname="127.0.0.1")
        s.add(host)
        s.flush()
        rt = models.Runtime(host_id=host.id, key="native", kind="linux", transport="local")
        s.add(rt)
        s.flush()
        sess = models.Session(
            id=session_id,
            runtime_id=rt.id,
            agent_id="claude",
            session_key="sess-key-1",
            title="Test Session",
        )
        s.add(sess)
        s.flush()

        # Insert legacy/cached transcript that contains an unmasked secret in tool_calls
        unmasked_turn = {
            "role": "assistant",
            "content": "normal content",
            "tool_calls": [
                {
                    "name": "bash",
                    "input": {"cmd": "curl -H 'Authorization: Bearer raw_leak_secret_12345678901234567890'"},
                    "output": "sk-ant-api03-rawleak12345678901234567890",
                }
            ],
        }
        tr = models.SessionTranscript(
            id=models.new_id(),
            session_id=session_id,
            turns=[unmasked_turn],
            has_secrets_masked=False,
            masked_count=0,
            cached_at=models.utcnow(),
        )
        s.add(tr)
        s.flush()

        # Read via get_session_transcript
        res = await session_service.get_session_transcript(s, session_id)
        assert res["cached"] is True
        assert res["has_secrets_masked"] is True
        assert res["masked_count"] >= 2

        # Check returned turns are sanitized
        res_tc = res["turns"][0]["tool_calls"][0]
        assert "raw_leak_secret_12345678901234567890" not in str(res_tc)
        assert "sk-ant-api03-rawleak12345678901234567890" not in str(res_tc)
        assert "***MASKED***" in str(res_tc)

        # Check database in-place migration
        s.expire_all()
        db_tr = s.query(models.SessionTranscript).filter_by(session_id=session_id).one()
        assert db_tr.has_secrets_masked is True
        assert db_tr.masked_count >= 2
        assert "raw_leak_secret_12345678901234567890" not in str(db_tr.turns)







