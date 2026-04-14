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


async def _read_until(reader: asyncio.StreamReader, predicate, timeout: float = 2.0) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        data = await asyncio.wait_for(reader.readline(), timeout=timeout)
        if not data:
            raise AssertionError("connection closed while waiting for response")
        payload = json.loads(data.decode().strip())
        if predicate(payload):
            return payload
    raise AssertionError("did not receive expected payload")


class HubDirectiveTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_db_cls = hub_module.PolyculeDB
        hub_module.PolyculeDB = None
        self.server = PolyculeServer(host="127.0.0.1", port=0, client_idle_timeout=1.0)
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

    async def _connect(self, name: str, agent_type: str) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        reader, writer = await asyncio.open_connection(self.host, self.port)
        writer.write(
            (
                json.dumps(
                    {
                        "type": "handshake",
                        "name": name,
                        "agent_type": agent_type,
                        "room_name": "Default",
                    }
                )
                + "\n"
            ).encode()
        )
        await writer.drain()
        await _read_until(reader, lambda p: p.get("type") == "room_state")
        return reader, writer

    async def test_directive_broadcast_and_ack_flow(self):
        maps_reader, maps_writer = await self._connect("maps", "human")
        codex_reader, codex_writer = await self._connect("codex", "codex")

        maps_writer.write(
            (
                json.dumps(
                    {
                        "type": "command",
                        "command": "send_directive",
                        "room_id": "default-room",
                        "directive_kind": "brief",
                        "targets": ["codex"],
                        "content": "read the docs and report back",
                        "refs": ["FEATURE_PLAN.md"],
                    }
                )
                + "\n"
            ).encode()
        )
        await maps_writer.drain()

        directive = await _read_until(
            codex_reader,
            lambda p: p.get("type") == "directive" and p.get("directive_kind") == "brief",
        )
        self.assertEqual(["codex"], directive["targets"])
        self.assertEqual("maps", directive["issued_by"])

        codex_writer.write(
            (
                json.dumps(
                    {
                        "type": "command",
                        "command": "ack_directive",
                        "room_id": "default-room",
                        "directive_id": directive["directive_id"],
                        "state": "accepted",
                    }
                )
                + "\n"
            ).encode()
        )
        await codex_writer.drain()

        ack = await _read_until(
            maps_reader,
            lambda p: p.get("type") == "system" and p.get("action") == "directive_ack",
        )
        self.assertEqual("accepted", ack["state"])
        self.assertEqual("codex", ack["agent_name"])

        maps_writer.close()
        codex_writer.close()
        await maps_writer.wait_closed()
        await codex_writer.wait_closed()

    async def test_watch_change_broadcasts_system_event(self):
        maps_reader, maps_writer = await self._connect("maps", "human")

        maps_writer.write(
            (
                json.dumps(
                    {
                        "type": "command",
                        "command": "set_watch",
                        "room_id": "default-room",
                        "watchers": ["wizard"],
                        "scope": "room",
                    }
                )
                + "\n"
            ).encode()
        )
        await maps_writer.drain()

        watch_event = await _read_until(
            maps_reader,
            lambda p: p.get("type") == "system" and p.get("action") == "watch_changed",
        )
        self.assertEqual("wizard", watch_event["watcher"])
        self.assertEqual("room", watch_event["scope"])

        maps_writer.close()
        await maps_writer.wait_closed()


if __name__ == "__main__":
    unittest.main()
