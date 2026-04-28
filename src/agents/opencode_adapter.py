# Polycule · MIT
"""
OpenCode Adapter for Polycule Hub

OpenCode is an open-source AI coding assistant. This adapter connects it to
the Polycule hub using `opencode run` while persisting and reusing the backing
session between turns.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
from agents.base_adapter import AgentConfig, BaseAdapter
from runtime_state import (
    clear_agent_session_entry,
    get_agent_session_entry,
    get_or_allocate_agent_session_title,
    make_agent_session_key,
    normalize_session_title,
    update_agent_session_entry,
)
from session_backends import opencode_session_exists, newest_opencode_session_id, snapshot_opencode_sessions

logger = logging.getLogger(__name__)

OPENCODE_BIN = "opencode"
BOOTSTRAP_CONTEXT_LIMIT = 40
RESUME_FALLBACK_CONTEXT_LIMIT = 12

MENTION_TRIGGERS = frozenset({"@opencode"})
HUMAN_WORD_TRIGGERS = frozenset({"opencode"})

SYSTEM_PROMPT = """You are OpenCode, an AI coding agent participating in the Polycule
multi-agent workspace. You specialize in code generation, debugging, and technical
implementation. Respond to messages directed at you. Be concise and technical.
When providing code, wrap it in appropriate fences."""


class OpenCodeAdapter(BaseAdapter):
    """OpenCode non-interactive adapter using `opencode run`."""

    def __init__(
        self,
        name: str = "OpenCode",
        room: str = "Default",
        hub_host: str = "localhost",
        hub_port: int = 7777,
        always_respond: bool = False,
        always_all: bool = False,
        agent_handoffs: bool = False,
        resume_session: Optional[str] = None,
        session_title: Optional[str] = None,
    ):
        config = AgentConfig(
            name=name,
            agent_type="opencode",
            room_name=room,
            hub_host=hub_host,
            hub_port=hub_port,
        )
        super().__init__(config)
        self.always_respond = always_respond
        self.always_all = always_all
        self.agent_handoffs = agent_handoffs
        self.resume_session = resume_session
        self._responding = False
        self._cwd = str(Path.cwd().resolve())
        self.session_key = make_agent_session_key("opencode", room)
        self.session_title = normalize_session_title(session_title)
        self.last_acknowledged_message_id = ""
        self._last_session_event_id = self.resume_session

        self._load_saved_session_state()
        if not self.session_title or self.session_title.lower().startswith("polycule:"):
            self.session_title = get_or_allocate_agent_session_title(self.session_key)

    def _build_cmd(self, prompt: str) -> list[str]:
        cmd = [OPENCODE_BIN, "run"]
        if self.resume_session:
            cmd.extend(["--session", self.resume_session])
        else:
            cmd.extend(["--title", self.session_title])
        cmd.append(prompt)
        return cmd

    def _load_saved_session_state(self):
        entry = get_agent_session_entry(self.session_key)
        if not entry:
            return

        stored_session_id = str(entry.get("session_id", "")).strip()
        if stored_session_id and not opencode_session_exists(stored_session_id):
            clear_agent_session_entry(self.session_key)
            return

        stored_title = normalize_session_title(entry.get("title", ""))
        if not self.session_title and stored_title:
            self.session_title = stored_title
        if not self.resume_session and stored_session_id:
            self.resume_session = stored_session_id
        stored_last_message_id = str(entry.get("last_message_id", "")).strip()
        if stored_last_message_id:
            self.last_acknowledged_message_id = stored_last_message_id

    def _persist_session_state(
        self,
        *,
        session_id: Optional[str] = None,
        last_message_id: Optional[str] = None,
    ):
        update_agent_session_entry(
            self.session_key,
            agent_family="opencode",
            profile="opencode",
            room=self.config.room_name,
            agent_name=self.config.name,
            title=self.session_title,
            session_id=session_id or self.resume_session,
            last_message_id=last_message_id,
        )
        if last_message_id:
            self.last_acknowledged_message_id = last_message_id

    def _capture_session_id(
        self,
        session_snapshot: Optional[dict[str, int]],
    ) -> tuple[Optional[str], Optional[str]]:
        previous_session_id = self.resume_session
        detected_session_id = (self.resume_session or "").strip()
        if not detected_session_id:
            detected_session_id = (
                newest_opencode_session_id(
                    self._cwd,
                    changed_since=session_snapshot,
                    title=self.session_title,
                )
                or ""
            )
        if not detected_session_id:
            detected_session_id = (
                newest_opencode_session_id(
                    self._cwd,
                    title=self.session_title,
                )
                or ""
            )
        if not detected_session_id:
            return None, None

        self.resume_session = detected_session_id
        self._persist_session_state(session_id=detected_session_id)

        if not previous_session_id:
            return detected_session_id, "created"
        if detected_session_id != previous_session_id:
            return detected_session_id, "changed"
        return detected_session_id, None

    async def _emit_session_event(self, state: str, session_id: str):
        if not self.room_id or not session_id:
            return
        if session_id == self._last_session_event_id:
            return
        await self.send_command(
            "agent_session",
            state=state,
            session_id=session_id,
            session_title=self.session_title,
        )
        self._last_session_event_id = session_id

    def _prompt_context_messages(self, message: dict) -> tuple[list[dict], str]:
        messages = [
            m
            for m in self.context_messages
            if isinstance(m, dict) and m.get("type") == "message"
        ]
        if not messages:
            return [], "bootstrap"
        if not self.resume_session:
            return messages[-BOOTSTRAP_CONTEXT_LIMIT:], "bootstrap"
        if not self.last_acknowledged_message_id:
            return messages[-RESUME_FALLBACK_CONTEXT_LIMIT:], "resume_bootstrap"

        last_index = None
        for idx, ctx_msg in enumerate(messages):
            if ctx_msg.get("id") == self.last_acknowledged_message_id:
                last_index = idx
                break
        if last_index is None:
            return messages[-RESUME_FALLBACK_CONTEXT_LIMIT:], "resume_fallback"

        delta = messages[last_index + 1 :]
        if not delta:
            return [message], "delta"
        return delta, "delta"

    def _build_context_log(self, message: dict) -> tuple[str, str]:
        prompt_messages, scope = self._prompt_context_messages(message)
        lines = []
        for item in prompt_messages:
            sender = item.get("sender", {})
            lines.append(f"[{sender.get('name', '?')}]: {item.get('content', '')}")
        return ("\n".join(lines) if lines else "(no prior messages)"), scope

    def _build_prompt(self, message: dict) -> str:
        sender = message.get("sender", {})
        content = message.get("content", "")
        context, scope = self._build_context_log(message)
        section_header = "Recent chat log"
        if scope == "delta":
            section_header = "New chat messages since your last handled turn"
        elif scope.startswith("resume_"):
            section_header = "Recent chat messages to refresh local context"
        return (
            f"{SYSTEM_PROMPT}\n\n"
            f"--- {section_header} ---\n{context}\n--- End of log ---\n\n"
            f"{sender.get('name', '?')} says: {content}\n\n"
            f"Your response:"
        )

    def _should_respond(self, message: dict) -> bool:
        if self._responding:
            return False
        sender = message.get("sender", {})
        if sender.get("type") == "opencode":
            return False
        if sender.get("name", "").lower() == self.config.name.lower():
            return False
        sender_type = self.sender_type(message)
        content = message.get("content", "")
        if self.always_all:
            return True
        if self.always_respond:
            return sender_type == "human"
        if self._watch_matches_message(message):
            return True
        if sender_type == "human":
            return self.has_any_trigger(content, MENTION_TRIGGERS | HUMAN_WORD_TRIGGERS)
        return self.agent_message_matches(
            content,
            MENTION_TRIGGERS,
            HUMAN_WORD_TRIGGERS,
            allow_plaintext=self.agent_handoffs,
        )

    async def _call_opencode(self, prompt: str) -> Optional[str]:
        cmd = self._build_cmd(prompt)
        logger.info("Calling opencode run (%s char prompt)", len(prompt))
        return await self.run_subprocess(cmd, timeout=120.0)

    async def handle_message(self, message: dict):
        if not self._should_respond(message):
            return
        if not self._claim_response_message(message):
            return

        self._responding = True
        session_snapshot = None
        if not self.resume_session:
            session_snapshot = snapshot_opencode_sessions(
                self._cwd,
                title=self.session_title,
            )
        try:
            prompt = self._build_prompt(message)
            await self.set_typing(True)
            response = await self._call_opencode(prompt)
            if response:
                session_id, session_event = self._capture_session_id(session_snapshot)
                if session_event and session_id:
                    await self._emit_session_event(session_event, session_id)
                logger.info("Responding (%s chars)", len(response))
                await self.send_message(response)
                message_id = str(message.get("id", "")).strip()
                if message_id:
                    self._persist_session_state(last_message_id=message_id)
            else:
                logger.warning("OpenCode returned no response")
        except asyncio.CancelledError:
            detail = self.consume_cancel_reason() or "response cancelled"
            logger.info("OpenCode response cancelled: %s", detail)
            await self.send_status("cancelled", detail)
            raise
        finally:
            await self.set_typing(False)
            self._responding = False

    async def handle_context_dump(self, messages: list):
        logger.info("OpenCode context loaded: %s historical messages", len(messages))

    def _on_mode_changed(self, mode: str):
        self.always_respond = (mode == "always")
        self.always_all = (mode == "ffa")
        self.agent_handoffs = (mode == "handoff")
        logger.info("%s mode updated → %s", self.config.name, mode)

    async def handle_directive(self, message: dict):
        if not self._directive_targets_me(message):
            return
        directive_id = str(message.get("directive_id", "")).strip()
        if directive_id:
            await self.send_command(
                "ack_directive",
                directive_id=directive_id,
                state="accepted",
            )
        content = str(message.get("content", "")).strip()
        refs = message.get("refs", [])
        if isinstance(refs, list) and refs:
            content += "\n\nRefs:\n" + "\n".join(f"- {item}" for item in refs if item)
        synthetic = {
            "id": f"directive:{directive_id or 'unknown'}:opencode",
            "type": "message",
            "sender": {
                "id": str(message.get("issued_by_id", "")),
                "name": str(message.get("issued_by", "human")),
                "type": str(message.get("issued_by_type", "human")),
            },
            "content": f"@opencode directive ({message.get('directive_kind', 'brief')}):\n{content}",
        }
        self._schedule_message_handling(synthetic)


def main():
    parser = argparse.ArgumentParser(description="OpenCode adapter for Polycule")
    parser.add_argument("--name", default="OpenCode")
    parser.add_argument("--room", default="Default")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=7777)
    parser.add_argument("--always", action="store_true", help="Respond to all human messages")
    parser.add_argument(
        "--always-all",
        action="store_true",
        help="Respond to all messages (unsafe: can trigger loops)",
    )
    parser.add_argument(
        "--agent-handoffs",
        action="store_true",
        help="Allow agent-to-agent plaintext handoffs without @mentions",
    )
    parser.add_argument("--session-title", default=None)
    parser.add_argument(
        "--resume",
        default=None,
        metavar="SESSION_ID",
        help="Resume a prior opencode session by ID",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [opencode/%(levelname)s] %(message)s",
    )

    adapter = OpenCodeAdapter(
        name=args.name,
        room=args.room,
        hub_host=args.host,
        hub_port=args.port,
        always_respond=args.always,
        always_all=args.always_all,
        agent_handoffs=args.agent_handoffs,
        session_title=args.session_title,
        resume_session=args.resume,
    )
    asyncio.run(adapter.run())


if __name__ == "__main__":
    main()
