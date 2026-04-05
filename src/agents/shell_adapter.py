# maps · cassette.help · MIT
"""
Shell Adapter for Polycule Hub

Connects any CLI-based AI tool to the hub. The command is called with the
prompt on stdin; the response is read from stdout. Works with ollama, llm,
custom scripts, or any tool that follows this pattern.

Usage:
    python3 shell_adapter.py --name Mistral --command "ollama run mistral" --room Main
    python3 shell_adapter.py --name GPT --command "llm -m gpt-4o" --room Main --always
    python3 shell_adapter.py --name MyBot --command "/path/to/script.sh" --trigger "@mybot"

Protocol:
    Prompt is written to the process stdin, then stdin is closed.
    Everything the process writes to stdout is collected as the response.
    Stderr is ignored (discarded).
"""
import argparse
import asyncio
import logging
import shlex
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
from agents.base_adapter import BaseAdapter, AgentConfig as BaseAgentConfig

logger = logging.getLogger(__name__)

CONTEXT_MESSAGES = 40


class ShellAdapter(BaseAdapter):
    def __init__(
        self,
        name: str,
        command: str,
        room: str = "Main",
        host: str = "localhost",
        port: int = 7777,
        triggers: list[str] | None = None,
        always: bool = False,
        resume_session: str | None = None,
    ):
        cfg = BaseAgentConfig(
            name=name,
            agent_type="shell",
            hub_host=host,
            hub_port=port,
            room_name=room,
        )
        super().__init__(cfg)
        self.command = command
        self.always = always
        self.resume_session = resume_session
        self._triggers = frozenset(t.lower() for t in (triggers or [f"@{name.lower()}", name.lower()]))

    def should_respond(self, message: dict) -> bool:
        sender = message.get("sender", {})
        if sender.get("name", "").lower() == self.config.name.lower():
            return False
        if sender.get("type") == "human":
            return True
        content = message.get("content", "").lower()
        if self.always:
            return True
        return any(t in content for t in self._triggers)

    async def generate_response(self, message: dict) -> Optional[str]:
        prompt = self._build_prompt(message)
        return await self._call_command(prompt)

    def _build_prompt(self, message: dict) -> str:
        lines = []
        if self.context_messages:
            recent = self.context_messages[-CONTEXT_MESSAGES:]
            lines.append("=== Recent conversation ===")
            for m in recent:
                sender = m.get("sender", {}) or {}
                name = sender.get("name", "unknown")
                content = m.get("content", "")
                lines.append(f"{name}: {content}")
            lines.append("=== End of context ===\n")
        sender = message.get("sender", {}) or {}
        lines.append(f"{sender.get('name', 'unknown')}: {message.get('content', '')}")
        lines.append(f"\n{self.config.name}:")
        return "\n".join(lines)

    async def _call_command(self, prompt: str) -> Optional[str]:
        self.log.info(f"Calling: {self.command} ({len(prompt)} char prompt)")
        try:
            cmd = shlex.split(self.command)
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(
                proc.communicate(prompt.encode()),
                timeout=120.0,
            )
            response = stdout.decode().strip()
            if not response:
                self.log.warning("Empty response from command")
                return None
            return response
        except asyncio.TimeoutError:
            self.log.error("Command timed out after 120s")
            return None
        except Exception as e:
            self.log.error(f"Command failed: {e}")
            return None

    async def handle_message(self, message: dict):
        if not self.should_respond(message):
            return
        sender = message.get("sender", {}) or {}
        self.log.info(f"Responding to {sender.get('name', '?')}")
        response = await self.generate_response(message)
        if response:
            await self.send_message(response)


def main():
    parser = argparse.ArgumentParser(description="Polycule Shell Adapter")
    parser.add_argument("--name",    required=True,           help="Agent name in chat")
    parser.add_argument("--command", required=True,           help="Shell command (reads stdin, writes stdout)")
    parser.add_argument("--room",    default="Main",          help="Room to join")
    parser.add_argument("--host",    default="localhost")
    parser.add_argument("--port",    default=7777, type=int)
    parser.add_argument("--trigger", action="append", dest="triggers", default=[],
                        help="Trigger word (repeatable). Default: @name and name.")
    parser.add_argument("--always",  action="store_true",     help="Respond to all messages")
    parser.add_argument("--resume",  default=None,            help="Resume session ID (passed to command as env var POLYCULE_SESSION)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format=f"[%(asctime)s] [{args.name}/%(levelname)s] %(message)s",
    )

    adapter = ShellAdapter(
        name=args.name,
        command=args.command,
        room=args.room,
        host=args.host,
        port=args.port,
        triggers=args.triggers or None,
        always=args.always,
        resume_session=args.resume,
    )
    asyncio.run(adapter.run())


if __name__ == "__main__":
    main()
