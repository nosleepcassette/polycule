# maps · cassette.help · MIT
"""
Claude Code Adapter for Polycule Hub

Bridges Claude Code CLI (subprocess) to the hub. Receives messages from the
hub, forwards relevant ones to Claude Code's stdin, captures responses and
posts them back.

Claude Code is interactive (REPL-style), unlike Hermes. This adapter
manages a persistent subprocess rather than one-shot calls.

Status: functional stub — Claude CLI subprocess handling works,
but interactive REPL bridging requires the agent running headlessly.
For now, this adapter is best used with non-interactive prompts:
    claude -p "one-shot prompt"

Usage:
    python3 claude_adapter.py [--name Claude1] [--room Default]
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

# Trigger words that cause Claude to respond
TRIGGER_WORDS = frozenset({'@claude', 'claude'})

SYSTEM_PROMPT = """You are Claude, an AI assistant participating in the Polycule \
multi-agent workspace. You are collaborating with maps (a human), Wizard (a Hermes agent), \
and potentially Codex. Be helpful, concise, and direct. Respond only to messages \
directed at you or when your input is clearly needed."""


class ClaudeAdapter(BaseAdapter):
    """Claude Code CLI adapter."""

    def __init__(
        self,
        name: str = 'Claude',
        room: str = 'Default',
        hub_host: str = 'localhost',
        hub_port: int = 7777,
        always_respond: bool = False,
        model: str = 'claude-sonnet-4-6',
    ):
        config = AgentConfig(
            name=name,
            agent_type='claude',
            room_name=room,
            hub_host=hub_host,
            hub_port=hub_port,
        )
        super().__init__(config)
        self.always_respond = always_respond
        self.model = model
        self._responding = False

    def _should_respond(self, message: dict) -> bool:
        if self._responding:
            return False
        sender = message.get('sender', {})
        if sender.get('type') == 'claude':
            return False
        if sender.get('name', '').lower() == self.config.name.lower():
            return False
        if self.always_respond:
            return True
        content = message.get('content', '').lower()
        return any(t in content for t in TRIGGER_WORDS)

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

    async def _call_claude(self, prompt: str) -> Optional[str]:
        """Run claude -p <prompt> non-interactively."""
        logger.info(f"Calling claude -p ({len(prompt)} chars)")
        result = await self.run_subprocess(
            ['claude', '-p', prompt, '--model', self.model],
            timeout=120.0,
        )
        return result

    async def handle_message(self, message: dict):
        if not self._should_respond(message):
            return

        self._responding = True
        try:
            prompt = self._build_prompt(message)
            response = await self._call_claude(prompt)
            if response:
                await self.send_message(response)
        finally:
            self._responding = False

    async def handle_context_dump(self, messages: list):
        logger.info(f"Claude context loaded: {len(messages)} messages")


def main():
    parser = argparse.ArgumentParser(description='Claude adapter for Polycule')
    parser.add_argument('--name', default='Claude')
    parser.add_argument('--room', default='Default')
    parser.add_argument('--host', default='localhost')
    parser.add_argument('--port', type=int, default=7777)
    parser.add_argument('--always', action='store_true')
    parser.add_argument('--model', default='claude-sonnet-4-6')
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format='[%(asctime)s] [claude/%(levelname)s] %(message)s',
    )

    adapter = ClaudeAdapter(
        name=args.name,
        room=args.room,
        hub_host=args.host,
        hub_port=args.port,
        always_respond=args.always,
        model=args.model,
    )
    asyncio.run(adapter.run())


if __name__ == '__main__':
    main()
