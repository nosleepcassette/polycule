import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agents.claude_adapter import ClaudeAdapter
from agents.codex_adapter import CodexAdapter
from agents.gemini_adapter import GeminiAdapter
from agents.opencode_adapter import OpenCodeAdapter


class AgentResumeAdapterTests(unittest.TestCase):
    @patch("agents.codex_adapter.get_agent_session_entry")
    @patch("agents.codex_adapter.codex_session_exists")
    def test_codex_init_loads_saved_session(self, mock_exists, mock_entry):
        mock_exists.return_value = True
        mock_entry.return_value = {
            "session_id": "codex-sess",
            "last_message_id": "msg-7",
        }

        adapter = CodexAdapter(name="codex", room="Default")

        self.assertEqual("codex-sess", adapter.resume_session)
        self.assertEqual("msg-7", adapter.last_acknowledged_message_id)

    def test_codex_resume_command_shape(self):
        with patch("agents.codex_adapter.get_agent_session_entry", return_value=None):
            adapter = CodexAdapter(name="codex", room="Default", resume_session="sess-1")
        self.assertEqual(
            [adapter._bin, "exec", "resume", "sess-1", "ping"],
            adapter._build_cmd("ping"),
        )

    @patch("agents.claude_adapter.get_agent_session_entry")
    @patch("agents.claude_adapter.claude_session_exists")
    def test_claude_init_loads_saved_session(self, mock_exists, mock_entry):
        mock_exists.return_value = True
        mock_entry.return_value = {
            "session_id": "claude-sess",
            "last_message_id": "msg-8",
        }

        adapter = ClaudeAdapter(name="claude", room="Default")

        self.assertEqual("claude-sess", adapter.resume_session)
        self.assertEqual("msg-8", adapter.last_acknowledged_message_id)

    def test_claude_resume_command_shape(self):
        with patch("agents.claude_adapter.get_agent_session_entry", return_value=None):
            adapter = ClaudeAdapter(name="claude", room="Default", resume_session="sess-2")
        self.assertEqual(
            ["claude", "-p", "--model", adapter.model, "-r", "sess-2", "ping"],
            adapter._build_cmd("ping"),
        )

    @patch("agents.opencode_adapter.get_agent_session_entry")
    @patch("agents.opencode_adapter.opencode_session_exists")
    def test_opencode_init_loads_saved_session(self, mock_exists, mock_entry):
        mock_exists.return_value = True
        mock_entry.return_value = {
            "session_id": "opencode-sess",
            "last_message_id": "msg-9",
        }

        adapter = OpenCodeAdapter(name="opencode", room="Default")

        self.assertEqual("opencode-sess", adapter.resume_session)
        self.assertEqual("msg-9", adapter.last_acknowledged_message_id)

    def test_opencode_resume_command_shape(self):
        with patch("agents.opencode_adapter.get_agent_session_entry", return_value=None):
            adapter = OpenCodeAdapter(name="opencode", room="Default", resume_session="sess-3")
        self.assertEqual(
            ["opencode", "run", "--session", "sess-3", "ping"],
            adapter._build_cmd("ping"),
        )

    @patch("agents.gemini_adapter.get_agent_session_entry")
    @patch("agents.gemini_adapter.gemini_session_exists")
    def test_gemini_init_loads_saved_session(self, mock_exists, mock_entry):
        mock_exists.return_value = True
        mock_entry.return_value = {
            "session_id": "gemini-sess",
            "last_message_id": "msg-10",
        }

        adapter = GeminiAdapter(name="gemini", room="Default")

        self.assertEqual("gemini-sess", adapter.resume_session)
        self.assertEqual("msg-10", adapter.last_acknowledged_message_id)

    def test_gemini_resume_command_shape(self):
        with patch("agents.gemini_adapter.get_agent_session_entry", return_value=None):
            adapter = GeminiAdapter(name="gemini", room="Default", resume_session="sess-4")
        self.assertEqual(
            ["gemini", "-m", adapter.model, "-r", "sess-4", "-p", "ping", "-o", "json"],
            adapter._build_cmd("ping"),
        )

    @patch("agents.codex_adapter.get_agent_session_entry", return_value=None)
    def test_codex_prompt_uses_delta_context_when_resuming(self, _mock_entry):
        adapter = CodexAdapter(name="codex", room="Default")
        adapter.resume_session = "sess-123"
        adapter.last_acknowledged_message_id = "msg-2"
        adapter.context_messages = [
            {
                "id": "msg-1",
                "type": "message",
                "sender": {"name": "maps", "type": "human"},
                "content": "old-alpha",
            },
            {
                "id": "msg-2",
                "type": "message",
                "sender": {"name": "maps", "type": "human"},
                "content": "old-beta",
            },
            {
                "id": "msg-3",
                "type": "message",
                "sender": {"name": "wizard", "type": "hermes"},
                "content": "new-gamma",
            },
            {
                "id": "msg-4",
                "type": "message",
                "sender": {"name": "maps", "type": "human"},
                "content": "new-delta",
            },
        ]

        prompt = adapter._build_prompt(adapter.context_messages[-1])

        self.assertIn("New chat messages since your last handled turn", prompt)
        self.assertIn("new-gamma", prompt)
        self.assertIn("new-delta", prompt)
        self.assertNotIn("old-alpha", prompt)
        self.assertNotIn("old-beta", prompt)


if __name__ == "__main__":
    unittest.main()
