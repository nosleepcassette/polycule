# Polycule · MIT
"""
Hermes Adapter for Polycule Hub

Hermes is a TUI REPL (like Claude Code). It has no persistent session —
each invocation starts fresh unless --resume SESSION_ID is passed.

Hermes profiles are discovered from ~/.hermes and can be launched as named
Polycule agents. The default Hermes profile uses the root ~/.hermes config;
named Hermes profiles use ~/.hermes/profiles/<profile>.

This adapter calls Hermes in non-interactive, quiet mode (-Q -q) for each
message it responds to. Context from the chat log is injected into the prompt
so the stateless agent has the necessary background.

Optionally pass --resume SESSION_ID to resume a prior session.

Usage:
    # Run the default Hermes profile
    python3 hermes_adapter.py --name hermes --profile default --room Default

    # Run a named Hermes profile
    python3 hermes_adapter.py --name analyst --profile analyst --room Default

    # Respond to all messages (not just mentions)
    python3 hermes_adapter.py --name analyst --profile analyst --always

    # Resume a prior session
    python3 hermes_adapter.py --name analyst --profile analyst --resume abc123
"""

import argparse
import asyncio
import logging
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
from agents.base_adapter import BaseAdapter, AgentConfig
from hermes_sessions import (
    hermes_session_exists,
    newest_hermes_session_id,
    normalize_hermes_profile,
    rename_hermes_session,
    snapshot_hermes_sessions,
)
from managed_agents import get_managed_agent_names
from runtime_state import (
    clear_agent_session_entry,
    get_agent_session_entry,
    get_or_allocate_agent_session_title,
    make_agent_session_key,
    normalize_session_title,
    update_agent_session_entry,
)

logger = logging.getLogger(__name__)

HERMES_BIN = Path.home() / ".hermes" / "bin" / "hermes"
HERMES_FALLBACK = shutil.which("hermes") or "hermes"
DEFAULT_HERMES_TIMEOUT_SECONDS = 240.0
BOOTSTRAP_CONTEXT_LIMIT = 60
RESUME_FALLBACK_CONTEXT_LIMIT = 20
_RESUME_BANNER_RE = re.compile(
    r"^↻ Resumed session\s+(?P<session_id>\S+)(?:\s+\"(?P<title>[^\"]+)\")?"
)
_TRAILING_SESSION_ID_RE = re.compile(
    r"^\s*session_id:\s*(?P<session_id>\S+)\s*$",
    re.MULTILINE,
)
_ANY_MENTION_RE = re.compile(r"@[\w-]+")


def _trigger_sets(name: str, profile: str) -> tuple[frozenset[str], frozenset[str]]:
    mentions = {f"@{name.strip().lower()}"}
    words = {name.strip().lower()}

    normalized_profile = normalize_hermes_profile(profile)
    if normalized_profile not in {"", "default"}:
        mentions.add(f"@{normalized_profile}")
        words.add(normalized_profile)
    elif name.strip().lower() != "hermes":
        mentions.add("@hermes")
        words.add("hermes")

    return frozenset(item for item in mentions if item and item != "@"), frozenset(
        item for item in words if item
    )


def _system_prompt(name: str, profile: str) -> str:
    normalized_profile = normalize_hermes_profile(profile)
    profile_label = "default" if normalized_profile == "default" else normalized_profile
    return (
        f"You are {name}, a Hermes AI agent participating in the Polycule "
        f"multi-agent workspace. Your Hermes profile is '{profile_label}'. "
        f"Collaborate with the human operator and any other agents in the room. "
        f"Be concise, direct, and technically precise. Respond only when addressed "
        f"or when your response mode/watch policy says to."
    )


class HermesAdapter(BaseAdapter):
    """
    Hermes adapter — supports the default Hermes profile plus any named profile.
    Calls `hermes chat -Q -q "prompt"` for each response.
    """

    def __init__(
        self,
        name: str = "Hermes",
        profile: str = "default",
        room: str = "Default",
        hub_host: str = "localhost",
        hub_port: int = 7777,
        always_respond: bool = False,
        always_all: bool = False,
        agent_handoffs: bool = False,
        resume_session: Optional[str] = None,
        session_title: Optional[str] = None,
        timeout_seconds: float = DEFAULT_HERMES_TIMEOUT_SECONDS,
    ):
        config = AgentConfig(
            name=name,
            agent_type="hermes",
            room_name=room,
            hub_host=hub_host,
            hub_port=hub_port,
        )
        super().__init__(config)
        self.profile = normalize_hermes_profile(profile)
        self.always_respond = always_respond
        self.always_all = always_all
        self.agent_handoffs = agent_handoffs
        self.resume_session = resume_session
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self._responding = False
        self._mention_triggers, self._human_word_triggers = _trigger_sets(
            self.config.name,
            self.profile,
        )
        self._system_prompt = _system_prompt(self.config.name, self.profile)
        self.session_key = make_agent_session_key(
            "hermes",
            room,
            profile=self.profile,
        )
        self.session_title = normalize_session_title(session_title)
        self.last_acknowledged_message_id: Optional[str] = None
        self._hydrate_managed_session_state()
        if not self.session_title or self.session_title.lower().startswith("polycule:"):
            self.session_title = get_or_allocate_agent_session_title(self.session_key)
        self._last_session_event_id = self.resume_session

    # -----------------------------------------------------------------------
    # Build hermes command
    # -----------------------------------------------------------------------

    def _build_cmd(self, prompt: str) -> list:
        """Build the hermes chat invocation for non-interactive one-shot use."""
        hermes_bin = str(HERMES_BIN) if HERMES_BIN.exists() else HERMES_FALLBACK
        cmd = [hermes_bin, "chat", "-Q", "-q", prompt]
        if self.profile not in ("default", ""):
            cmd = [
                hermes_bin,
                "chat",
                "--profile",
                self.profile,
                "-Q",
                "-q",
                prompt,
            ]
        if self.resume_session:
            cmd += ["--resume", self.resume_session]
        return cmd

    # -----------------------------------------------------------------------
    # Trigger logic
    # -----------------------------------------------------------------------

    def _exclusively_addressed_to_other(self, content: str) -> bool:
        """True if message mentions another agent but not this agent.

        Used to prevent always-mode agents from responding to messages
        that are clearly directed at a different agent.
        """
        my_triggers = self._mention_triggers | self._human_word_triggers
        if self.has_any_trigger(content, my_triggers):
            return False  # message includes me — respond
        if _ANY_MENTION_RE.search(content.lower()):
            return True
        other_triggers = {
            token.lower()
            for token in get_managed_agent_names()
            if token.strip().lower() not in my_triggers
        } | frozenset({"codex", "claude", "opencode", "gemini"})
        return self.has_any_trigger(content, other_triggers)

    def _should_respond(self, message: dict) -> bool:
        if self._responding:
            return False
        sender = message.get("sender", {})
        if sender.get("type") == "hermes":
            return False
        if sender.get("name", "").lower() == self.config.name.lower():
            return False
        sender_type = sender.get("type", "").lower()
        content = message.get("content", "")

        if self.always_all:
            return True
        if self.always_respond:
            if sender_type != "human":
                return False
            # Skip if message is explicitly addressed to a different agent
            return not self._exclusively_addressed_to_other(content)
        if self._watch_matches_message(message):
            return True

        if sender_type == "human":
            return self.has_any_trigger(
                content, self._mention_triggers | self._human_word_triggers
            )
        return self.agent_message_matches(
            content,
            self._mention_triggers,
            self._human_word_triggers,
            allow_plaintext=self.agent_handoffs,
        )

    # -----------------------------------------------------------------------
    # Prompt construction
    # -----------------------------------------------------------------------

    def _hydrate_managed_session_state(self):
        entry = get_agent_session_entry(self.session_key)
        if not entry:
            return

        stored_session_id = str(entry.get("session_id", "")).strip()
        if stored_session_id and not hermes_session_exists(self.profile, stored_session_id):
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

    @staticmethod
    def _sanitize_hermes_output(output: str) -> tuple[str, dict[str, object]]:
        text = output or ""
        metadata: dict[str, object] = {}

        trailing_ids = [
            match.group("session_id")
            for match in _TRAILING_SESSION_ID_RE.finditer(text)
        ]
        if trailing_ids:
            metadata["session_id"] = trailing_ids[-1]
            text = _TRAILING_SESSION_ID_RE.sub("", text)

        lines = text.splitlines()
        while lines and not lines[0].strip():
            lines.pop(0)

        if lines and lines[0].lstrip().startswith("↻ Resumed session "):
            banner = lines.pop(0).strip()
            match = _RESUME_BANNER_RE.match(banner)
            if match:
                metadata["resumed"] = True
                metadata.setdefault("session_id", match.group("session_id"))
                if match.group("title"):
                    metadata["session_title"] = match.group("title")
            if lines:
                next_line = lines[0].strip()
                if next_line and (
                    "total messages)" in next_line
                    or next_line.startswith("message,")
                    or next_line.endswith("messages)")
                ):
                    lines.pop(0)

        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()

        return "\n".join(lines).strip(), metadata

    def _persist_session_state(
        self,
        *,
        session_id: Optional[str] = None,
        last_message_id: Optional[str] = None,
    ):
        update_agent_session_entry(
            self.session_key,
            agent_family="hermes",
            profile=self.profile,
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
        session_snapshot: Optional[dict[str, float]],
        *,
        output_session_id: Optional[str] = None,
        output_session_title: Optional[str] = None,
    ) -> tuple[Optional[str], Optional[str]]:
        previous_session_id = self.resume_session
        detected_session_id = (output_session_id or self.resume_session or "").strip()
        if not detected_session_id:
            detected_session_id = newest_hermes_session_id(
                self.profile,
                changed_since=session_snapshot,
            ) or ""
        if not detected_session_id:
            detected_session_id = newest_hermes_session_id(self.profile) or ""
        if not detected_session_id:
            return None, None

        self.resume_session = detected_session_id
        observed_title = normalize_session_title(output_session_title)
        if detected_session_id != previous_session_id or (
            observed_title and observed_title != self.session_title
        ):
            rename_hermes_session(detected_session_id, self.session_title)
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
        for m in prompt_messages:
            sender = m.get("sender", {})
            lines.append(f"[{sender.get('name', '?')}]: {m.get('content', '')}")
        return ("\n".join(lines) if lines else "(no prior messages)"), scope

    def _build_prompt(self, message: dict) -> str:
        sender = message.get("sender", {})
        sender_name = sender.get("name", "Unknown")
        content = message.get("content", "")
        context, scope = self._build_context_log(message)
        section_header = "Recent chat log"
        if scope == "delta":
            section_header = "New chat messages since your last handled turn"
        elif scope.startswith("resume_"):
            section_header = "Recent chat messages to refresh local context"
        return (
            f"{self._system_prompt}\n\n"
            f"--- {section_header} ---\n{context}\n--- End of log ---\n\n"
            f"{sender_name} says: {content}\n\n"
            f"Your response:"
        )

    # -----------------------------------------------------------------------
    # Hermes call
    # -----------------------------------------------------------------------

    async def _call_hermes(self, prompt: str) -> tuple[Optional[str], str, float, str]:
        started = time.monotonic()
        session_snapshot = None

        # Broadcast typing started
        if self.room_id:
            await self.send_command("agent_typing", is_typing=True)

        hermes_bin = str(HERMES_BIN) if HERMES_BIN.exists() else shutil.which("hermes")
        if not hermes_bin:
            detail = "Hermes binary not found. Install Hermes or add `hermes` to PATH."
            logger.error(detail)
            return None, "missing_bin", 0.0, detail

        if not self.resume_session:
            session_snapshot = snapshot_hermes_sessions(self.profile)
        cmd = self._build_cmd(prompt)
        logger.info(
            f"Calling hermes ({self.profile} profile, {len(prompt)} char prompt)"
        )

        try:
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            elapsed = time.monotonic() - started
            detail = (
                f"{self.profile} profile timed out after {self.timeout_seconds:.1f}s"
            )
            logger.warning(detail)
            # Broadcast tool failure
            if self.room_id:
                await self.send_command("agent_typing", is_typing=False)
                await self.send_command(
                    "agent_tool_use", tool_name="hermes", status="failed"
                )
            return None, "timeout", elapsed, detail
        except Exception as e:
            elapsed = time.monotonic() - started
            detail = f"Hermes execution failed: {e}"
            logger.error(detail)
            # Broadcast error
            if self.room_id:
                await self.send_command("agent_typing", is_typing=False)
                await self.send_command(
                    "agent_tool_use", tool_name="hermes", status="failed"
                )
            return None, "error", elapsed, detail

        elapsed = time.monotonic() - started
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            detail = (
                f"Hermes exited {result.returncode}: {(stderr or 'no stderr')[:200]}"
            )
            logger.warning(detail)
            return None, "error", elapsed, detail

        output, output_meta = self._sanitize_hermes_output((result.stdout or "").strip())
        session_id, session_event = self._capture_session_id(
            session_snapshot,
            output_session_id=str(output_meta.get("session_id", "")).strip() or None,
            output_session_title=str(output_meta.get("session_title", "")).strip() or None,
        )
        if session_event and session_id:
            await self._emit_session_event(session_event, session_id)
        if not output:
            # Broadcast typing stopped
            if self.room_id:
                await self.send_command("agent_typing", is_typing=False)
            return None, "empty", elapsed, "Hermes returned no response text"

        # Broadcast typing stopped and tool complete
        if self.room_id:
            await self.send_command("agent_typing", is_typing=False)
            await self.send_command(
                "agent_tool_use", tool_name="hermes", status="completed"
            )

        return output, "ok", elapsed, ""

    # -----------------------------------------------------------------------
    # Message handlers
    # -----------------------------------------------------------------------

    async def handle_message(self, message: dict):
        if not self._should_respond(message):
            return

        self._responding = True
        try:
            prompt = self._build_prompt(message)
            await self.send_status(
                "responding",
                f"{self.profile} profile call started (timeout {self.timeout_seconds:.0f}s)",
            )
            response, state, elapsed, detail = await self._call_hermes(prompt)
            if state == "ok" and response:
                logger.info(f"Responding ({len(response)} chars)")
                await self.send_message(response)
                message_id = str(message.get("id", "")).strip()
                if message_id:
                    self._persist_session_state(last_message_id=message_id)
            elif state == "timeout":
                await self.send_status("timeout", detail)
            elif state == "empty":
                logger.warning("Hermes returned no response")
                await self.send_status(
                    "no_response",
                    f"{self.profile} profile returned no response after {elapsed:.1f}s",
                )
            else:
                await self.send_status(
                    "error", detail or f"{self.profile} profile call failed"
                )
        finally:
            self._responding = False

    async def handle_context_dump(self, messages: list):
        logger.info(f"Context loaded: {len(messages)} historical messages")

    async def handle_approval_request(self, message: dict):
        req_id = message.get("request_id", "?")
        command = message.get("command", "?")
        requester = message.get("requester", "?")
        logger.info(
            f"Approval request {req_id}: {requester} wants {command} (awaiting human approval)"
        )

    def _on_mode_changed(self, mode: str):
        self.always_respond = mode == "always"
        self.always_all = mode == "ffa"
        self.agent_handoffs = mode == "handoff"
        logger.info(
            "%s mode updated -> %s (always=%s ffa=%s handoff=%s)",
            self.profile,
            mode,
            self.always_respond,
            self.always_all,
            self.agent_handoffs,
        )

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
        mention = next(iter(sorted(self._mention_triggers)), f"@{self.config.name.lower()}")
        content = str(message.get("content", "")).strip()
        refs = message.get("refs", [])
        if isinstance(refs, list) and refs:
            content += "\n\nRefs:\n" + "\n".join(f"- {item}" for item in refs if item)
        synthetic = {
            "type": "message",
            "sender": {
                "id": str(message.get("issued_by_id", "")),
                "name": str(message.get("issued_by", "human")),
                "type": str(message.get("issued_by_type", "human")),
            },
            "content": f"{mention} directive ({message.get('directive_kind', 'brief')}):\n{content}",
        }
        await self.handle_message(synthetic)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Hermes adapter for Polycule")
    parser.add_argument(
        "--name",
        default=None,
        help="Display name (defaults to 'hermes' for default profile or the profile name)",
    )
    parser.add_argument(
        "--profile",
        default="default",
        help="Hermes profile to run (default: default)",
    )
    parser.add_argument("--room", default="Default")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=7777)
    parser.add_argument(
        "--always",
        action="store_true",
        help="Respond to all human messages, not just mentions",
    )
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
    parser.add_argument(
        "--resume",
        default=None,
        metavar="SESSION_ID",
        help="Resume a prior hermes session by ID",
    )
    parser.add_argument("--session-title", default=None)
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_HERMES_TIMEOUT_SECONDS,
        help=f"Seconds to wait for hermes response (default: {DEFAULT_HERMES_TIMEOUT_SECONDS:.0f})",
    )
    args = parser.parse_args()

    if args.name is None:
        normalized_profile = normalize_hermes_profile(args.profile)
        args.name = "hermes" if normalized_profile == "default" else normalized_profile

    logging.basicConfig(
        level=logging.INFO,
        format=f"[%(asctime)s] [{args.name}/%(levelname)s] %(message)s",
    )

    adapter = HermesAdapter(
        name=args.name,
        profile=args.profile,
        room=args.room,
        hub_host=args.host,
        hub_port=args.port,
        always_respond=args.always,
        always_all=args.always_all,
        agent_handoffs=args.agent_handoffs,
        resume_session=args.resume,
        session_title=args.session_title,
        timeout_seconds=args.timeout,
    )
    asyncio.run(adapter.run())


if __name__ == "__main__":
    main()
