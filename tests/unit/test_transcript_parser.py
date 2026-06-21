"""
Tests for TranscriptParser and ClaudeAIParser.

Tests:
1. Parse individual record types (user, assistant, system, summary)
2. Thread records into conversation turns
3. Tool chain resolution (tool_use → tool_result)
4. Session statistics
5. SessionIndex discovery
6. Claude.ai export parsing
7. Edge cases (empty files, malformed records, sidechains)
"""

import json
from pathlib import Path

from empirica.core.canonical.transcript_parser import (
    ClaudeAIParser,
    ContentBlockType,
    RecordType,
    SessionIndex,
    TranscriptParser,
)

# --- Fixtures ---


def make_user_record(
    content: str,
    uuid: str = "u1",
    timestamp: str = "2026-03-24T10:00:00Z",
    parent_uuid=None,
    session_id: str = "sess1",
    sidechain: bool = False,
) -> dict:
    """Create a user record matching Claude Code .jsonl format."""
    return {
        "type": "user",
        "uuid": uuid,
        "timestamp": timestamp,
        "parentUuid": parent_uuid,
        "sessionId": session_id,
        "isSidechain": sidechain,
        "gitBranch": "main",
        "cwd": "/home/user/project",
        "message": {
            "role": "user",
            "content": content,
        },
    }


def make_assistant_record(
    text_blocks: list[str] | None = None,
    tool_uses: list[dict] | None = None,
    thinking: str = "",
    uuid: str = "a1",
    timestamp: str = "2026-03-24T10:00:01Z",
    parent_uuid: str = "u1",
    session_id: str = "sess1",
    model: str = "claude-opus-4-6",
    sidechain: bool = False,
) -> dict:
    """Create an assistant record matching Claude Code .jsonl format."""
    content = []
    if thinking:
        content.append({"type": "thinking", "thinking": thinking, "signature": "sig123"})
    for text in text_blocks or []:
        content.append({"type": "text", "text": text})
    for tool in tool_uses or []:
        content.append(
            {
                "type": "tool_use",
                "id": tool.get("id", "tool1"),
                "name": tool.get("name", "Bash"),
                "input": tool.get("input", {}),
            }
        )

    return {
        "type": "assistant",
        "uuid": uuid,
        "timestamp": timestamp,
        "parentUuid": parent_uuid,
        "sessionId": session_id,
        "isSidechain": sidechain,
        "gitBranch": "main",
        "cwd": "/home/user/project",
        "message": {
            "model": model,
            "id": f"msg_{uuid}",
            "type": "message",
            "role": "assistant",
            "content": content,
            "stop_reason": "end_turn",
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
            },
        },
    }


def make_tool_result_user_record(
    tool_use_id: str,
    result_content: str,
    uuid: str = "u2",
    timestamp: str = "2026-03-24T10:00:02Z",
    session_id: str = "sess1",
) -> dict:
    """Create a user record containing a tool result."""
    return {
        "type": "user",
        "uuid": uuid,
        "timestamp": timestamp,
        "parentUuid": None,
        "sessionId": session_id,
        "isSidechain": False,
        "gitBranch": "main",
        "cwd": "/home/user/project",
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": result_content,
                }
            ],
        },
    }


def make_system_record(
    subtype: str = "compact_boundary",
    content: str = "Conversation compacted",
    uuid: str = "s1",
    timestamp: str = "2026-03-24T10:05:00Z",
    session_id: str = "sess1",
) -> dict:
    return {
        "type": "system",
        "uuid": uuid,
        "timestamp": timestamp,
        "sessionId": session_id,
        "isSidechain": False,
        "subtype": subtype,
        "content": content,
        "level": "info",
    }


def make_progress_record(uuid: str = "p1", timestamp: str = "2026-03-24T10:00:00Z") -> dict:
    return {
        "type": "progress",
        "uuid": uuid,
        "timestamp": timestamp,
        "sessionId": "sess1",
        "isSidechain": False,
        "data": {"type": "hook_progress", "hookEvent": "PreToolUse"},
    }


def write_jsonl(path: Path, records: list):
    """Write records to a .jsonl file."""
    with open(path, "w") as f:
        for record in records:
            f.write(json.dumps(record) + "\n")


# --- TranscriptParser Tests ---


class TestRecordParsing:
    """Test parsing individual record types."""

    def test_parse_user_record(self):
        parser = TranscriptParser()
        records = [make_user_record("Hello, help me fix a bug")]
        parsed = parser._parse_record(records[0])

        assert parsed is not None
        assert parsed.record_type == RecordType.USER
        assert parsed.role == "user"
        assert parsed.raw_content == "Hello, help me fix a bug"
        assert parsed.uuid == "u1"

    def test_parse_assistant_record_text(self):
        parser = TranscriptParser()
        record = make_assistant_record(text_blocks=["I'll help you fix that bug."])
        parsed = parser._parse_record(record)

        assert parsed.record_type == RecordType.ASSISTANT
        assert parsed.role == "assistant"
        assert parsed.model == "claude-opus-4-6"
        assert len(parsed.content_blocks) == 1
        assert parsed.content_blocks[0].block_type == ContentBlockType.TEXT
        assert "fix that bug" in parsed.content_blocks[0].text

    def test_parse_assistant_record_with_thinking(self):
        parser = TranscriptParser()
        record = make_assistant_record(
            text_blocks=["Let me check."],
            thinking="The user wants me to investigate the auth module.",
        )
        parsed = parser._parse_record(record)

        assert len(parsed.content_blocks) == 2
        thinking_blocks = [b for b in parsed.content_blocks if b.block_type == ContentBlockType.THINKING]
        assert len(thinking_blocks) == 1
        assert "auth module" in thinking_blocks[0].thinking

    def test_parse_assistant_record_with_tool_use(self):
        parser = TranscriptParser()
        record = make_assistant_record(
            text_blocks=["Let me read the file."],
            tool_uses=[{"id": "tool_123", "name": "Read", "input": {"file_path": "/src/auth.py"}}],
        )
        parsed = parser._parse_record(record)

        tool_blocks = [b for b in parsed.content_blocks if b.block_type == ContentBlockType.TOOL_USE]
        assert len(tool_blocks) == 1
        assert tool_blocks[0].tool_name == "Read"
        assert tool_blocks[0].tool_use_id == "tool_123"
        assert tool_blocks[0].tool_input["file_path"] == "/src/auth.py"

    def test_parse_system_record(self):
        parser = TranscriptParser()
        record = make_system_record(subtype="compact_boundary")
        parsed = parser._parse_record(record)

        assert parsed.record_type == RecordType.SYSTEM
        assert parsed.subtype == "compact_boundary"

    def test_progress_records_filtered(self):
        parser = TranscriptParser()
        record = make_progress_record()
        parsed = parser._parse_record(record)

        assert parsed is None  # Progress records are filtered out

    def test_parse_token_usage(self):
        parser = TranscriptParser()
        record = make_assistant_record(text_blocks=["Response"])
        parsed = parser._parse_record(record)

        assert parsed.input_tokens == 100
        assert parsed.output_tokens == 50

    def test_parse_user_record_with_tool_result(self):
        parser = TranscriptParser()
        record = make_tool_result_user_record("tool_123", "file contents here")
        parsed = parser._parse_record(record)

        assert parsed.record_type == RecordType.USER
        assert len(parsed.content_blocks) == 1
        assert parsed.content_blocks[0].block_type == ContentBlockType.TOOL_RESULT
        assert parsed.content_blocks[0].tool_use_id == "tool_123"


class TestSessionParsing:
    """Test parsing complete session files."""

    def test_parse_session_file(self, tmp_path):
        parser = TranscriptParser()

        records = [
            make_user_record("Fix the login bug", uuid="u1", timestamp="2026-03-24T10:00:00Z"),
            make_assistant_record(
                text_blocks=["I'll investigate the login module."],
                uuid="a1",
                timestamp="2026-03-24T10:00:01Z",
            ),
        ]

        jsonl_file = tmp_path / "test-session.jsonl"
        write_jsonl(jsonl_file, records)

        parsed = parser.parse_session(str(jsonl_file))
        assert len(parsed) == 2
        assert parsed[0].record_type == RecordType.USER
        assert parsed[1].record_type == RecordType.ASSISTANT

    def test_parse_session_nonexistent_file(self):
        parser = TranscriptParser()
        parsed = parser.parse_session("/nonexistent/path.jsonl")
        assert parsed == []

    def test_parse_session_with_malformed_lines(self, tmp_path):
        jsonl_file = tmp_path / "test.jsonl"
        with open(jsonl_file, "w") as f:
            f.write(json.dumps(make_user_record("valid message")) + "\n")
            f.write("this is not json\n")
            f.write(json.dumps(make_assistant_record(text_blocks=["response"])) + "\n")

        parser = TranscriptParser()
        parsed = parser.parse_session(str(jsonl_file))
        assert len(parsed) == 2  # Skips malformed line

    def test_records_sorted_by_timestamp(self, tmp_path):
        parser = TranscriptParser()

        records = [
            make_assistant_record(text_blocks=["Second"], uuid="a1", timestamp="2026-03-24T10:01:00Z"),
            make_user_record("First", uuid="u1", timestamp="2026-03-24T10:00:00Z"),
        ]

        jsonl_file = tmp_path / "test.jsonl"
        write_jsonl(jsonl_file, records)

        parsed = parser.parse_session(str(jsonl_file))
        assert parsed[0].record_type == RecordType.USER
        assert parsed[1].record_type == RecordType.ASSISTANT


class TestConversationTurns:
    """Test threading records into conversation turns."""

    def test_simple_turn(self, tmp_path):
        parser = TranscriptParser()

        records = [
            make_user_record("What's the architecture?", uuid="u1", timestamp="2026-03-24T10:00:00Z"),
            make_assistant_record(
                text_blocks=["The system uses a layered architecture."],
                uuid="a1",
                timestamp="2026-03-24T10:00:01Z",
            ),
        ]

        jsonl_file = tmp_path / "test.jsonl"
        write_jsonl(jsonl_file, records)

        parsed = parser.parse_session(str(jsonl_file))
        turns = list(parser.iter_conversation_turns(parsed))

        assert len(turns) == 1
        assert turns[0].user_message == "What's the architecture?"
        assert "layered architecture" in turns[0].assistant_text

    def test_multiple_turns(self, tmp_path):
        parser = TranscriptParser()

        records = [
            make_user_record("Question 1", uuid="u1", timestamp="2026-03-24T10:00:00Z"),
            make_assistant_record(text_blocks=["Answer 1"], uuid="a1", timestamp="2026-03-24T10:00:01Z"),
            make_user_record("Question 2", uuid="u2", timestamp="2026-03-24T10:01:00Z"),
            make_assistant_record(text_blocks=["Answer 2"], uuid="a2", timestamp="2026-03-24T10:01:01Z"),
        ]

        jsonl_file = tmp_path / "test.jsonl"
        write_jsonl(jsonl_file, records)

        parsed = parser.parse_session(str(jsonl_file))
        turns = list(parser.iter_conversation_turns(parsed))

        assert len(turns) == 2
        assert turns[0].turn_index == 0
        assert turns[1].turn_index == 1
        assert turns[0].user_message == "Question 1"
        assert turns[1].user_message == "Question 2"

    def test_turn_with_tool_chain(self, tmp_path):
        parser = TranscriptParser()

        records = [
            make_user_record("Read the config file", uuid="u1", timestamp="2026-03-24T10:00:00Z"),
            make_assistant_record(
                text_blocks=["I'll read the config."],
                tool_uses=[{"id": "tool_abc", "name": "Read", "input": {"file_path": "config.yaml"}}],
                uuid="a1",
                timestamp="2026-03-24T10:00:01Z",
            ),
            make_tool_result_user_record("tool_abc", "key: value", uuid="u2", timestamp="2026-03-24T10:00:02Z"),
            make_assistant_record(
                text_blocks=["The config contains key: value."],
                uuid="a2",
                timestamp="2026-03-24T10:00:03Z",
            ),
        ]

        jsonl_file = tmp_path / "test.jsonl"
        write_jsonl(jsonl_file, records)

        parsed = parser.parse_session(str(jsonl_file))
        turns = list(parser.iter_conversation_turns(parsed))

        # First turn should have the tool chain
        assert len(turns[0].tool_chains) == 1
        assert turns[0].tool_chains[0].tool_name == "Read"
        assert turns[0].tool_chains[0].result_content == "key: value"

    def test_turn_with_thinking(self, tmp_path):
        parser = TranscriptParser()

        records = [
            make_user_record("How does auth work?", uuid="u1", timestamp="2026-03-24T10:00:00Z"),
            make_assistant_record(
                text_blocks=["Auth uses JWT tokens."],
                thinking="The user is asking about the authentication system. I should check the middleware.",
                uuid="a1",
                timestamp="2026-03-24T10:00:01Z",
            ),
        ]

        jsonl_file = tmp_path / "test.jsonl"
        write_jsonl(jsonl_file, records)

        parsed = parser.parse_session(str(jsonl_file))
        turns = list(parser.iter_conversation_turns(parsed))

        assert turns[0].thinking != ""
        assert "middleware" in turns[0].thinking

    def test_sidechains_excluded_by_default(self, tmp_path):
        parser = TranscriptParser()

        records = [
            make_user_record("Main question", uuid="u1", timestamp="2026-03-24T10:00:00Z"),
            make_assistant_record(text_blocks=["Main answer"], uuid="a1", timestamp="2026-03-24T10:00:01Z"),
            make_user_record("Subagent prompt", uuid="u2", timestamp="2026-03-24T10:00:02Z", sidechain=True),
            make_assistant_record(
                text_blocks=["Subagent response"], uuid="a2", timestamp="2026-03-24T10:00:03Z", sidechain=True
            ),
        ]

        jsonl_file = tmp_path / "test.jsonl"
        write_jsonl(jsonl_file, records)

        parsed = parser.parse_session(str(jsonl_file))
        turns = list(parser.iter_conversation_turns(parsed, include_sidechains=False))

        assert len(turns) == 1
        assert turns[0].user_message == "Main question"

    def test_sidechains_included_when_requested(self, tmp_path):
        parser = TranscriptParser()

        records = [
            make_user_record("Main question", uuid="u1", timestamp="2026-03-24T10:00:00Z"),
            make_assistant_record(text_blocks=["Main answer"], uuid="a1", timestamp="2026-03-24T10:00:01Z"),
            make_user_record("Subagent prompt", uuid="u2", timestamp="2026-03-24T10:00:02Z", sidechain=True),
            make_assistant_record(
                text_blocks=["Subagent response"], uuid="a2", timestamp="2026-03-24T10:00:03Z", sidechain=True
            ),
        ]

        jsonl_file = tmp_path / "test.jsonl"
        write_jsonl(jsonl_file, records)

        parsed = parser.parse_session(str(jsonl_file))
        turns = list(parser.iter_conversation_turns(parsed, include_sidechains=True))

        assert len(turns) == 2

    def test_compact_boundary_detected_in_turn(self, tmp_path):
        parser = TranscriptParser()

        records = [
            make_user_record("Question", uuid="u1", timestamp="2026-03-24T10:00:00Z"),
            make_assistant_record(text_blocks=["Answer"], uuid="a1", timestamp="2026-03-24T10:00:01Z"),
            make_system_record(subtype="compact_boundary", uuid="s1", timestamp="2026-03-24T10:02:00Z"),
            make_user_record("Post-compact question", uuid="u2", timestamp="2026-03-24T10:03:00Z"),
            make_assistant_record(text_blocks=["Post-compact answer"], uuid="a2", timestamp="2026-03-24T10:03:01Z"),
        ]

        jsonl_file = tmp_path / "test.jsonl"
        write_jsonl(jsonl_file, records)

        parsed = parser.parse_session(str(jsonl_file))
        turns = list(parser.iter_conversation_turns(parsed))

        assert len(turns) == 2
        # The compact boundary falls after the first assistant reply,
        # so it's part of the first turn's context
        assert turns[0].compact_occurred is True


class TestSessionStats:
    """Test session statistics computation."""

    def test_basic_stats(self, tmp_path):
        parser = TranscriptParser()

        records = [
            make_user_record("Q1", uuid="u1", timestamp="2026-03-24T10:00:00Z"),
            make_assistant_record(text_blocks=["A1"], uuid="a1", timestamp="2026-03-24T10:01:00Z"),
            make_user_record("Q2", uuid="u2", timestamp="2026-03-24T10:10:00Z"),
            make_assistant_record(
                text_blocks=["A2"],
                tool_uses=[{"id": "t1", "name": "Bash", "input": {"command": "ls"}}],
                uuid="a2",
                timestamp="2026-03-24T10:11:00Z",
            ),
        ]

        jsonl_file = tmp_path / "test.jsonl"
        write_jsonl(jsonl_file, records)

        parsed = parser.parse_session(str(jsonl_file))
        stats = parser.session_stats(parsed)

        assert stats["user_messages"] == 2
        assert stats["assistant_messages"] == 2
        assert stats["tools_used"]["Bash"] == 1
        assert stats["duration_minutes"] == 11.0
        assert stats["models"] == ["claude-opus-4-6"]


class TestSessionIndex:
    """Test session index discovery."""

    def test_get_sessions(self, tmp_path):
        # Create mock .claude structure
        project_dir = tmp_path / ".claude" / "projects" / "test-project"
        project_dir.mkdir(parents=True)

        index = {
            "version": 1,
            "entries": [
                {
                    "sessionId": "sess-001",
                    "fullPath": str(project_dir / "sess-001.jsonl"),
                    "firstPrompt": "Fix the bug",
                    "summary": "Bug fixing session",
                    "messageCount": 10,
                    "created": "2026-03-24T10:00:00Z",
                    "modified": "2026-03-24T11:00:00Z",
                    "gitBranch": "main",
                    "projectPath": "/home/user/project",
                    "isSidechain": False,
                },
            ],
        }

        (project_dir / "sessions-index.json").write_text(json.dumps(index))

        session_index = SessionIndex(str(tmp_path / ".claude"))
        sessions = session_index.get_sessions("test-project")

        assert len(sessions) == 1
        assert sessions[0].session_id == "sess-001"
        assert sessions[0].summary == "Bug fixing session"
        assert sessions[0].message_count == 10

    def test_discover_projects(self, tmp_path):
        projects_dir = tmp_path / ".claude" / "projects"
        (projects_dir / "project-a").mkdir(parents=True)
        (projects_dir / "project-b").mkdir(parents=True)

        session_index = SessionIndex(str(tmp_path / ".claude"))
        projects = session_index.discover_projects()

        assert "project-a" in projects
        assert "project-b" in projects


class TestClaudeAIParser:
    """Test Claude.ai export parsing."""

    def test_parse_simple_conversation(self, tmp_path):
        export = [
            {
                "chat_messages": [
                    {"sender": "human", "text": "What is Python?", "created_at": "2026-03-20T10:00:00Z"},
                    {
                        "sender": "assistant",
                        "text": "Python is a programming language.",
                        "created_at": "2026-03-20T10:00:05Z",
                    },
                ]
            }
        ]

        export_file = tmp_path / "export.json"
        export_file.write_text(json.dumps(export))

        parser = ClaudeAIParser()
        turns, metadata = parser.parse_export(str(export_file))

        assert len(turns) == 1
        assert turns[0].user_message == "What is Python?"
        assert "programming language" in turns[0].assistant_text
        assert metadata["source"] == "claude.ai"
        assert metadata["conversation_count"] == 1

    def test_parse_multi_turn_conversation(self, tmp_path):
        export = [
            {
                "chat_messages": [
                    {"sender": "human", "text": "Q1"},
                    {"sender": "assistant", "text": "A1"},
                    {"sender": "human", "text": "Q2"},
                    {"sender": "assistant", "text": "A2"},
                ]
            }
        ]

        export_file = tmp_path / "export.json"
        export_file.write_text(json.dumps(export))

        parser = ClaudeAIParser()
        turns, _ = parser.parse_export(str(export_file))

        assert len(turns) == 2
        assert turns[0].user_message == "Q1"
        assert turns[1].user_message == "Q2"

    def test_parse_multiple_conversations(self, tmp_path):
        export = [
            {
                "chat_messages": [
                    {"sender": "human", "text": "Conv1 Q1"},
                    {"sender": "assistant", "text": "Conv1 A1"},
                ]
            },
            {
                "chat_messages": [
                    {"sender": "human", "text": "Conv2 Q1"},
                    {"sender": "assistant", "text": "Conv2 A1"},
                ]
            },
        ]

        export_file = tmp_path / "export.json"
        export_file.write_text(json.dumps(export))

        parser = ClaudeAIParser()
        turns, metadata = parser.parse_export(str(export_file))

        assert len(turns) == 2
        assert metadata["conversation_count"] == 2

    def test_parse_nonexistent_file(self):
        parser = ClaudeAIParser()
        turns, metadata = parser.parse_export("/nonexistent/export.json")
        assert turns == []
        assert metadata == {}

    def test_parse_with_content_blocks(self, tmp_path):
        """Test parsing when messages use content block format."""
        export = [
            {
                "chat_messages": [
                    {"sender": "human", "content": [{"type": "text", "text": "Block format Q"}]},
                    {"sender": "assistant", "content": [{"type": "text", "text": "Block format A"}]},
                ]
            }
        ]

        export_file = tmp_path / "export.json"
        export_file.write_text(json.dumps(export))

        parser = ClaudeAIParser()
        turns, _ = parser.parse_export(str(export_file))

        assert len(turns) == 1
        assert turns[0].user_message == "Block format Q"
        assert turns[0].assistant_text == "Block format A"

    def test_parse_wrapper_format(self, tmp_path):
        """Test parsing when export is wrapped in a conversations key."""
        export = {
            "conversations": [
                {
                    "chat_messages": [
                        {"sender": "human", "text": "Wrapped Q"},
                        {"sender": "assistant", "text": "Wrapped A"},
                    ]
                }
            ]
        }

        export_file = tmp_path / "export.json"
        export_file.write_text(json.dumps(export))

        parser = ClaudeAIParser()
        turns, _metadata = parser.parse_export(str(export_file))

        assert len(turns) == 1
        assert turns[0].user_message == "Wrapped Q"
