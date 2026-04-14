import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from backend.hub import Agent, MessageGuard


class MessageGuardTests(unittest.TestCase):
    def setUp(self):
        self.guard = MessageGuard(
            min_interval_seconds=1.0,
            duplicate_window_seconds=60.0,
            room_echo_sender_threshold=1,
            burst_limit_count=4,
            burst_window_seconds=8.0,
        )
        self.room_id = "room-1"
        self.a1 = Agent(id="a1", name="Codex", type="codex")
        self.a2 = Agent(id="a2", name="Claude", type="claude")
        self.maps = Agent(id="m1", name="maps", type="human")

    def test_cooldown_blocks_rapid_agent_replies(self):
        first = self.guard.evaluate(self.room_id, self.a1, "ready", now_mono=0.0)
        self.assertTrue(first.allowed)
        self.guard.record(self.room_id, self.a1, "ready", now_mono=0.0)

        second = self.guard.evaluate(self.room_id, self.a1, "new message", now_mono=0.2)
        self.assertFalse(second.allowed)
        self.assertEqual("cooldown", second.reason)

    def test_echo_loop_blocks_cross_agent_duplicate_content(self):
        self.guard.record(self.room_id, self.a1, "silent standby", now_mono=0.0)
        decision = self.guard.evaluate(self.room_id, self.a2, "silent standby", now_mono=2.0)
        self.assertFalse(decision.allowed)
        self.assertEqual("echo_loop", decision.reason)

    def test_human_messages_bypass_guard(self):
        self.guard.record(self.room_id, self.a1, "silent standby", now_mono=0.0)
        decision = self.guard.evaluate(self.room_id, self.maps, "silent standby", now_mono=0.1)
        self.assertTrue(decision.allowed)


if __name__ == "__main__":
    unittest.main()
