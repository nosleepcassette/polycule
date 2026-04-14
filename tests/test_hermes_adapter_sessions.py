import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agents.hermes_adapter import HermesAdapter


class HermesAdapterSessionTests(unittest.TestCase):
    def test_sanitize_output_strips_resume_banner_and_session_id(self):
        cleaned, meta = HermesAdapter._sanitize_hermes_output(
            '↻ Resumed session 20260409_085140_4a6467 "chat"\n'
            '(3 user messages, 10 total messages)\n'
            'RESUME_OK\n'
            '\n'
            'session_id: 20260409_085140_4a6467\n'
        )

        self.assertEqual("RESUME_OK", cleaned)
        self.assertEqual("20260409_085140_4a6467", meta["session_id"])
        self.assertEqual("chat", meta["session_title"])
        self.assertTrue(meta["resumed"])

    @patch("agents.hermes_adapter.get_agent_session_entry")
    @patch("agents.hermes_adapter.hermes_session_exists")
    def test_init_loads_stored_session_from_registry(self, mock_exists, mock_entry):
        mock_exists.return_value = True
        mock_entry.return_value = {
            "session_id": "sess-123",
            "last_message_id": "msg-2",
        }

        adapter = HermesAdapter(name="wizard", profile="wizard", room="Default")

        self.assertEqual("sess-123", adapter.resume_session)
        self.assertEqual("msg-2", adapter.last_acknowledged_message_id)

    @patch("agents.hermes_adapter.get_agent_session_entry", return_value=None)
    def test_prompt_uses_delta_context_when_resuming(self, _mock_entry):
        adapter = HermesAdapter(name="cassette", profile="cassette", room="Default")
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
