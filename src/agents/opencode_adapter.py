# maps · cassette.help · MIT
"""
OpenCode Adapter for Polycule Hub

OpenCode is an open-source AI coding assistant. This adapter connects it to
the Polycule hub using `opencode run "prompt"` for non-interactive execution.

Usage:
    python3 opencode_adapter.py --name OpenCode --room Default
    python3 opencode_adapter.py --name OpenCode --always
    python3 opencode_adapter.py --name OpenCode --resume SESSION_ID
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
from agents.base_adapter import BaseAdapter, AgentConfig

logger = logging.getLogger(__name__)

OPENCODE_BIN = "opencode"

TRIGGER_PHRASES = frozenset({"@opencode", "opencode", "hey opencode"})

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
        resume_session: Optional[str] = None,
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
        self.resume_session = resume_session
        self._responding = False

    def _build_cmd(self, prompt: str) -> list:
        cmd = [OPENCODE_BIN, "run"]
        if self.resume_session:
            cmd.extend(["--resume", self.resume_session])
        cmd.append(prompt)
        return cmd

    def _should_respond(self, message: dict) -> bool:
        if self._responding:
            return False
        sender = message.get("sender", {})
        if sender.get("type") == "opencode":
            return False
        if sender.get("name", "").lower() == self.config.name.lower():
            return False
        if self.always_respond:
            return True
        content = message.get("content", "").lower()
        return any(t in content for t in TRIGGER_PHRASES)

    def _build_context_log(self) -> str:
        lines = []
        for m in self.context_messages[-40:]:
            if not isinstance(m, dict) or m.get("type") != "message":
                continue
            sender = m.get("sender", {})
            lines.append(f"[{sender.get('name', '?')}]: {m.get('content', '')}")
        return "\n".join(lines) if lines else "(no prior messages)"

    def _build_prompt(self, message: dict) -> str:
        sender = message.get("sender", {})
        content = message.get("content", "")
        context = self._build_context_log()
        return (
            f"{SYSTEM_PROMPT}\n\n"
            f"--- Recent chat log ---\n{context}\n--- End of log ---\n\n"
            f"{sender.get('name', '?')} says: {content}\n\n"
            f"Your response:"
        )

    async def _call_opencode(self, prompt: str) -> Optional[str]:
        cmd = self._build_cmd(prompt)
        logger.info(f"Calling opencode run ({len(prompt)} char prompt)")
        result = await self.run_subprocess(cmd, timeout=120.0)
        return result

    async def handle_message(self, message: dict):
        if not self._should_respond(message):
            return

        self._responding = True
        try:
            prompt = self._build_prompt(message)
            response = await self._call_opencode(prompt)
            if response:
                logger.info(f"Responding ({len(response)} chars)")
                await self.send_message(response)
            else:
                logger.warning("OpenCode returned no response")
        finally:
            self._responding = False

    async def handle_context_dump(self, messages: list):
        logger.info(f"OpenCode context loaded: {len(messages)} historical messages")


def main():
    parser = argparse.ArgumentParser(description="OpenCode adapter for Polycule")
    parser.add_argument("--name", default="OpenCode")
    parser.add_argument("--room", default="Default")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=7777)
    parser.add_argument("--always", action="store_true", help="Respond to all messages")
    parser.add_argument(
        "--resume",
        default=None,
        metavar="SESSION_ID",
        help="Resume a prior opencode session by ID",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format=f"[%(asctime)s] [opencode/%(levelname)s] %(message)s",
    )

    adapter = OpenCodeAdapter(
        name=args.name,
        room=args.room,
        hub_host=args.host,
        hub_port=args.port,
        always_respond=args.always,
        resume_session=args.resume,
    )
    asyncio.run(adapter.run())


if __name__ == "__main__":
    main()
