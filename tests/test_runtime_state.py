import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from runtime_state import (
    clear_agent_watch_entry,
    clear_agent_session_entry,
    clear_temporary_agent_enablements,
    get_agent_watch_entry,
    get_or_allocate_agent_session_title,
    get_agent_session_entry,
    get_temporary_agent_enablements,
    make_agent_session_key,
    mark_temporary_agent_enablements,
    normalize_watch_scope,
    update_agent_watch_entry,
    update_agent_session_entry,
)


class RuntimeStateTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "polycule.db"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_session_registry_round_trip(self):
        key = make_agent_session_key("hermes", "Default", profile="wizard")
        update_agent_session_entry(
            key,
            db_path=self.db_path,
            session_id="sess-123",
            last_message_id="msg-9",
            room="Default",
            profile="wizard",
        )

        entry = get_agent_session_entry(key, db_path=self.db_path)
        self.assertIsNotNone(entry)
        self.assertEqual("sess-123", entry["session_id"])
        self.assertEqual("msg-9", entry["last_message_id"])
        self.assertEqual("Default", entry["room"])

        clear_agent_session_entry(key, db_path=self.db_path)
        self.assertIsNone(get_agent_session_entry(key, db_path=self.db_path))

    def test_title_allocator_reuses_stored_title(self):
        key = make_agent_session_key("claude", "Default")
        update_agent_session_entry(
            key,
            db_path=self.db_path,
            title="chat 3",
            room="Default",
            profile="claude",
        )

        self.assertEqual(
            "chat 3",
            get_or_allocate_agent_session_title(key, db_path=self.db_path),
        )

    def test_title_allocator_skips_taken_titles(self):
        key_a = make_agent_session_key("codex", "Default")
        key_b = make_agent_session_key("claude", "Default")
        key_c = make_agent_session_key("gemini", "Default")
        update_agent_session_entry(key_a, db_path=self.db_path, title="chat")
        update_agent_session_entry(key_b, db_path=self.db_path, title="chat 2")
        update_agent_session_entry(key_c, db_path=self.db_path, title="polycule:legacy")

        self.assertEqual(
            "chat 3",
            get_or_allocate_agent_session_title(key_c, db_path=self.db_path),
        )

    def test_watch_registry_round_trip(self):
        update_agent_watch_entry(
            "wizard",
            "Default",
            db_path=self.db_path,
            scope="human",
            updated_by="maps",
        )
        entry = get_agent_watch_entry("wizard", "Default", db_path=self.db_path)
        self.assertIsNotNone(entry)
        self.assertEqual("human", entry["scope"])
        self.assertEqual("", entry["target"])

        clear_agent_watch_entry("wizard", "Default", db_path=self.db_path)
        self.assertIsNone(
            get_agent_watch_entry("wizard", "Default", db_path=self.db_path)
        )

    def test_normalize_watch_scope_supports_agent_targets(self):
        self.assertEqual(("agent", "codex"), normalize_watch_scope("@codex"))
        self.assertEqual(("agent", "wizard"), normalize_watch_scope("agent", "@wizard"))
        self.assertEqual(("room", ""), normalize_watch_scope("room"))

    def test_temporary_enablements_round_trip(self):
        mark_temporary_agent_enablements(
            "Default",
            ["claude", "gemini"],
            db_path=self.db_path,
            enabled_by="maps",
            reason="brief",
        )
        entries = get_temporary_agent_enablements("Default", db_path=self.db_path)
        self.assertEqual({"claude", "gemini"}, set(entries.keys()))
        self.assertEqual("brief", entries["claude"]["reason"])

        clear_temporary_agent_enablements(
            "Default",
            db_path=self.db_path,
            agent_names=["claude"],
        )
        entries = get_temporary_agent_enablements("Default", db_path=self.db_path)
        self.assertEqual({"gemini"}, set(entries.keys()))


if __name__ == "__main__":
    unittest.main()
