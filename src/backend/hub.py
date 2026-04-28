# Polycule · MIT
"""
Polycule Hub — central TCP message broker for multi-agent collaboration.

Agents connect via TCP (localhost:7777), send a JSON handshake, and then
exchange newline-delimited JSON messages. The hub routes messages between
agents, persists everything to SQLite, and manages approval flow for
structural tmux commands.

Protocol: see BUILD.md
"""

import asyncio
import argparse
import json
import logging
import os
import sys
import time
import uuid
from collections import defaultdict, deque
from pathlib import Path
from typing import Dict, List, Optional, Any, Deque
from dataclasses import dataclass, field
from datetime import datetime

# Logging — log to file and stderr
LOG_DIR = Path(__file__).parent.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "hub.log"),
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

try:
    from runtime_state import (
        clear_agent_watch_entry,
        normalize_watch_scope,
        update_agent_watch_entry,
    )
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from runtime_state import (
        clear_agent_watch_entry,
        normalize_watch_scope,
        update_agent_watch_entry,
    )


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class Agent:
    id: str
    name: str
    type: str  # 'claude', 'codex', 'hermes', 'human', 'test_agent', …
    handle: Optional[asyncio.StreamWriter] = None
    joined_at: datetime = field(default_factory=datetime.now)
    is_operator: bool = False


@dataclass
class Room:
    id: str
    name: str
    agents: Dict[str, Agent] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    messages: List[dict] = field(default_factory=list)  # in-memory recent cache


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
# Message Guard
# ---------------------------------------------------------------------------


@dataclass
class GuardDecision:
    allowed: bool
    reason: str = ""
    detail: Dict[str, Any] = field(default_factory=dict)


class MessageGuard:
    """
    Suppress obvious recursive agent chatter without blocking human input.
    """

    def __init__(
        self,
        min_interval_seconds: float = 1.5,
        duplicate_window_seconds: float = 90.0,
        room_echo_sender_threshold: int = 1,
        burst_limit_count: int = 4,
        burst_window_seconds: float = 8.0,
    ):
        self.min_interval_seconds = min_interval_seconds
        self.duplicate_window_seconds = duplicate_window_seconds
        self.room_echo_sender_threshold = room_echo_sender_threshold
        self.burst_limit_count = burst_limit_count
        self.burst_window_seconds = burst_window_seconds

        self._last_sent_by_agent: Dict[str, float] = {}
        self._recent_by_room: Dict[str, Deque[dict]] = defaultdict(deque)

    @staticmethod
    def _normalize(content: str) -> str:
        return " ".join(content.lower().split())[:500]

    def _prune(self, room_id: str, now_mono: float):
        cutoff = now_mono - self.duplicate_window_seconds
        bucket = self._recent_by_room[room_id]
        while bucket and bucket[0]["ts"] < cutoff:
            bucket.popleft()

    def evaluate(
        self, room_id: str, agent: Agent, content: str, now_mono: Optional[float] = None
    ) -> GuardDecision:
        if agent.type == "human":
            return GuardDecision(allowed=True)

        norm = self._normalize(content)
        if not norm:
            return GuardDecision(allowed=False, reason="empty_message")

        now_mono = now_mono if now_mono is not None else time.monotonic()
        self._prune(room_id, now_mono)
        bucket = self._recent_by_room[room_id]

        last_sent = self._last_sent_by_agent.get(agent.id)
        if last_sent is not None:
            delta = now_mono - last_sent
            if delta < self.min_interval_seconds:
                return GuardDecision(
                    allowed=False,
                    reason="cooldown",
                    detail={
                        "retry_after_seconds": round(
                            self.min_interval_seconds - delta, 3
                        )
                    },
                )

        if any(
            item["agent_id"] == agent.id and item["norm"] == norm for item in bucket
        ):
            return GuardDecision(allowed=False, reason="duplicate_recent")

        matching_non_human_senders = {
            item["agent_id"]
            for item in bucket
            if item["agent_id"] != agent.id
            and item["agent_type"] != "human"
            and item["norm"] == norm
        }
        if len(matching_non_human_senders) >= self.room_echo_sender_threshold:
            return GuardDecision(allowed=False, reason="echo_loop")

        burst_count = sum(
            1
            for item in bucket
            if item["agent_id"] == agent.id
            and (now_mono - item["ts"]) <= self.burst_window_seconds
        )
        if burst_count >= self.burst_limit_count:
            return GuardDecision(allowed=False, reason="burst_limit")

        return GuardDecision(allowed=True)

    def record(
        self, room_id: str, agent: Agent, content: str, now_mono: Optional[float] = None
    ):
        norm = self._normalize(content)
        if not norm:
            return
        now_mono = now_mono if now_mono is not None else time.monotonic()
        self._last_sent_by_agent[agent.id] = now_mono
        self._recent_by_room[room_id].append(
            {
                "ts": now_mono,
                "agent_id": agent.id,
                "agent_type": agent.type,
                "norm": norm,
            }
        )
        self._prune(room_id, now_mono)


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
        self._broadcast_to_room(
            room_id,
            {
                "type": "system",
                "action": "agent_joined",
                "room_id": room_id,
                "agent": {"id": agent.id, "name": agent.name, "type": agent.type},
                "timestamp": datetime.now().isoformat(),
            },
            exclude_ids=[agent.id],
        )
        return room

    def leave_room(self, room_id: str, agent_id: str):
        if room_id not in self.rooms:
            return
        room = self.rooms[room_id]
        if agent_id not in room.agents:
            return
        del room.agents[agent_id]
        self.agents.pop(agent_id, None)
        self._broadcast_to_room(
            room_id,
            {
                "type": "system",
                "action": "agent_left",
                "room_id": room_id,
                "agent_id": agent_id,
                "timestamp": datetime.now().isoformat(),
            },
        )
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
            "id": str(uuid.uuid4())[:12],
            "type": "message",
            "content": content,
            "sender": {"id": sender.id, "name": sender.name, "type": sender.type},
            "room_id": room_id,
            "timestamp": datetime.now().isoformat(),
        }
        room.messages.append(msg)
        if len(room.messages) > 200:
            room.messages = room.messages[-100:]
        self._broadcast_to_room(room_id, msg)
        logger.info(f"[{room_id}] {sender.name}: {content[:80]}")
        return msg

    def get_room_state(
        self, room_id: str, recent_messages: Optional[List[dict]] = None
    ) -> dict:
        if room_id not in self.rooms:
            raise KeyError(f"Room not found: {room_id}")
        room = self.rooms[room_id]
        return {
            "room_id": room.id,
            "room_name": room.name,
            "created_at": room.created_at.isoformat(),
            "agents": [
                {"id": a.id, "name": a.name, "type": a.type}
                for a in room.agents.values()
            ],
            "recent_messages": recent_messages
            if recent_messages is not None
            else room.messages[-50:],
        }

    def get_rooms(self) -> List[dict]:
        return [
            {
                "id": r.id,
                "name": r.name,
                "agent_count": len(r.agents),
                "created_at": r.created_at.isoformat(),
                "message_count": len(r.messages),
            }
            for r in self.rooms.values()
        ]

    def _broadcast_to_room(self, room_id: str, message: dict, exclude_ids: list = None):
        if room_id not in self.rooms:
            return
        exclude_ids = exclude_ids or []
        room = self.rooms[room_id]
        payload = (json.dumps(message) + "\n").encode()
        for agent in list(room.agents.values()):
            if agent.id in exclude_ids:
                continue
            if agent.handle and not agent.handle.is_closing():
                try:
                    agent.handle.write(payload)
                except Exception as e:
                    logger.error(f"Failed to write to {agent.name}: {e}")

    def broadcast_typing(
        self, room_id: str, agent_id: str, agent_name: str, is_typing: bool
    ):
        """Broadcast typing indicator to room."""
        self._broadcast_to_room(
            room_id,
            {
                "type": "system",
                "action": "agent_typing",
                "agent_id": agent_id,
                "agent_name": agent_name,
                "is_typing": is_typing,
            },
            exclude_ids=[agent_id],
        )

    def broadcast_tool_use(
        self, room_id: str, agent_id: str, agent_name: str, tool_name: str, status: str
    ):
        """Broadcast tool use status (started/completed/failed)."""
        self._broadcast_to_room(
            room_id,
            {
                "type": "system",
                "action": "agent_tool_use",
                "agent_id": agent_id,
                "agent_name": agent_name,
                "tool_name": tool_name,
                "status": status,
            },
        )

    def broadcast_context_warning(
        self, room_id: str, agent_id: str, agent_name: str, usage_pct: float
    ):
        """Broadcast context window warning."""
        self._broadcast_to_room(
            room_id,
            {
                "type": "system",
                "action": "context_warning",
                "agent_id": agent_id,
                "agent_name": agent_name,
                "usage_pct": usage_pct,
            },
        )

    def broadcast_session_event(
        self,
        room_id: str,
        agent_id: str,
        agent_name: str,
        session_id: str,
        session_title: str,
        state: str,
    ):
        """Broadcast non-chat session lifecycle events for adapters."""
        self._broadcast_to_room(
            room_id,
            {
                "type": "system",
                "action": "agent_session",
                "agent_id": agent_id,
                "agent_name": agent_name,
                "session_id": session_id,
                "session_title": session_title,
                "state": state,
            },
        )


# ---------------------------------------------------------------------------
# Polycule Server
# ---------------------------------------------------------------------------


class PolyculeServer:
    def __init__(
        self,
        host: str = "localhost",
        port: int = 7777,
        client_idle_timeout: float = 300.0,
    ):
        self.host = host
        self.port = port
        self.client_idle_timeout = client_idle_timeout
        self.router = MessageRouter()
        self.db: Optional[PolyculeDB] = PolyculeDB() if PolyculeDB else None
        self.pending_approvals: Dict[str, ApprovalRequest] = {}
        self.rate_limit_count = self._env_int("POLYCULE_RATE_LIMIT_COUNT", 10)
        self.rate_limit_window_seconds = self._env_float(
            "POLYCULE_RATE_LIMIT_WINDOW_SECONDS", 60.0
        )
        self.message_windows: Dict[str, Deque[float]] = defaultdict(deque)
        self.guard = MessageGuard(
            min_interval_seconds=self._env_float(
                "POLYCULE_GUARD_MIN_INTERVAL_SECONDS", 1.5
            ),
            duplicate_window_seconds=self._env_float(
                "POLYCULE_GUARD_DUPLICATE_WINDOW_SECONDS", 90.0
            ),
            room_echo_sender_threshold=self._env_int(
                "POLYCULE_GUARD_ROOM_ECHO_SENDER_THRESHOLD", 1
            ),
            burst_limit_count=self._env_int("POLYCULE_GUARD_BURST_LIMIT_COUNT", 4),
            burst_window_seconds=self._env_float(
                "POLYCULE_GUARD_BURST_WINDOW_SECONDS", 8.0
            ),
        )
        self.directives: Dict[str, dict] = {}
        self.running = False
        self._server: Optional[asyncio.AbstractServer] = None
        self._shutdown_task: Optional[asyncio.Task] = None

    # -----------------------------------------------------------------------
    # Start / stop
    # -----------------------------------------------------------------------

    async def start(self):
        self.running = True
        logger.info(f"Polycule Hub starting on {self.host}:{self.port}")
        logger.info(
            "Rate limit: %s message(s) per %.1fs",
            self.rate_limit_count,
            self.rate_limit_window_seconds,
        )
        logger.info(
            "Loop guard: min_interval=%.2fs dup_window=%.1fs burst=%s/%ss echo_threshold=%s",
            self.guard.min_interval_seconds,
            self.guard.duplicate_window_seconds,
            self.guard.burst_limit_count,
            int(self.guard.burst_window_seconds),
            self.guard.room_echo_sender_threshold,
        )

        if self.db:
            self._restore_rooms_from_db()

        # Ensure Default room always exists
        if "Default" not in [r.name for r in self.router.rooms.values()]:
            room = self.router.create_room("Default")
            if self.db:
                self.db.save_room(room.id, room.name)

        self._server = await asyncio.start_server(
            self.handle_client, self.host, self.port,
            limit=2**22  # 4MB line limit — context dumps can be large
        )
        addr = self._server.sockets[0].getsockname()
        logger.info(f"Polycule Hub listening on {addr}")

        async with self._server:
            await self._server.serve_forever()

    async def stop(self):
        self.running = False
        logger.info("Polycule Hub stopping...")
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if self.router.rooms:
            for room_id in list(self.router.rooms.keys()):
                self.router.remove_room(room_id)
        logger.info("Polycule Hub stopped")

    def status_payload(self) -> dict:
        rooms = self.router.get_rooms()
        agents = [
            {"id": agent.id, "name": agent.name, "type": agent.type}
            for agent in self.router.agents.values()
        ]
        return {
            "host": self.host,
            "port": self.port,
            "room_count": len(rooms),
            "agent_count": len(agents),
            "rooms": rooms,
            "agents": agents,
        }

    async def _shutdown_after_notice(self, mode: str, timeout: float):
        delay = 0.0 if mode == "immediate" else max(0.0, timeout)
        if delay:
            await asyncio.sleep(delay)
        await self.stop()

    def _restore_rooms_from_db(self):
        """Recreate rooms from DB on startup so history is accessible."""
        if not self.db:
            return
        for row in self.db.get_all_rooms():
            rid = row["id"]
            if rid not in self.router.rooms:
                room = self.router.create_room(row["name"], room_id=rid)
                # Pre-load recent messages into in-memory cache
                msgs = self.db.get_recent_messages(rid, limit=50)
                room.messages = msgs
        logger.info(f"Restored {len(self.router.rooms)} room(s) from DB")

    # -----------------------------------------------------------------------
    # Client handler
    # -----------------------------------------------------------------------

    async def handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ):
        agent: Optional[Agent] = None
        current_room_id: Optional[str] = None

        try:
            # Handshake
            data = await asyncio.wait_for(reader.readline(), timeout=30.0)
            if not data:
                return

            handshake = json.loads(data.decode().strip())
            if handshake.get("type") != "handshake":
                self._write(writer, {"error": "Invalid handshake"})
                await writer.drain()
                return

            agent = Agent(
                id=str(uuid.uuid4())[:12],
                name=handshake.get("name", "Anonymous"),
                type=handshake.get("agent_type", "unknown"),
                handle=writer,
                is_operator=handshake.get("agent_type") == "human",
            )
            logger.info(f"Agent connected: {agent.name} ({agent.type})")

            # Try to join a room from handshake
            room_name = handshake.get("room_name", "Default")
            room_id = handshake.get("room_id")

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
                    room_state = self.router.get_room_state(
                        room_id, recent_messages=history[-20:]
                    )
                    self._write(writer, {"type": "room_state", "room": room_state})
                    await writer.drain()
                    # Send full context dump to stateless agents
                    await self._send_context_dump(writer, room_id, history)
                    logger.info(f"Agent {agent.name} joined room {room_id}")
                except Exception as e:
                    logger.error(f"Failed to join room {room_id}: {e}")
                    self._write(writer, {"error": f"Failed to join room: {e}"})
                    await writer.drain()
                    return
            else:
                # No room found — ask agent to create one
                self._write(
                    writer,
                    {
                        "type": "system",
                        "action": "awaiting_room",
                        "message": "Please send JOIN or CREATE command",
                    },
                )
                await writer.drain()

            # Main receive loop
            while True:
                try:
                    data = await asyncio.wait_for(
                        reader.readline(), timeout=self.client_idle_timeout
                    )
                    if not data:
                        break
                    message = json.loads(data.decode().strip())
                    result_room = await self.handle_message(
                        agent, message, current_room_id
                    )
                    if result_room:
                        current_room_id = result_room
                except asyncio.TimeoutError:
                    # Keep idle clients alive; tmux panes/windows are often inactive.
                    if writer.is_closing():
                        break
                    continue

        except json.JSONDecodeError as e:
            logger.error(f"JSON error from client: {e}")
        except Exception as e:
            logger.error(f"Client handler error: {e}", exc_info=True)
        finally:
            if agent:
                logger.info(f"Agent disconnected: {agent.name}")
                if current_room_id:
                    self.router.leave_room(current_room_id, agent.id)
                self.message_windows.pop(agent.id, None)
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
        msg_type = message.get("type")

        if msg_type == "message":
            content = message.get("content", "").strip()
            room_id = message.get("room_id") or current_room_id
            if content and room_id:
                allowed, retry_after = self._allow_message(agent.id)
                if not allowed:
                    logger.warning(
                        "Rate limit exceeded: %s (%s), retry_after=%.2fs",
                        agent.name,
                        agent.id,
                        retry_after,
                    )
                    self._write(
                        agent.handle,
                        {
                            "type": "error",
                            "error": "rate_limit_exceeded",
                            "message": (
                                f"Rate limit exceeded: max {self.rate_limit_count} messages "
                                f"per {self.rate_limit_window_seconds:.1f}s"
                            ),
                            "limit": self.rate_limit_count,
                            "window_seconds": self.rate_limit_window_seconds,
                            "retry_after_seconds": round(retry_after, 3),
                        },
                    )
                    await agent.handle.drain()
                    return current_room_id
                try:
                    decision = self.guard.evaluate(room_id, agent, content)
                    if not decision.allowed:
                        logger.warning(
                            "Loop guard blocked %s (%s): %s",
                            agent.name,
                            agent.id,
                            decision.reason,
                        )
                        self._write(
                            agent.handle,
                            {
                                "type": "error",
                                "error": "loop_guard_blocked",
                                "reason": decision.reason,
                                "message": f"Message blocked by loop guard ({decision.reason})",
                                "detail": decision.detail,
                            },
                        )
                        await agent.handle.drain()
                        return current_room_id
                    self.guard.record(room_id, agent, content)
                    msg = self.router.broadcast_message(room_id, agent.id, content)
                    if self.db:
                        self.db.save_message(
                            msg["id"],
                            room_id,
                            agent.id,
                            agent.name,
                            agent.type,
                            content,
                            msg["timestamp"],
                        )
                except Exception as e:
                    logger.error(f"Broadcast failed: {e}")
                    self._write(agent.handle, {"type": "error", "message": str(e)})
                    await agent.handle.drain()
            return current_room_id

        elif msg_type == "command":
            return await self.handle_command(agent, message, current_room_id)

        elif msg_type == "request":
            await self.handle_request(agent, message)
            return current_room_id

        elif msg_type == "status":
            await self.handle_status(agent, message, current_room_id)
            return current_room_id

        else:
            logger.warning(f"Unknown message type: {msg_type}")
            return current_room_id

    async def handle_command(
        self, agent: Agent, message: dict, current_room_id: Optional[str]
    ) -> Optional[str]:
        command = message.get("command")
        room_id = message.get("room_id") or current_room_id

        # --- Room management ---

        if command == "create_room":
            room_name = message.get("room_name", "Unnamed Room")
            # Join existing room by name if it already exists (idempotent)
            existing = next(
                (r for r in self.router.rooms.values() if r.name == room_name), None
            )
            if existing:
                room = existing
                if agent.id not in room.agents:
                    self.router.join_room(room.id, agent)
                response_type = "room_state"
            else:
                room = self.router.create_room(room_name)
                self.router.join_room(room.id, agent)
                if self.db:
                    self.db.save_room(room.id, room_name)
                response_type = "room_created"
            logger.info(
                f"Agent {agent.name} ({agent.id}) → room {room.id} ({room_name}) [{response_type}]"
            )
            history = self._get_history(room.id)
            self._write(
                agent.handle,
                {
                    "type": response_type,
                    "room": self.router.get_room_state(
                        room.id, recent_messages=history[-20:]
                    ),
                },
            )
            await agent.handle.drain()
            await self._send_context_dump(agent.handle, room.id, history)
            return room.id

        elif command == "join_room":
            target_id = message.get("room_id")
            if not target_id or target_id not in self.router.rooms:
                self._write(
                    agent.handle, {"type": "error", "message": "Room not found"}
                )
                await agent.handle.drain()
                return current_room_id
            if current_room_id:
                self.router.leave_room(current_room_id, agent.id)
            room = self.router.join_room(target_id, agent)
            history = self._get_history(target_id)
            self._write(
                agent.handle,
                {
                    "type": "room_state",
                    "room": self.router.get_room_state(
                        target_id, recent_messages=history[-20:]
                    ),
                },
            )
            await agent.handle.drain()
            await self._send_context_dump(agent.handle, target_id, history)
            return target_id

        elif command == "leave_room":
            if room_id:
                self.router.leave_room(room_id, agent.id)
                self._write(agent.handle, {"type": "left_room", "room_id": room_id})
                await agent.handle.drain()
            return None

        elif command == "list_rooms":
            self._write(
                agent.handle, {"type": "rooms_list", "rooms": self.router.get_rooms()}
            )
            await agent.handle.drain()
            return current_room_id

        # --- Settings ---

        elif command == "set_auto_approve":
            value = bool(message.get("value", False))
            if self.db:
                self.db.set_auto_approve(value)
            status = "enabled" if value else "disabled"
            logger.info(f"Auto-approve {status} by {agent.name}")
            self._write(
                agent.handle,
                {
                    "type": "system",
                    "action": "auto_approve_changed",
                    "value": value,
                    "message": f"Auto-approve {status}",
                },
            )
            await agent.handle.drain()
            # Announce to room
            if room_id and room_id in self.router.rooms:
                self.router._broadcast_to_room(
                    room_id,
                    {
                        "type": "system",
                        "action": "auto_approve_changed",
                        "value": value,
                        "message": f"[hub] Auto-approve {status} by {agent.name}",
                        "timestamp": datetime.now().isoformat(),
                    },
                    exclude_ids=[agent.id],
                )
            return current_room_id

        elif command == "approve":
            return await self._handle_approve(
                agent, message, current_room_id, granted=True
            )

        elif command == "deny":
            return await self._handle_approve(
                agent, message, current_room_id, granted=False
            )

        # --- Agent status broadcasts ---

        elif command == "agent_typing":
            is_typing = bool(message.get("is_typing", False))
            if room_id:
                self.router.broadcast_typing(room_id, agent.id, agent.name, is_typing)

        elif command == "agent_tool_use":
            tool_name = str(message.get("tool_name", ""))
            status = str(message.get("status", "started"))
            if room_id and tool_name:
                self.router.broadcast_tool_use(
                    room_id, agent.id, agent.name, tool_name, status
                )

        elif command == "context_warning":
            usage_pct = float(message.get("usage_pct", 0))
            if room_id:
                self.router.broadcast_context_warning(
                    room_id, agent.id, agent.name, usage_pct
                )

        elif command == "agent_session":
            session_id = str(message.get("session_id", "")).strip()
            session_title = str(message.get("session_title", "")).strip()
            state = str(message.get("state", "changed")).strip().lower() or "changed"
            if room_id and session_id:
                self.router.broadcast_session_event(
                    room_id,
                    agent.id,
                    agent.name,
                    session_id,
                    session_title,
                    state,
                )

        elif command == "set_watch":
            return await self._handle_watch_update(
                agent,
                message,
                room_id,
                current_room_id,
            )

        elif command == "summon_agents":
            return await self._handle_summon(
                agent,
                message,
                room_id,
                current_room_id,
            )

        elif command == "standdown_agents":
            return await self._handle_standdown(
                agent,
                message,
                room_id,
                current_room_id,
            )

        elif command == "cancel_response":
            return await self._handle_cancel_response(
                agent,
                message,
                room_id,
                current_room_id,
            )

        elif command == "send_directive":
            return await self._handle_directive(
                agent,
                message,
                room_id,
                current_room_id,
            )

        elif command == "ack_directive":
            return await self._handle_directive_ack(
                agent,
                message,
                room_id,
                current_room_id,
            )

        elif command == "agent_mode_update":
            # Broadcast a live mode change to all agents in the room so they
            # update their trigger policy without needing a process restart.
            target_agent = str(message.get("agent", "")).strip().lower()
            new_mode = str(message.get("mode", "")).strip().lower()
            if target_agent and new_mode and room_id and room_id in self.router.rooms:
                self.router._broadcast_to_room(
                    room_id,
                    {
                        "type": "system",
                        "action": "agent_mode_changed",
                        "agent": target_agent,
                        "mode": new_mode,
                        "changed_by": agent.name,
                        "timestamp": datetime.now().isoformat(),
                    },
                )
            return current_room_id

        elif command == "status":
            self._write(
                agent.handle,
                {
                    "type": "command_response",
                    "command": "status",
                    "status": self.status_payload(),
                    "timestamp": datetime.now().isoformat(),
                },
            )
            await agent.handle.drain()
            return current_room_id

        elif command == "shutdown":
            mode = str(message.get("mode", "graceful")).strip().lower()
            if mode not in ("graceful", "immediate"):
                mode = "graceful"
            timeout = self._env_float("POLYCULE_HUB_SHUTDOWN_TIMEOUT", 2.0)
            for room_id in list(self.router.rooms):
                self.router._broadcast_to_room(
                    room_id,
                    {
                        "type": "system",
                        "action": "shutdown_announced",
                        "mode": mode,
                        "issued_by": agent.name,
                        "timeout": timeout,
                        "timestamp": datetime.now().isoformat(),
                    },
                )
            self._write(
                agent.handle,
                {
                    "type": "command_response",
                    "command": "shutdown",
                    "mode": mode,
                    "ok": True,
                },
            )
            await agent.handle.drain()
            if not self._shutdown_task or self._shutdown_task.done():
                self._shutdown_task = asyncio.create_task(
                    self._shutdown_after_notice(mode, timeout)
                )
            return current_room_id

        elif command == "set_topic":
            # Persist room topic and broadcast to all members.
            topic = str(message.get("topic", "")).strip()
            if room_id and self.db:
                self.db.set_room_topic(room_id, topic)
            if room_id:
                self.router._broadcast_to_room(
                    room_id,
                    {
                        "type": "system",
                        "action": "topic_changed",
                        "topic": topic,
                        "changed_by": agent.name,
                        "timestamp": datetime.now().isoformat(),
                    },
                )
            return current_room_id

        # --- Structural (tmux) commands — require approval unless auto_approve ---

        elif command in ("split_window", "kill_pane", "rename_window", "new_window"):
            return await self._handle_structural(
                agent, command, message, room_id, current_room_id
            )

        else:
            logger.warning(f"Unknown command: {command}")
            self._write(
                agent.handle,
                {"type": "error", "message": f"Unknown command: {command}"},
            )
            await agent.handle.drain()
            return current_room_id

    async def handle_request(self, agent: Agent, message: dict):
        req = message.get("request")
        if req == "rooms":
            self._write(
                agent.handle, {"type": "rooms_list", "rooms": self.router.get_rooms()}
            )
            await agent.handle.drain()
        elif req == "room_state":
            room_id = message.get("room_id")
            if room_id and room_id in self.router.rooms:
                history = self._get_history(room_id)
                state = self.router.get_room_state(
                    room_id, recent_messages=history[-20:]
                )
                self._write(agent.handle, {"type": "room_state", "room": state})
            else:
                self._write(
                    agent.handle, {"type": "error", "message": "Room not found"}
                )
            await agent.handle.drain()
        elif req == "auto_approve":
            value = self.db.auto_approve() if self.db else False
            self._write(agent.handle, {"type": "auto_approve_status", "value": value})
            await agent.handle.drain()

    async def handle_status(
        self, agent: Agent, message: dict, current_room_id: Optional[str]
    ):
        """
        Relay adapter status events as room-level system messages.

        Status events are UI transparency signals (responding/timeout/error) and
        are intentionally not persisted as chat messages.
        """
        room_id = message.get("room_id") or current_room_id
        if not room_id or room_id not in self.router.rooms:
            return
        room = self.router.rooms[room_id]
        if agent.id not in room.agents:
            return

        raw_status = message.get("status", "")
        status = str(raw_status).strip().lower() or "update"
        raw_detail = message.get("detail", "")
        detail = str(raw_detail).strip()

        logger.info(
            "Status update [%s] %s (%s): %s %s",
            room_id,
            agent.name,
            agent.id,
            status,
            f"- {detail}" if detail else "",
        )
        self.router._broadcast_to_room(
            room_id,
            {
                "type": "system",
                "action": "agent_status",
                "room_id": room_id,
                "agent": {
                    "id": agent.id,
                    "name": agent.name,
                    "type": agent.type,
                },
                "status": status,
                "detail": detail,
                "timestamp": datetime.now().isoformat(),
            },
        )

    @staticmethod
    def _normalize_targets(raw_targets: Any) -> list[str]:
        if not isinstance(raw_targets, list):
            return []
        out: list[str] = []
        for item in raw_targets:
            value = str(item).strip().lower()
            if value and value not in out:
                out.append(value)
        return out

    async def _handle_watch_update(
        self,
        agent: Agent,
        message: dict,
        room_id: Optional[str],
        current_room_id: Optional[str],
    ) -> Optional[str]:
        if not room_id or room_id not in self.router.rooms:
            return current_room_id
        room = self.router.rooms[room_id]
        watchers = self._normalize_targets(message.get("watchers", []))
        if not watchers:
            self._write(
                agent.handle,
                {"type": "error", "message": "watchers missing"},
            )
            await agent.handle.drain()
            return current_room_id

        scope, target = normalize_watch_scope(
            message.get("scope", "none"),
            message.get("target", ""),
        )

        for watcher in watchers:
            if scope == "none":
                clear_agent_watch_entry(watcher, room.name)
            else:
                update_agent_watch_entry(
                    watcher,
                    room.name,
                    scope=scope,
                    target=target,
                    updated_by=agent.name,
                )
            self.router._broadcast_to_room(
                room_id,
                {
                    "type": "system",
                    "action": "watch_changed",
                    "room_id": room_id,
                    "watcher": watcher,
                    "scope": scope,
                    "target": target,
                    "updated_by": agent.name,
                    "timestamp": datetime.now().isoformat(),
                },
            )
        return current_room_id

    async def _handle_summon(
        self,
        agent: Agent,
        message: dict,
        room_id: Optional[str],
        current_room_id: Optional[str],
    ) -> Optional[str]:
        if not room_id or room_id not in self.router.rooms:
            return current_room_id
        targets = self._normalize_targets(message.get("targets", []))
        auto_enabled = self._normalize_targets(message.get("auto_enabled", []))
        if not targets:
            self._write(
                agent.handle,
                {"type": "error", "message": "summon targets missing"},
            )
            await agent.handle.drain()
            return current_room_id
        self.router._broadcast_to_room(
            room_id,
            {
                "type": "system",
                "action": "agents_summoned",
                "room_id": room_id,
                "targets": targets,
                "auto_enabled": auto_enabled,
                "issued_by": agent.name,
                "timestamp": datetime.now().isoformat(),
            },
        )
        return current_room_id

    async def _handle_standdown(
        self,
        agent: Agent,
        message: dict,
        room_id: Optional[str],
        current_room_id: Optional[str],
    ) -> Optional[str]:
        if not room_id or room_id not in self.router.rooms:
            return current_room_id
        targets = self._normalize_targets(message.get("targets", []))
        auto_disabled = self._normalize_targets(message.get("auto_disabled", []))
        self.router._broadcast_to_room(
            room_id,
            {
                "type": "system",
                "action": "agents_stood_down",
                "room_id": room_id,
                "targets": targets,
                "auto_disabled": auto_disabled,
                "issued_by": agent.name,
                "timestamp": datetime.now().isoformat(),
            },
        )
        return current_room_id

    async def _handle_cancel_response(
        self,
        agent: Agent,
        message: dict,
        room_id: Optional[str],
        current_room_id: Optional[str],
    ) -> Optional[str]:
        if not room_id or room_id not in self.router.rooms:
            return current_room_id
        targets = self._normalize_targets(message.get("targets", []))
        if not targets:
            self._write(
                agent.handle,
                {"type": "error", "message": "cancel targets missing"},
            )
            await agent.handle.drain()
            return current_room_id
        self.router._broadcast_to_room(
            room_id,
            {
                "type": "system",
                "action": "cancel_response",
                "room_id": room_id,
                "targets": targets,
                "issued_by": agent.name,
                "timestamp": datetime.now().isoformat(),
            },
        )
        return current_room_id

    async def _handle_directive(
        self,
        agent: Agent,
        message: dict,
        room_id: Optional[str],
        current_room_id: Optional[str],
    ) -> Optional[str]:
        if not room_id or room_id not in self.router.rooms:
            return current_room_id
        targets = self._normalize_targets(message.get("targets", []))
        content = str(message.get("content", "")).strip()
        if not targets or not content:
            self._write(
                agent.handle,
                {"type": "error", "message": "directive requires targets and content"},
            )
            await agent.handle.drain()
            return current_room_id

        refs = message.get("refs", [])
        if not isinstance(refs, list):
            refs = []
        directive_kind = (
            str(message.get("directive_kind", "brief")).strip().lower() or "brief"
        )
        directive_id = str(uuid.uuid4())[:8]
        payload = {
            "type": "directive",
            "directive_id": directive_id,
            "directive_kind": directive_kind,
            "room_id": room_id,
            "issued_by": agent.name,
            "issued_by_id": agent.id,
            "issued_by_type": agent.type,
            "targets": targets,
            "content": content,
            "refs": refs,
            "timestamp": datetime.now().isoformat(),
        }
        self.directives[directive_id] = dict(payload)
        self.router._broadcast_to_room(room_id, payload)
        return current_room_id

    async def _handle_directive_ack(
        self,
        agent: Agent,
        message: dict,
        room_id: Optional[str],
        current_room_id: Optional[str],
    ) -> Optional[str]:
        directive_id = str(message.get("directive_id", "")).strip()
        if not directive_id:
            return current_room_id
        directive = self.directives.get(directive_id)
        if directive is not None:
            acks = directive.setdefault("acks", {})
            if isinstance(acks, dict):
                acks[agent.id] = {
                    "agent_name": agent.name,
                    "state": str(message.get("state", "accepted")).strip().lower()
                    or "accepted",
                    "timestamp": datetime.now().isoformat(),
                }
        if room_id and room_id in self.router.rooms:
            self.router._broadcast_to_room(
                room_id,
                {
                    "type": "system",
                    "action": "directive_ack",
                    "directive_id": directive_id,
                    "directive_kind": str(
                        (directive or {}).get("directive_kind", "brief")
                    ),
                    "agent_name": agent.name,
                    "state": str(message.get("state", "accepted")).strip().lower()
                    or "accepted",
                    "timestamp": datetime.now().isoformat(),
                },
            )
        return current_room_id

    # -----------------------------------------------------------------------
    # Approval flow
    # -----------------------------------------------------------------------

    async def _handle_structural(
        self,
        agent: Agent,
        command: str,
        message: dict,
        room_id: Optional[str],
        current_room_id: Optional[str],
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
                room_id=room_id or "",
                detail=message,
            )
            self.pending_approvals[req_id] = req
            logger.info(f"Approval request {req_id}: {command} from {agent.name}")

            # Broadcast request to room
            if room_id and room_id in self.router.rooms:
                self.router._broadcast_to_room(
                    room_id,
                    {
                        "type": "approval_request",
                        "request_id": req_id,
                        "requester": agent.name,
                        "command": command,
                        "detail": message,
                        "timestamp": datetime.now().isoformat(),
                    },
                )
        return current_room_id

    async def _handle_approve(
        self, agent: Agent, message: dict, current_room_id: Optional[str], granted: bool
    ) -> Optional[str]:
        req_id = message.get("request_id")
        if not req_id or req_id not in self.pending_approvals:
            self._write(
                agent.handle,
                {"type": "error", "message": f"Request {req_id} not found"},
            )
            await agent.handle.drain()
            return current_room_id

        req = self.pending_approvals.pop(req_id)
        result_type = "approval_granted" if granted else "approval_denied"
        logger.info(f"Approval {result_type}: {req_id} ({req.command}) by {agent.name}")

        # Broadcast result to room
        if req.room_id and req.room_id in self.router.rooms:
            self.router._broadcast_to_room(
                req.room_id,
                {
                    "type": result_type,
                    "request_id": req_id,
                    "command": req.command,
                    "requester": req.requester_name,
                    "approved_by": agent.name,
                    "timestamp": datetime.now().isoformat(),
                },
            )

        if granted:
            await self._execute_structural(agent, req.command, req.detail, req.room_id)

        return current_room_id

    async def _execute_structural(
        self, agent: Agent, command: str, message: dict, room_id: Optional[str]
    ):
        """Execute a tmux structural command via TmuxController."""
        try:
            import sys

            sys.path.insert(0, str(Path(__file__).parent.parent))
            from tmux_controller import TmuxController

            ctrl = TmuxController(session_name="polycule")

            if command == "split_window":
                pane = ctrl.create_pane()
                result = f"Created pane {pane.id}"
            elif command == "kill_pane":
                pane_id = message.get("pane_id", "")
                ctrl.kill_pane(pane_id)
                result = f"Killed pane {pane_id}"
            elif command == "rename_window":
                result = "rename_window not yet implemented"
            elif command == "new_window":
                result = "new_window not yet implemented"
            else:
                result = f"Unknown structural command: {command}"

            logger.info(f"Structural command executed: {command} → {result}")

            # Notify room
            if room_id and room_id in self.router.rooms:
                self.router._broadcast_to_room(
                    room_id,
                    {
                        "type": "system",
                        "action": "structural_executed",
                        "command": command,
                        "result": result,
                        "executor": agent.name,
                        "timestamp": datetime.now().isoformat(),
                    },
                )
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
        self._write(
            writer,
            {
                "type": "context_dump",
                "room_id": room_id,
                "messages": history,
                "count": len(history),
            },
        )
        try:
            await writer.drain()
        except Exception:
            pass

    # -----------------------------------------------------------------------
    # Utility
    # -----------------------------------------------------------------------

    @staticmethod
    def _env_int(name: str, default: int) -> int:
        raw = os.getenv(name)
        if raw is None:
            return default
        try:
            value = int(raw)
            if value < 1:
                raise ValueError
            return value
        except ValueError:
            logger.warning(f"Invalid {name}={raw!r}; using default {default}")
            return default

    @staticmethod
    def _env_float(name: str, default: float) -> float:
        raw = os.getenv(name)
        if raw is None:
            return default
        try:
            value = float(raw)
            if value <= 0:
                raise ValueError
            return value
        except ValueError:
            logger.warning(f"Invalid {name}={raw!r}; using default {default}")
            return default

    def _allow_message(self, agent_id: str) -> tuple[bool, float]:
        now = time.monotonic()
        cutoff = now - self.rate_limit_window_seconds
        bucket = self.message_windows[agent_id]

        while bucket and bucket[0] <= cutoff:
            bucket.popleft()

        if len(bucket) >= self.rate_limit_count:
            retry_after = self.rate_limit_window_seconds - (now - bucket[0])
            return False, max(0.0, retry_after)

        bucket.append(now)
        return True, 0.0

    @staticmethod
    def _write(writer: asyncio.StreamWriter, obj: dict):
        try:
            writer.write((json.dumps(obj) + "\n").encode())
        except Exception as e:
            logger.error(f"Write error: {e}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def main():
    parser = argparse.ArgumentParser(description="Polycule Hub")
    parser.add_argument("--host", default=os.getenv("POLYCULE_HUB_HOST", "localhost"))
    parser.add_argument("--port", type=int, default=int(os.getenv("POLYCULE_HUB_PORT", "7777")))
    args = parser.parse_args()

    server = PolyculeServer(host=args.host, port=args.port)
    try:
        await server.start()
    except KeyboardInterrupt:
        logger.info("Interrupt received")
    finally:
        await server.stop()


if __name__ == "__main__":
    asyncio.run(main())
