import asyncio
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agents.base_adapter import AgentConfig, BaseAdapter


class _FakeReader:
    def __init__(self, payloads):
        self._payloads = list(payloads)

    async def readline(self):
        if self._payloads:
            return self._payloads.pop(0)
        return b''


class _FakeWriter:
    def __init__(self):
        self._closed = False

    def write(self, _data):
        return None

    async def drain(self):
        return None

    def close(self):
        self._closed = True

    def is_closing(self):
        return self._closed

    async def wait_closed(self):
        return None


class _RecordingAdapter(BaseAdapter):
    def __init__(self, connections):
        super().__init__(AgentConfig(name='tester', agent_type='test_agent'))
        self.connections = list(connections)
        self.connect_attempts = 0
        self.handled_messages = []
        self._reconnect_base_delay = 0.01
        self._reconnect_max_delay = 0.02

    async def connect(self) -> bool:
        self.connect_attempts += 1
        if not self.connections:
            await self._close_connection()
            return False
        outcome = self.connections.pop(0)
        if outcome is False:
            await self._close_connection()
            return False
        self.reader = _FakeReader(outcome)
        self.writer = _FakeWriter()
        self.room_id = 'room-1'
        return True

    async def handle_message(self, message: dict):
        self.handled_messages.append(message.get('content'))
        self.running = False


class _CancelableAdapter(BaseAdapter):
    def __init__(self):
        super().__init__(AgentConfig(name='tester', agent_type='test_agent'))
        self.started = asyncio.Event()
        self.cancelled = False

    def _should_respond(self, _message: dict) -> bool:
        return True

    async def handle_message(self, _message: dict):
        self.started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            self.cancelled = True
            raise


class BaseAdapterReconnectTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_retries_until_initial_connection_succeeds(self):
        payload = (
            json.dumps(
                {
                    'type': 'message',
                    'id': 'msg-1',
                    'content': 'hello after retry',
                    'sender': {'name': 'maps', 'type': 'human'},
                }
            ).encode()
            + b'\n'
        )
        adapter = _RecordingAdapter([False, [payload, b'']])

        await asyncio.wait_for(adapter.run(), timeout=1.0)

        self.assertEqual(2, adapter.connect_attempts)
        self.assertEqual(['hello after retry'], adapter.handled_messages)

    async def test_run_reconnects_after_hub_closes_connection(self):
        payload = (
            json.dumps(
                {
                    'type': 'message',
                    'id': 'msg-2',
                    'content': 'hello after reconnect',
                    'sender': {'name': 'maps', 'type': 'human'},
                }
            ).encode()
            + b'\n'
        )
        adapter = _RecordingAdapter([[b''], [payload, b'']])

        await asyncio.wait_for(adapter.run(), timeout=1.0)

        self.assertEqual(2, adapter.connect_attempts)
        self.assertEqual(['hello after reconnect'], adapter.handled_messages)

    async def test_cancel_response_system_event_stops_active_task(self):
        adapter = _CancelableAdapter()
        await adapter._dispatch(
            {
                'type': 'message',
                'id': 'msg-1',
                'content': 'please respond',
                'sender': {'name': 'maps', 'type': 'human'},
            }
        )
        await asyncio.wait_for(adapter.started.wait(), timeout=1.0)

        await adapter._dispatch(
            {
                'type': 'system',
                'action': 'cancel_response',
                'issued_by': 'maps',
                'targets': ['tester'],
            }
        )

        await asyncio.sleep(0)
        self.assertTrue(adapter.cancelled)


if __name__ == '__main__':
    unittest.main()
