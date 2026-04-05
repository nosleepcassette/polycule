# maps · cassette.help · MIT
"""
Codex Adapter for Polycule Hub

Codex is a TUI REPL with a non-interactive execution mode:
    codex exec "prompt"   — one-shot, prints response to stdout
    codex "prompt"        — interactive REPL with initial prompt

This adapter calls `codex exec "prompt"` for each message it responds to,
with the chat log context injected into the prompt.

Note: The `codex` shell function in maps' shell wraps the real codex binary
at /usr/local/bin/codex. This adapter calls /usr/local/bin/codex directly
to avoid the wrapper's post-run hooks (changelog/handover).

Usage:
    python3 codex_adapter.py [--name Codex] [--room Default]
    python3 codex_adapter.py --name Codex --always     # respond to everything
    python3 codex_adapter.py --name Codex --resume SESSION_ID
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

# Call the real codex binary, not the shell wrapper (avoids post-run hooks)
CODEX_BIN = Path('/usr/local/bin/codex')
CODEX_FALLBACK = 'codex'  # fall back to PATH if /usr/local/bin/codex missing

TRIGGER_WORDS = frozenset({'@codex', 'codex'})

SYSTEM_PROMPT = """You are Codex, an AI coding agent participating in the Polycule \
multi-agent workspace. You specialize in code generation, debugging, and technical \
implementation. Respond to messages directed at you. Be concise and technical. \
When providing code, wrap it in appropriate fences."""


class CodexAdapter(BaseAdapter):
    """Codex non-interactive adapter using `codex exec`."""

    def __init__(
        self,
        name: str = 'Codex',
        room: str = 'Default',
        hub_host: str = 'localhost',
        hub_port: int = 7777,
        always_respond: bool = False,
        resume_session: Optional[str] = None,
    ):
        config = AgentConfig(
            name=name,
            agent_type='codex',
            room_name=room,
            hub_host=hub_host,
            hub_port=hub_port,
        )
        super().__init__(config)
        self.always_respond = always_respond
        self.resume_session = resume_session
        self._responding = False

        # Resolve binary
        self._bin = str(CODEX_BIN) if CODEX_BIN.exists() else CODEX_FALLBACK

    # -----------------------------------------------------------------------
    # Build command
    # -----------------------------------------------------------------------

    def _build_cmd(self, prompt: str) -> list:
        cmd = [self._bin, 'exec', prompt]
        if self.resume_session:
            cmd = [self._bin, 'exec', '--resume', self.resume_session, prompt]
        return cmd

    # -----------------------------------------------------------------------
    # Trigger logic
    # -----------------------------------------------------------------------

    def _should_respond(self, message: dict) -> bool:
        if self._responding:
            return False
        sender = message.get('sender', {})
        if sender.get('type') == 'codex':
            return False
        if sender.get('name', '').lower() == self.config.name.lower():
            return False
        if self.always_respond:
            return True
        content = message.get('content', '').lower()
        return any(t in content for t in TRIGGER_WORDS)

    # -----------------------------------------------------------------------
    # Prompt construction
    # -----------------------------------------------------------------------

    def _build_context_log(self) -> str:
        lines = []
        for m in self.context_messages[-40:]:
            if not isinstance(m, dict) or m.get('type') != 'message':
                continue
            sender = m.get('sender', {})
            lines.append(f"[{sender.get('name', '?')}]: {m.get('content', '')}")
        return '\n'.join(lines) if lines else '(no prior messages)'

    def _build_prompt(self, message: dict) -> str:
        sender = message.get('sender', {})
        content = message.get('content', '')
        context = self._build_context_log()
        return (
            f"{SYSTEM_PROMPT}\n\n"
            f"--- Recent chat log ---\n{context}\n--- End of log ---\n\n"
            f"{sender.get('name', '?')} says: {content}\n\n"
            f"Your response:"
        )

    # -----------------------------------------------------------------------
    # Codex call
    # -----------------------------------------------------------------------

    async def _call_codex(self, prompt: str) -> Optional[str]:
        cmd = self._build_cmd(prompt)
        logger.info(f"Calling codex exec ({len(prompt)} char prompt)")
        result = await self.run_subprocess(cmd, timeout=120.0)
        return result

    # -----------------------------------------------------------------------
    # Message handlers
    # -----------------------------------------------------------------------

    async def handle_message(self, message: dict):
        if not self._should_respond(message):
            return

        self._responding = True
        try:
            prompt = self._build_prompt(message)
            response = await self._call_codex(prompt)
            if response:
                logger.info(f"Responding ({len(response)} chars)")
                await self.send_message(response)
            else:
                logger.warning("Codex returned no response")
        finally:
            self._responding = False

    async def handle_context_dump(self, messages: list):
        logger.info(f"Codex context loaded: {len(messages)} historical messages")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description='Codex adapter for Polycule')
    parser.add_argument('--name', default='Codex')
    parser.add_argument('--room', default='Default')
    parser.add_argument('--host', default='localhost')
    parser.add_argument('--port', type=int, default=7777)
    parser.add_argument('--always', action='store_true',
                        help='Respond to all messages')
    parser.add_argument('--resume', default=None, metavar='SESSION_ID',
                        help='Resume a prior codex session by ID')
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format=f'[%(asctime)s] [codex/%(levelname)s] %(message)s',
    )

    adapter = CodexAdapter(
        name=args.name,
        room=args.room,
        hub_host=args.host,
        hub_port=args.port,
        always_respond=args.always,
        resume_session=args.resume,
    )
    asyncio.run(adapter.run())


if __name__ == '__main__':
    main()
