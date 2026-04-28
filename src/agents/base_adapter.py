# Polycule · MIT
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
import re
import sys
import contextlib
from collections import deque
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_SRC_DIR = str(Path(__file__).resolve().parents[1])
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from runtime_state import get_agent_watch_entry


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
        self._seen_message_ids: set[str] = set()
        self._seen_message_order: List[str] = []
        self._seen_message_cap = 2000
        self._handled_response_message_ids: set[str] = set()
        self._handled_response_message_order: List[str] = []
        self._handled_response_cap = 2000
        self._response_task: Optional[asyncio.Task] = None
        self._message_queue: deque = deque(maxlen=3)
        self._current_process: Optional[asyncio.subprocess.Process] = None
        self._cancel_reason = ""
        self.running = True
        self._reconnect_base_delay = 1.0
        self._reconnect_max_delay = 15.0
        self.log = logging.getLogger(f"polycule.agent.{config.name}")
        self.watch_scope = "none"
        self.watch_target = ""
        self._load_watch_state()

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    async def connect(self) -> bool:
        """Connect to hub, send handshake, receive initial room state."""
        try:
            self.reader, self.writer = await asyncio.open_connection(
                self.config.hub_host, self.config.hub_port,
                limit=2**22  # 4MB line limit — context dumps can be large
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
                    self._append_context_message(msg)
                self.log.info(
                    f"Connected to room {self.room_id}, "
                    f"loaded {len(self.context_messages)} context messages"
                )
                return True

            self.log.error(f"Unexpected connect response: {response}")
            return False

        except Exception as e:
            self.log.error(f"Connection failed: {e}")
            await self._close_connection()
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

    async def send_status(self, status: str, detail: Optional[str] = None):
        """
        Send an agent status event to the hub.

        Unlike normal chat messages, status events are intended for UI transparency
        (e.g., responding, timed out, failed) and are relayed as system events.
        """
        if not self.writer or not self.room_id:
            return
        msg: Dict[str, Any] = {
            'type': 'status',
            'room_id': self.room_id,
            'status': status,
        }
        if detail:
            msg['detail'] = detail
        self.writer.write((json.dumps(msg) + '\n').encode())
        await self.writer.drain()

    async def send_command(self, command: str, **kwargs):
        """Send a hub command."""
        if not self.writer:
            return
        msg = {'type': 'command', 'command': command, **kwargs}
        self.writer.write((json.dumps(msg) + '\n').encode())
        await self.writer.drain()

    async def set_typing(self, is_typing: bool):
        """Broadcast a typing indicator for this agent."""
        if not self.room_id:
            return
        await self.send_command("agent_typing", is_typing=is_typing)

    # ------------------------------------------------------------------
    # Receiving / main loop
    # ------------------------------------------------------------------

    async def run(self):
        """Connect and stay attached to the hub, reconnecting when needed."""
        attempt = 0

        while self.running:
            if not await self.connect():
                attempt += 1
                delay = min(
                    self._reconnect_max_delay,
                    self._reconnect_base_delay * (2 ** (attempt - 1)),
                )
                self.log.warning(
                    "Connect failed; retrying in %.1fs (attempt %s)",
                    delay,
                    attempt,
                )
                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    break
                continue

            if attempt:
                self.log.info("Reconnected to hub after %s attempt(s)", attempt)
            attempt = 0

            reconnect_required = False
            while self.running and self.reader:
                try:
                    data = await asyncio.wait_for(self.reader.readline(), timeout=30.0)
                    if not data:
                        self.log.info("Hub closed connection")
                        reconnect_required = True
                        break
                    msg = json.loads(data.decode().strip())
                    await self._dispatch(msg)
                except asyncio.TimeoutError:
                    continue
                except json.JSONDecodeError as e:
                    self.log.warning(f"Bad JSON from hub: {e}")
                except Exception as e:
                    self.log.error(f"Receive loop error: {e}")
                    reconnect_required = self.running
                    break

            await self._close_connection()

            if not self.running or not reconnect_required:
                break

        await self.disconnect()

    async def _dispatch(self, msg: dict):
        """Internal: route hub messages."""
        msg_type = msg.get('type')

        if msg_type == 'message':
            added = self._append_context_message(msg)
            if added:
                self._schedule_message_handling(msg)
                await asyncio.sleep(0)

        elif msg_type == 'context_dump':
            msgs = msg.get('messages', [])
            history_to_prepend = []
            for message in msgs:
                if self._remember_message_id(message):
                    history_to_prepend.append(message)
            self.context_messages = history_to_prepend + self.context_messages
            if len(self.context_messages) > 300:
                self.context_messages = self.context_messages[-200:]
            self.log.info(
                "Context dump: received %s historical messages (%s new)",
                len(msgs),
                len(history_to_prepend),
            )
            await self.handle_context_dump(history_to_prepend)

        elif msg_type == 'system':
            await self.handle_system(msg)

        elif msg_type == 'directive':
            await self.handle_directive(msg)

        elif msg_type == 'approval_request':
            await self.handle_approval_request(msg)

        elif msg_type == 'error':
            self.log.warning(f"Hub error: {msg.get('message')}")

    # ------------------------------------------------------------------
    # Message tracking / trigger helpers
    # ------------------------------------------------------------------

    def _load_watch_state(self):
        entry = get_agent_watch_entry(self.config.name, self.config.room_name)
        if not entry:
            return
        self._set_watch_state(entry.get("scope", "none"), entry.get("target", ""))

    def _set_watch_state(self, scope: str, target: str = ""):
        normalized_scope = " ".join(str(scope or "").split()).strip().lower() or "none"
        normalized_target = " ".join(str(target or "").split()).strip().lower()
        if normalized_scope in ("off", "clear"):
            normalized_scope = "none"
            normalized_target = ""
        self.watch_scope = normalized_scope
        self.watch_target = normalized_target

    def _watch_matches_message(self, message: dict) -> bool:
        """
        Phase 1 watch policy:
        - `human`: respond to direct human input without needing a mention
        - `room`: respond to human room traffic without needing a mention
        - `agent:<name>` is tracked and persisted, but remains observe-only for now
        """
        sender = message.get("sender", {})
        sender_type = str(sender.get("type", "")).lower()
        sender_name = str(sender.get("name", "")).lower()

        if self.watch_scope in ("human", "maps"):
            return sender_type == "human"
        if self.watch_scope == "room":
            return sender_type == "human"
        return False

    def _directive_targets_me(self, directive: dict) -> bool:
        targets = directive.get("targets", [])
        if not isinstance(targets, list):
            return False
        normalized_name = self.config.name.strip().lower()
        return any(str(item).strip().lower() == normalized_name for item in targets)

    def _remember_message_id(self, message: dict) -> bool:
        """Track message IDs; return False when this message was already seen."""
        if not isinstance(message, dict):
            return False
        msg_id = message.get('id')
        if not msg_id:
            return True
        if msg_id in self._seen_message_ids:
            return False
        self._seen_message_ids.add(msg_id)
        self._seen_message_order.append(msg_id)
        if len(self._seen_message_order) > self._seen_message_cap:
            old = self._seen_message_order.pop(0)
            self._seen_message_ids.discard(old)
        return True

    def _append_context_message(self, message: dict) -> bool:
        if not isinstance(message, dict) or message.get('type') != 'message':
            return False
        if not self._remember_message_id(message):
            return False
        self.context_messages.append(message)
        if len(self.context_messages) > 200:
            self.context_messages = self.context_messages[-100:]
        return True

    @staticmethod
    def has_any_trigger(content: str, triggers: set[str] | frozenset[str]) -> bool:
        """
        Trigger matching rules:
        - @mentions match by substring (e.g. '@codex')
        - bare words match token boundaries
        """
        lowered = content.lower()
        for trigger in triggers:
            token = trigger.lower()
            if token.startswith('@'):
                if token in lowered:
                    return True
                continue
            if re.search(rf'(?<![\w@]){re.escape(token)}(?![\w])', lowered):
                return True
        return False

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
        elif action == 'watch_changed':
            watcher = str(message.get("watcher", "")).strip().lower()
            if watcher == self.config.name.strip().lower():
                self._set_watch_state(
                    str(message.get("scope", "none")),
                    str(message.get("target", "")),
                )
                self.log.info(
                    "Watch changed: %s %s",
                    self.watch_scope,
                    self.watch_target,
                )
        elif action == "cancel_response":
            issued_by = str(message.get("issued_by", "")).strip() or "unknown"
            targets = [
                str(item).strip().lower()
                for item in message.get("targets", [])
                if str(item).strip()
            ]
            my_name = self.config.name.strip().lower()
            if "all" in targets or my_name in targets:
                await self.cancel_active_response(f"stopped by {issued_by}")
        elif action == "agent_mode_changed":
            target = str(message.get("agent", "")).strip().lower()
            if target == self.config.name.strip().lower():
                mode = str(message.get("mode", "")).strip().lower()
                if mode:
                    self._on_mode_changed(mode)
        elif action == "shutdown_announced":
            mode = str(message.get("mode", "graceful")).strip().lower()
            issued_by = str(message.get("issued_by", "hub")).strip() or "hub"
            if mode == "immediate":
                await self.cancel_active_response(f"shutdown by {issued_by}")
            elif self._response_task and not self._response_task.done():
                with contextlib.suppress(asyncio.CancelledError):
                    await self._response_task
            self.running = False

    async def handle_approval_request(self, message: dict):
        """Called when hub requests approval for a structural command."""
        self.log.info(
            f"Approval request {message.get('request_id')}: "
            f"{message.get('command')} from {message.get('requester')}"
        )

    def _on_mode_changed(self, mode: str):
        """
        Called when the hub broadcasts an agent_mode_changed event targeting
        this adapter.  Override in subclasses to update trigger policy live.
        """
        self.log.info("Mode changed → %s (base: no-op; override to apply)", mode)

    async def handle_directive(self, message: dict):
        """Called when the hub emits a targeted directive."""
        if self._directive_targets_me(message):
            self.log.info(
                "Directive %s (%s) received",
                message.get("directive_id", "?"),
                message.get("directive_kind", "directive"),
            )

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    async def _close_connection(self):
        if self.writer and not self.writer.is_closing():
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except Exception:
                pass
        self.writer = None
        self.reader = None
        self.room_id = None

    async def disconnect(self):
        self.running = False
        if self._response_task and not self._response_task.done():
            await self.cancel_active_response("adapter shutting down")
        await self._close_connection()

    # ------------------------------------------------------------------
    # Helper: run subprocess non-blocking
    # ------------------------------------------------------------------

    def _schedule_message_handling(self, message: dict):
        if self._response_task and not self._response_task.done():
            self._message_queue.append(message)  # drops oldest if maxlen exceeded
            return

        should_respond = getattr(self, "_should_respond", None)
        if callable(should_respond):
            try:
                if not bool(should_respond(message)):
                    return
            except Exception as e:
                self.log.error("Trigger evaluation failed: %s", e)
                return

        task = asyncio.create_task(self.handle_message(message))
        self._response_task = task
        task.add_done_callback(self._on_response_task_done)

    def _on_response_task_done(self, task: asyncio.Task):
        if self._response_task is task:
            self._response_task = None
        try:
            task.result()
        except asyncio.CancelledError:
            self.log.info("Response task cancelled")
        except Exception as e:
            self.log.error("Response task failed: %s", e)
        # Process any queued message that arrived while we were busy
        if self._message_queue:
            next_msg = self._message_queue.popleft()
            self._schedule_message_handling(next_msg)

    def _claim_response_message(self, message: dict) -> bool:
        msg_id = str(message.get("id", "")).strip()
        if not msg_id:
            return True
        if msg_id in self._handled_response_message_ids:
            return False
        self._handled_response_message_ids.add(msg_id)
        self._handled_response_message_order.append(msg_id)
        if len(self._handled_response_message_order) > self._handled_response_cap:
            old = self._handled_response_message_order.pop(0)
            self._handled_response_message_ids.discard(old)
        return True

    def consume_cancel_reason(self) -> str:
        reason = self._cancel_reason
        self._cancel_reason = ""
        return reason

    async def _terminate_current_process(self):
        proc = self._current_process
        if not proc or proc.returncode is not None:
            return
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            proc.kill()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(proc.wait(), timeout=2.0)

    async def cancel_active_response(self, reason: str = "cancelled") -> bool:
        task = self._response_task
        if not task or task.done():
            return False
        self._cancel_reason = str(reason or "").strip() or "cancelled"
        await self._terminate_current_process()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        return True

    async def run_subprocess_capture(
        self,
        cmd: List[str],
        input_text: Optional[str] = None,
        timeout: float = 60.0,
        cwd: Optional[Path] = None,
    ) -> tuple[Optional[int], str, str, Optional[str]]:
        """
        Run a subprocess without blocking the event loop.

        Returns (returncode, stdout, stderr, error_kind) where error_kind is
        one of None, "timeout", or "error". Cancellation is re-raised so
        callers can surface user-triggered stop requests cleanly.
        """
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=(
                    asyncio.subprocess.PIPE
                    if input_text is not None
                    else asyncio.subprocess.DEVNULL
                ),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(cwd) if cwd else None,
            )
        except Exception as e:
            self.log.error("Subprocess %s failed to start: %s", cmd[0], e)
            return None, "", "", "error"

        self._current_process = process
        try:
            stdout_data, stderr_data = await asyncio.wait_for(
                process.communicate(
                    input_text.encode() if input_text is not None else None
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            await self._terminate_current_process()
            self.log.warning("Subprocess %s timed out after %ss", cmd[0], timeout)
            return None, "", "", "timeout"
        except asyncio.CancelledError:
            await self._terminate_current_process()
            raise
        except Exception as e:
            await self._terminate_current_process()
            self.log.error("Subprocess %s failed: %s", cmd[0], e)
            return None, "", "", "error"
        finally:
            self._current_process = None

        stdout = stdout_data.decode(errors="replace").strip()
        stderr = stderr_data.decode(errors="replace").strip()
        return process.returncode, stdout, stderr, None

    async def run_subprocess(
        self,
        cmd: List[str],
        input_text: Optional[str] = None,
        timeout: float = 60.0,
        cwd: Optional[Path] = None,
    ) -> Optional[str]:
        """Run a subprocess without blocking the event loop."""
        returncode, stdout, stderr, error_kind = await self.run_subprocess_capture(
            cmd,
            input_text=input_text,
            timeout=timeout,
            cwd=cwd,
        )
        if error_kind is not None:
            return None
        if returncode == 0:
            return stdout
        self.log.warning(
            "Subprocess %s exited %s: %s",
            cmd[0],
            returncode,
            stderr[:200],
        )
        return None

    def agent_message_matches(
        self,
        content: str,
        mention_triggers: set[str] | frozenset[str],
        plaintext_triggers: set[str] | frozenset[str] = frozenset(),
        *,
        allow_plaintext: bool = False,
    ) -> bool:
        triggers = set(mention_triggers)
        if allow_plaintext:
            triggers.update(plaintext_triggers)
        return self.has_any_trigger(content, frozenset(triggers))

    @staticmethod
    def is_agent_message(message: dict) -> bool:
        sender = message.get("sender", {})
        return str(sender.get("type", "")).lower() != "human"

    @staticmethod
    def sender_type(message: dict) -> str:
        sender = message.get("sender", {})
        return str(sender.get("type", "")).lower()
