import asyncio
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ui.chat_tui import ChatTUI


class ChatTUIReconnectTests(unittest.IsolatedAsyncioTestCase):
    async def test_reconnect_loop_attempts_until_connected(self):
        tui = ChatTUI(name="maps", room="Demo", host="localhost", port=7777)
        tui._reconnect_base_delay = 0.01
        tui._reconnect_max_delay = 0.02

        attempts = {"count": 0}

        async def fake_connect(reconnecting: bool = False):
            attempts["count"] += 1
            tui.connected = attempts["count"] >= 2
            return tui.connected

        tui.connect = fake_connect  # monkeypatch instance method for deterministic test
        tui._schedule_reconnect()

        await asyncio.sleep(0.12)

        self.assertTrue(tui.connected)
        self.assertGreaterEqual(attempts["count"], 2)

        await tui.shutdown()

    async def test_send_reports_false_when_offline(self):
        tui = ChatTUI(name="maps", room="Demo", host="localhost", port=7777)
        sent = await tui._send({"type": "message", "room_id": "x", "content": "hello"})
        self.assertFalse(sent)


if __name__ == "__main__":
    unittest.main()
