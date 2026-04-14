import asyncio
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import backend.hub as hub_module
from backend.hub import PolyculeServer


async def _read_until_type(
    reader: asyncio.StreamReader, msg_type: str, timeout: float = 2.0
) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        data = await asyncio.wait_for(reader.readline(), timeout=timeout)
        if not data:
            raise AssertionError("connection closed while waiting for response")
        payload = json.loads(data.decode().strip())
        if payload.get("type") == msg_type:
            return payload
    raise AssertionError(f"did not receive message type {msg_type}")


class HubIdleTimeoutTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_db_cls = hub_module.PolyculeDB
        hub_module.PolyculeDB = None
        self.server = PolyculeServer(host="127.0.0.1", port=0, client_idle_timeout=0.15)
        self.server.router.create_room("Default", room_id="default-room")

        self.tcp_server = await asyncio.start_server(
            self.server.handle_client,
            host="127.0.0.1",
            port=0,
        )
        sock = self.tcp_server.sockets[0]
        self.host, self.port = sock.getsockname()[0], sock.getsockname()[1]

    async def asyncTearDown(self):
        self.tcp_server.close()
        await self.tcp_server.wait_closed()
        hub_module.PolyculeDB = self._old_db_cls

    async def test_idle_timeout_does_not_disconnect_client(self):
        reader, writer = await asyncio.open_connection(self.host, self.port)

        handshake = {
            "type": "handshake",
            "name": "maps",
            "agent_type": "human",
            "room_name": "Default",
        }
        writer.write((json.dumps(handshake) + "\n").encode())
        await writer.drain()

        room_state = await _read_until_type(reader, "room_state")
        self.assertEqual("Default", room_state["room"]["room_name"])

        await asyncio.sleep(0.45)

        writer.write((json.dumps({"type": "command", "command": "list_rooms"}) + "\n").encode())
        await writer.drain()
        rooms = await _read_until_type(reader, "rooms_list")
        self.assertGreaterEqual(len(rooms.get("rooms", [])), 1)

        writer.close()
        await writer.wait_closed()


if __name__ == "__main__":
    unittest.main()
