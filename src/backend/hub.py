# maps · cassette.help · MIT
"""
Polycule Hub — central TCP message broker for multi-agent collaboration.

Agents connect via TCP (localhost:7777), send a JSON handshake, and then
exchange newline-delimited JSON messages. The hub routes messages between
agents, persists everything to SQLite, and manages approval flow for
structural tmux commands.

Protocol: see BUILD.md
"""
import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime

# Logging — log to file and stderr
LOG_DIR = Path(__file__).parent.parent.parent / 'logs'
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_DIR / 'hub.log'),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)

# Import DB (will be None if import fails so hub can run without it)
try:
    from db import PolyculeDB
except ImportError:
    try:
        from backend.db import PolyculeDB
    except ImportError:
        PolyculeDB = None
        logger.warning("Could not import PolyculeDB — running without persistence")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Agent:
    id: str
    name: str
    type: str           # 'claude', 'codex', 'hermes', 'human', 'test_agent', …
    handle: Optional[asyncio.StreamWriter] = None
    joined_at: datetime = field(default_factory=datetime.now)
    is_maps: bool = False   # Maps gets special privileges (approve/deny, set_auto_approve)


@dataclass
class Room:
    id: str
    name: str
    agents: Dict[str, Agent] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    messages: List[dict] = field(default_factory=list)   # in-memory recent cache


@dataclass
class ApprovalRequest:
    id: str
    requester_id: str
    requester_name: str
    command: str
    room_id: str
    detail: dict
    created_at: datetime = field(default_factory=datetime.now)


# ---------------------------------------------------------------------------
# Message Router
# ---------------------------------------------------------------------------

class MessageRouter:
    """Routes messages between agents. Stateless (no DB access)."""

    def __init__(self):
        self.rooms: Dict[str, Room] = {}
        self.agents: Dict[str, Agent] = {}

    def create_room(self, name: str, room_id: Optional[str] = None) -> Room:
        rid = room_id or str(uuid.uuid4())[:8]
        room = Room(id=rid, name=name)
        self.rooms[rid] = room
        logger.info(f"Created room: {rid} ({name})")
        return room

    def remove_room(self, room_id: str):
        if room_id not in self.rooms:
            return
        room = self.rooms.pop(room_id)
        for agent_id in list(room.agents):
            self.agents.pop(agent_id, None)
        logger.info(f"Removed room: {room_id} ({room.name})")

    def join_room(self, room_id: str, agent: Agent) -> Room:
        if room_id not in self.rooms:
            raise KeyError(f"Room not found: {room_id}")
        room = self.rooms[room_id]
        room.agents[agent.id] = agent
        self.agents[agent.id] = agent
        logger.info(f"Agent joined: {agent.name} ({agent.id}) → room {room_id}")
        # Notify others
        self._broadcast_to_room(room_id, {
            'type': 'system',
            'action': 'agent_joined',
            'room_id': room_id,
            'agent': {'id': agent.id, 'name': agent.name, 'type': agent.type},
            'timestamp': datetime.now().isoformat(),
        }, exclude_ids=[agent.id])
        return room

    def leave_room(self, room_id: str, agent_id: str):
        if room_id not in self.rooms:
            return
        room = self.rooms[room_id]
        if agent_id not in room.agents:
            return
        del room.agents[agent_id]
        self.agents.pop(agent_id, None)
        self._broadcast_to_room(room_id, {
            'type': 'system',
            'action': 'agent_left',
            'room_id': room_id,
            'agent_id': agent_id,
            'timestamp': datetime.now().isoformat(),
        })
        if not room.agents:
            logger.info(f"Room {room_id} empty, removing")
            del self.rooms[room_id]

    def broadcast_message(self, room_id: str, sender_id: str, content: str) -> dict:
        if room_id not in self.rooms:
            raise KeyError(f"Room not found: {room_id}")
        room = self.rooms[room_id]
        if sender_id not in room.agents:
            raise ValueError(f"Agent {sender_id} not in room {room_id}")
        sender = room.agents[sender_id]
        msg = {
            'id': str(uuid.uuid4())[:12],
            'type': 'message',
            'content': content,
            'sender': {'id': sender.id, 'name': sender.name, 'type': sender.type},
            'room_id': room_id,
            'timestamp': datetime.now().isoformat(),
        }
        room.messages.append(msg)
        if len(room.messages) > 200:
            room.messages = room.messages[-100:]
        self._broadcast_to_room(room_id, msg)
        logger.info(f"[{room_id}] {sender.name}: {content[:80]}")
        return msg

    def get_room_state(self, room_id: str, recent_messages: Optional[List[dict]] = None) -> dict:
        if room_id not in self.rooms:
            raise KeyError(f"Room not found: {room_id}")
        room = self.rooms[room_id]
        return {
            'room_id': room.id,
            'room_name': room.name,
            'created_at': room.created_at.isoformat(),
            'agents': [
                {'id': a.id, 'name': a.name, 'type': a.type}
                for a in room.agents.values()
            ],
            'recent_messages': recent_messages if recent_messages is not None else room.messages[-50:],
        }

    def get_rooms(self) -> List[dict]:
        return [
            {
                'id': r.id,
                'name': r.name,
                'agent_count': len(r.agents),
                'created_at': r.created_at.isoformat(),
                'message_count': len(r.messages),
            }
            for r in self.rooms.values()
        ]

    def _broadcast_to_room(self, room_id: str, message: dict, exclude_ids: list = None):
        if room_id not in self.rooms:
            return
        exclude_ids = exclude_ids or []
        room = self.rooms[room_id]
        payload = (json.dumps(message) + '\n').encode()
        for agent in list(room.agents.values()):
            if agent.id in exclude_ids:
                continue
            if agent.handle and not agent.handle.is_closing():
                try:
                    agent.handle.write(payload)
                except Exception as e:
                    logger.error(f"Failed to write to {agent.name}: {e}")


# ---------------------------------------------------------------------------
# Polycule Server
# ---------------------------------------------------------------------------

class PolyculeServer:

    def __init__(self, host: str = 'localhost', port: int = 7777):
        self.host = host
        self.port = port
        self.router = MessageRouter()
        self.db: Optional[PolyculeDB] = PolyculeDB() if PolyculeDB else None
        self.pending_approvals: Dict[str, ApprovalRequest] = {}
        self.running = False

    # -----------------------------------------------------------------------
    # Start / stop
    # -----------------------------------------------------------------------

    async def start(self):
        self.running = True
        logger.info(f"Polycule Hub starting on {self.host}:{self.port}")

        if self.db:
            self._restore_rooms_from_db()

        # Ensure Default room always exists
        if 'Default' not in [r.name for r in self.router.rooms.values()]:
            room = self.router.create_room('Default')
            if self.db:
                self.db.save_room(room.id, room.name)

        server = await asyncio.start_server(
            self.handle_client, self.host, self.port
        )
        addr = server.sockets[0].getsockname()
        logger.info(f"Polycule Hub listening on {addr}")

        async with server:
            await server.serve_forever()

    async def stop(self):
        self.running = False
        logger.info("Polycule Hub stopping...")
        if self.router.rooms:
            for room_id in list(self.router.rooms.keys()):
                self.router.remove_room(room_id)
        logger.info("Polycule Hub stopped")

    def _restore_rooms_from_db(self):
        """Recreate rooms from DB on startup so history is accessible."""
        if not self.db:
            return
        for row in self.db.get_all_rooms():
            rid = row['id']
            if rid not in self.router.rooms:
                room = self.router.create_room(row['name'], room_id=rid)
                # Pre-load recent messages into in-memory cache
                msgs = self.db.get_recent_messages(rid, limit=50)
                room.messages = msgs
        logger.info(f"Restored {len(self.router.rooms)} room(s) from DB")

    # -----------------------------------------------------------------------
    # Client handler
    # -----------------------------------------------------------------------

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        agent: Optional[Agent] = None
        current_room_id: Optional[str] = None

        try:
            # Handshake
            data = await asyncio.wait_for(reader.readline(), timeout=30.0)
            if not data:
                return

            handshake = json.loads(data.decode().strip())
            if handshake.get('type') != 'handshake':
                self._write(writer, {'error': 'Invalid handshake'})
                await writer.drain()
                return

            agent = Agent(
                id=str(uuid.uuid4())[:12],
                name=handshake.get('name', 'Anonymous'),
                type=handshake.get('agent_type', 'unknown'),
                handle=writer,
                is_maps=(handshake.get('agent_type') == 'human' and
                         handshake.get('name', '').lower() == 'maps'),
            )
            logger.info(f"Agent connected: {agent.name} ({agent.type})")

            # Try to join a room from handshake
            room_name = handshake.get('room_name', 'Default')
            room_id = handshake.get('room_id')

            # Find existing room by name if no explicit room_id
            if not room_id:
                for r in self.router.rooms.values():
                    if r.name == room_name:
                        room_id = r.id
                        break

            if room_id:
                try:
                    room = self.router.join_room(room_id, agent)
                    current_room_id = room_id
                    # Pull history from DB for context dump
                    history = self._get_history(room_id)
                    room_state = self.router.get_room_state(room_id, recent_messages=history[-20:])
                    self._write(writer, {'type': 'room_state', 'room': room_state})
                    await writer.drain()
                    # Send full context dump to stateless agents
                    await self._send_context_dump(writer, room_id, history)
                    logger.info(f"Agent {agent.name} joined room {room_id}")
                except Exception as e:
                    logger.error(f"Failed to join room {room_id}: {e}")
                    self._write(writer, {'error': f'Failed to join room: {e}'})
                    await writer.drain()
                    return
            else:
                # No room found — ask agent to create one
                self._write(writer, {
                    'type': 'system',
                    'action': 'awaiting_room',
                    'message': 'Please send JOIN or CREATE command',
                })
                await writer.drain()

            # Main receive loop
            while True:
                try:
                    data = await asyncio.wait_for(reader.readline(), timeout=300.0)
                    if not data:
                        break
                    message = json.loads(data.decode().strip())
                    result_room = await self.handle_message(agent, message, current_room_id)
                    if result_room:
                        current_room_id = result_room
                except asyncio.TimeoutError:
                    logger.warning(f"Timeout: {agent.name}")
                    break

        except json.JSONDecodeError as e:
            logger.error(f"JSON error from client: {e}")
        except Exception as e:
            logger.error(f"Client handler error: {e}", exc_info=True)
        finally:
            if agent:
                logger.info(f"Agent disconnected: {agent.name}")
                if current_room_id:
                    self.router.leave_room(current_room_id, agent.id)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    # -----------------------------------------------------------------------
    # Message dispatch
    # -----------------------------------------------------------------------

    async def handle_message(
        self, agent: Agent, message: dict, current_room_id: Optional[str]
    ) -> Optional[str]:
        """Process an incoming message. Returns updated current_room_id if changed."""
        msg_type = message.get('type')

        if msg_type == 'message':
            content = message.get('content', '').strip()
            room_id = message.get('room_id') or current_room_id
            if content and room_id:
                try:
                    msg = self.router.broadcast_message(room_id, agent.id, content)
                    if self.db:
                        self.db.save_message(
                            msg['id'], room_id, agent.id,
                            agent.name, agent.type, content, msg['timestamp']
                        )
                except Exception as e:
                    logger.error(f"Broadcast failed: {e}")
                    self._write(agent.handle, {'type': 'error', 'message': str(e)})
                    await agent.handle.drain()
            return current_room_id

        elif msg_type == 'command':
            return await self.handle_command(agent, message, current_room_id)

        elif msg_type == 'request':
            await self.handle_request(agent, message)
            return current_room_id

        else:
            logger.warning(f"Unknown message type: {msg_type}")
            return current_room_id

    async def handle_command(
        self, agent: Agent, message: dict, current_room_id: Optional[str]
    ) -> Optional[str]:
        command = message.get('command')
        room_id = message.get('room_id') or current_room_id

        # --- Room management ---

        if command == 'create_room':
            room_name = message.get('room_name', 'Unnamed Room')
            # Join existing room by name if it already exists (idempotent)
            existing = next(
                (r for r in self.router.rooms.values() if r.name == room_name), None
            )
            if existing:
                room = existing
                if agent.id not in room.agents:
                    self.router.join_room(room.id, agent)
                response_type = 'room_state'
            else:
                room = self.router.create_room(room_name)
                self.router.join_room(room.id, agent)
                if self.db:
                    self.db.save_room(room.id, room_name)
                response_type = 'room_created'
            logger.info(f"Agent {agent.name} ({agent.id}) → room {room.id} ({room_name}) [{response_type}]")
            history = self._get_history(room.id)
            self._write(agent.handle, {
                'type': response_type,
                'room': self.router.get_room_state(room.id, recent_messages=history[-20:]),
            })
            await agent.handle.drain()
            await self._send_context_dump(agent.handle, room.id, history)
            return room.id

        elif command == 'join_room':
            target_id = message.get('room_id')
            if not target_id or target_id not in self.router.rooms:
                self._write(agent.handle, {'type': 'error', 'message': 'Room not found'})
                await agent.handle.drain()
                return current_room_id
            if current_room_id:
                self.router.leave_room(current_room_id, agent.id)
            room = self.router.join_room(target_id, agent)
            history = self._get_history(target_id)
            self._write(agent.handle, {
                'type': 'room_state',
                'room': self.router.get_room_state(target_id, recent_messages=history[-20:]),
            })
            await agent.handle.drain()
            await self._send_context_dump(agent.handle, target_id, history)
            return target_id

        elif command == 'leave_room':
            if room_id:
                self.router.leave_room(room_id, agent.id)
                self._write(agent.handle, {'type': 'left_room', 'room_id': room_id})
                await agent.handle.drain()
            return None

        elif command == 'list_rooms':
            self._write(agent.handle, {'type': 'rooms_list', 'rooms': self.router.get_rooms()})
            await agent.handle.drain()
            return current_room_id

        # --- Settings (maps only for sensitive ones) ---

        elif command == 'set_auto_approve':
            value = bool(message.get('value', False))
            if self.db:
                self.db.set_auto_approve(value)
            status = 'enabled' if value else 'disabled'
            logger.info(f"Auto-approve {status} by {agent.name}")
            self._write(agent.handle, {
                'type': 'system',
                'action': 'auto_approve_changed',
                'value': value,
                'message': f'Auto-approve {status}',
            })
            await agent.handle.drain()
            # Announce to room
            if room_id and room_id in self.router.rooms:
                self.router._broadcast_to_room(room_id, {
                    'type': 'system',
                    'action': 'auto_approve_changed',
                    'value': value,
                    'message': f'[hub] Auto-approve {status} by {agent.name}',
                    'timestamp': datetime.now().isoformat(),
                }, exclude_ids=[agent.id])
            return current_room_id

        elif command == 'approve':
            return await self._handle_approve(agent, message, current_room_id, granted=True)

        elif command == 'deny':
            return await self._handle_approve(agent, message, current_room_id, granted=False)

        # --- Structural (tmux) commands — require approval unless auto_approve ---

        elif command in ('split_window', 'kill_pane', 'rename_window', 'new_window'):
            return await self._handle_structural(agent, command, message, room_id, current_room_id)

        else:
            logger.warning(f"Unknown command: {command}")
            self._write(agent.handle, {'type': 'error', 'message': f'Unknown command: {command}'})
            await agent.handle.drain()
            return current_room_id

    async def handle_request(self, agent: Agent, message: dict):
        req = message.get('request')
        if req == 'rooms':
            self._write(agent.handle, {'type': 'rooms_list', 'rooms': self.router.get_rooms()})
            await agent.handle.drain()
        elif req == 'room_state':
            room_id = message.get('room_id')
            if room_id and room_id in self.router.rooms:
                history = self._get_history(room_id)
                state = self.router.get_room_state(room_id, recent_messages=history[-20:])
                self._write(agent.handle, {'type': 'room_state', 'room': state})
            else:
                self._write(agent.handle, {'type': 'error', 'message': 'Room not found'})
            await agent.handle.drain()
        elif req == 'auto_approve':
            value = self.db.auto_approve() if self.db else False
            self._write(agent.handle, {'type': 'auto_approve_status', 'value': value})
            await agent.handle.drain()

    # -----------------------------------------------------------------------
    # Approval flow
    # -----------------------------------------------------------------------

    async def _handle_structural(
        self, agent: Agent, command: str, message: dict, room_id: Optional[str], current_room_id: Optional[str]
    ) -> Optional[str]:
        auto = self.db.auto_approve() if self.db else False

        if auto:
            await self._execute_structural(agent, command, message, room_id)
        else:
            req_id = str(uuid.uuid4())[:8]
            req = ApprovalRequest(
                id=req_id,
                requester_id=agent.id,
                requester_name=agent.name,
                command=command,
                room_id=room_id or '',
                detail=message,
            )
            self.pending_approvals[req_id] = req
            logger.info(f"Approval request {req_id}: {command} from {agent.name}")

            # Broadcast request to room
            if room_id and room_id in self.router.rooms:
                self.router._broadcast_to_room(room_id, {
                    'type': 'approval_request',
                    'request_id': req_id,
                    'requester': agent.name,
                    'command': command,
                    'detail': message,
                    'timestamp': datetime.now().isoformat(),
                })
        return current_room_id

    async def _handle_approve(
        self, agent: Agent, message: dict, current_room_id: Optional[str], granted: bool
    ) -> Optional[str]:
        req_id = message.get('request_id')
        if not req_id or req_id not in self.pending_approvals:
            self._write(agent.handle, {'type': 'error', 'message': f'Request {req_id} not found'})
            await agent.handle.drain()
            return current_room_id

        req = self.pending_approvals.pop(req_id)
        result_type = 'approval_granted' if granted else 'approval_denied'
        logger.info(f"Approval {result_type}: {req_id} ({req.command}) by {agent.name}")

        # Broadcast result to room
        if req.room_id and req.room_id in self.router.rooms:
            self.router._broadcast_to_room(req.room_id, {
                'type': result_type,
                'request_id': req_id,
                'command': req.command,
                'requester': req.requester_name,
                'approved_by': agent.name,
                'timestamp': datetime.now().isoformat(),
            })

        if granted:
            await self._execute_structural(agent, req.command, req.detail, req.room_id)

        return current_room_id

    async def _execute_structural(self, agent: Agent, command: str, message: dict, room_id: Optional[str]):
        """Execute a tmux structural command via TmuxController."""
        try:
            import sys
            sys.path.insert(0, str(Path(__file__).parent.parent))
            from tmux_controller import TmuxController
            ctrl = TmuxController(session_name='polycule')

            if command == 'split_window':
                pane = ctrl.create_pane()
                result = f"Created pane {pane.id}"
            elif command == 'kill_pane':
                pane_id = message.get('pane_id', '')
                ctrl.kill_pane(pane_id)
                result = f"Killed pane {pane_id}"
            elif command == 'rename_window':
                result = "rename_window not yet implemented"
            elif command == 'new_window':
                result = "new_window not yet implemented"
            else:
                result = f"Unknown structural command: {command}"

            logger.info(f"Structural command executed: {command} → {result}")

            # Notify room
            if room_id and room_id in self.router.rooms:
                self.router._broadcast_to_room(room_id, {
                    'type': 'system',
                    'action': 'structural_executed',
                    'command': command,
                    'result': result,
                    'executor': agent.name,
                    'timestamp': datetime.now().isoformat(),
                })
        except Exception as e:
            logger.error(f"Structural command {command} failed: {e}")

    # -----------------------------------------------------------------------
    # Context / history helpers
    # -----------------------------------------------------------------------

    def _get_history(self, room_id: str) -> List[dict]:
        """Pull history from DB, fall back to in-memory cache."""
        if self.db and self.db.room_exists(room_id):
            limit = self.db.context_window()
            return self.db.get_recent_messages(room_id, limit=limit)
        if room_id in self.router.rooms:
            return self.router.rooms[room_id].messages[-50:]
        return []

    async def _send_context_dump(
        self, writer: asyncio.StreamWriter, room_id: str, history: List[dict]
    ):
        """Send full history to a newly connected agent (for stateless agents)."""
        if not history:
            return
        self._write(writer, {
            'type': 'context_dump',
            'room_id': room_id,
            'messages': history,
            'count': len(history),
        })
        try:
            await writer.drain()
        except Exception:
            pass

    # -----------------------------------------------------------------------
    # Utility
    # -----------------------------------------------------------------------

    @staticmethod
    def _write(writer: asyncio.StreamWriter, obj: dict):
        try:
            writer.write((json.dumps(obj) + '\n').encode())
        except Exception as e:
            logger.error(f"Write error: {e}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    server = PolyculeServer(host='localhost', port=7777)
    try:
        await server.start()
    except KeyboardInterrupt:
        logger.info("Interrupt received")
    finally:
        await server.stop()


if __name__ == '__main__':
    asyncio.run(main())
