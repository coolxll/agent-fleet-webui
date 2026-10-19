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

    cmd_remote = session_service.generate_resume_command(s, "corp172-dev", "ssh")
    assert cmd_remote == 'ssh -t corp172-dev "cd \'/home/dev/repo\' && claude --resume test-1234"'

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





