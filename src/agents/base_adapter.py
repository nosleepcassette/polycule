# maps · cassette.help · MIT
"""
Agent Base Adapter — connects AI agents to polycule hub.

Each adapter:
- Connects to hub via TCP, sends handshake with room_name
- Receives context_dump on connect (history from DB for stateless agents)
- Maintains local context_messages list
- Subclasses implement handle_message() for agent-specific logic
"""
import asyncio
import json
import subprocess
import os
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class AgentConfig:
    name: str
    agent_type: str          # 'claude', 'codex', 'hermes', 'human'
    room_name: str = 'Default'
    hub_host: str = 'localhost'
    hub_port: int = 7777


class BaseAdapter:
    """Base class for all polycule agent adapters."""

    def __init__(self, config: AgentConfig):
        self.config = config
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.room_id: Optional[str] = None
        self.context_messages: List[dict] = []
        self.running = True
        self.log = logging.getLogger(f"polycule.agent.{config.name}")

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    async def connect(self) -> bool:
        """Connect to hub, send handshake, receive initial room state."""
        try:
            self.reader, self.writer = await asyncio.open_connection(
                self.config.hub_host, self.config.hub_port
            )

            # Handshake — include room_name so hub can route immediately
            handshake = {
                'type': 'handshake',
                'name': self.config.name,
                'agent_type': self.config.agent_type,
                'room_name': self.config.room_name,
            }
            self.writer.write((json.dumps(handshake) + '\n').encode())
            await self.writer.drain()

            # Hub may respond with awaiting_room or room_state
            data = await asyncio.wait_for(self.reader.readline(), timeout=10.0)
            response = json.loads(data.decode().strip())

            if response.get('action') == 'awaiting_room':
                # Hub didn't auto-join; send create_room command
                cmd = {
                    'type': 'command',
                    'command': 'create_room',
                    'room_name': self.config.room_name,
                }
                self.writer.write((json.dumps(cmd) + '\n').encode())
                await self.writer.drain()
                data = await asyncio.wait_for(self.reader.readline(), timeout=10.0)
                response = json.loads(data.decode().strip())

            if response.get('type') in ('room_created', 'room_state'):
                room = response.get('room', {})
                self.room_id = room.get('room_id')
                # Seed context from room history
                for msg in room.get('recent_messages', []):
                    if isinstance(msg, dict) and msg.get('type') == 'message':
                        self.context_messages.append(msg)
                self.log.info(
                    f"Connected to room {self.room_id}, "
                    f"loaded {len(self.context_messages)} context messages"
                )
                return True

            self.log.error(f"Unexpected connect response: {response}")
            return False

        except Exception as e:
            self.log.error(f"Connection failed: {e}")
            return False

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    async def send_message(self, content: str):
        """Send a chat message to the hub."""
        if not self.writer or not self.room_id:
            self.log.warning("send_message called but not connected")
            return
        msg = {
            'type': 'message',
            'room_id': self.room_id,
            'content': content,
        }
        self.writer.write((json.dumps(msg) + '\n').encode())
        await self.writer.drain()

    async def send_command(self, command: str, **kwargs):
        """Send a hub command."""
        if not self.writer:
            return
        msg = {'type': 'command', 'command': command, **kwargs}
        self.writer.write((json.dumps(msg) + '\n').encode())
        await self.writer.drain()

    # ------------------------------------------------------------------
    # Receiving / main loop
    # ------------------------------------------------------------------

    async def run(self):
        """Connect and run the receive loop."""
        if not await self.connect():
            self.log.error("Could not connect to hub, exiting")
            return

        while self.running and self.reader:
            try:
                data = await asyncio.wait_for(self.reader.readline(), timeout=30.0)
                if not data:
                    self.log.info("Hub closed connection")
                    break
                msg = json.loads(data.decode().strip())
                await self._dispatch(msg)
            except asyncio.TimeoutError:
                continue
            except json.JSONDecodeError as e:
                self.log.warning(f"Bad JSON from hub: {e}")
            except Exception as e:
                self.log.error(f"Receive loop error: {e}")
                break

        await self.disconnect()

    async def _dispatch(self, msg: dict):
        """Internal: route hub messages."""
        msg_type = msg.get('type')

        if msg_type == 'message':
            self.context_messages.append(msg)
            if len(self.context_messages) > 200:
                self.context_messages = self.context_messages[-100:]
            await self.handle_message(msg)

        elif msg_type == 'context_dump':
            msgs = msg.get('messages', [])
            self.context_messages = msgs + self.context_messages
            if len(self.context_messages) > 300:
                self.context_messages = self.context_messages[-200:]
            self.log.info(f"Context dump: received {len(msgs)} historical messages")
            await self.handle_context_dump(msgs)

        elif msg_type == 'system':
            await self.handle_system(msg)

        elif msg_type == 'approval_request':
            await self.handle_approval_request(msg)

        elif msg_type == 'error':
            self.log.warning(f"Hub error: {msg.get('message')}")

    # ------------------------------------------------------------------
    # Overridable hooks
    # ------------------------------------------------------------------

    async def handle_message(self, message: dict):
        """Called for each chat message. Override in subclasses."""
        pass

    async def handle_context_dump(self, messages: list):
        """Called when hub sends historical messages. Override if needed."""
        pass

    async def handle_system(self, message: dict):
        """Called for system events (agent_joined, agent_left, etc.)."""
        action = message.get('action', '')
        if action == 'agent_joined':
            agent = message.get('agent', {})
            self.log.info(f"Agent joined: {agent.get('name')}")
        elif action == 'agent_left':
            self.log.info(f"Agent left: {message.get('agent_id', '?')}")

    async def handle_approval_request(self, message: dict):
        """Called when hub requests approval for a structural command."""
        self.log.info(
            f"Approval request {message.get('request_id')}: "
            f"{message.get('command')} from {message.get('requester')}"
        )

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def disconnect(self):
        self.running = False
        if self.writer and not self.writer.is_closing():
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Helper: run subprocess non-blocking
    # ------------------------------------------------------------------

    async def run_subprocess(
        self,
        cmd: List[str],
        input_text: Optional[str] = None,
        timeout: float = 60.0,
        cwd: Optional[Path] = None,
    ) -> Optional[str]:
        """Run a subprocess without blocking the event loop."""
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                input=input_text,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd,
            )
            if result.returncode == 0:
                return result.stdout.strip()
            self.log.warning(f"Subprocess {cmd[0]} exited {result.returncode}: {result.stderr[:200]}")
            return None
        except subprocess.TimeoutExpired:
            self.log.warning(f"Subprocess {cmd[0]} timed out after {timeout}s")
            return None
        except Exception as e:
            self.log.error(f"Subprocess {cmd[0]} failed: {e}")
            return None
