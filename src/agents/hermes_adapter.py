# maps · cassette.help · MIT
"""
Hermes Adapter for Polycule Hub

Connects a Hermes AI agent to the hub. Hermes is a stateless CLI agent —
each invocation starts fresh and receives the conversation context injected
into its prompt.

Auto-discovers the hermes binary from PATH or common install locations.
Supports named profiles (hermes chat --profile <name>) for different personas.

Usage:
    python3 hermes_adapter.py --name Cassette --room Main
    python3 hermes_adapter.py --name Wizard --profile wizard --room Main
    python3 hermes_adapter.py --name Research --profile research --always
"""
import argparse
import asyncio
import logging
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
from agents.base_adapter import BaseAdapter, AgentConfig

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 240.0

# Common install locations, checked in order if hermes is not on PATH
_HERMES_SEARCH_PATHS = [
    Path.home() / ".hermes" / "bin" / "hermes",
    Path("/usr/local/bin/hermes"),
    Path("/opt/homebrew/bin/hermes"),
    Path.home() / ".local" / "bin" / "hermes",
]


def _find_hermes() -> Optional[Path]:
    """Return the path to the hermes binary, or None if not found."""
    found = shutil.which("hermes")
    if found:
        return Path(found)
    for candidate in _HERMES_SEARCH_PATHS:
        if candidate.exists():
            return candidate
    return None


SYSTEM_PROMPT_DEFAULT = """\
You are {name}, an AI agent participating in a multi-agent terminal workspace called Polycule. \
You are collaborating with a human user and potentially other agents in a shared chat room. \
Be helpful, direct, and technically precise. Respond concisely.\
"""


class HermesAdapter(BaseAdapter):
    """
    Hermes adapter — calls `hermes chat [-Q] [-q prompt]` for each response.
    """

    def __init__(
        self,
        name: str = "Hermes",
        profile: Optional[str] = None,
        room: str = "Main",
        hub_host: str = "localhost",
        hub_port: int = 7777,
        always_respond: bool = False,
        triggers: Optional[list[str]] = None,
        system_prompt: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        config = AgentConfig(
            name=name,
            agent_type="hermes",
            room_name=room,
            hub_host=hub_host,
            hub_port=hub_port,
        )
        super().__init__(config)
        self.profile = profile
        self.always_respond = always_respond
        self.timeout = max(1.0, float(timeout))
        self._responding = False

        self._triggers: frozenset[str] = frozenset(
            t.lower() for t in (triggers or [f"@{name.lower()}", name.lower()])
        )
        self._system_prompt = (
            system_prompt
            or SYSTEM_PROMPT_DEFAULT.format(name=name)
        )

        self._hermes_bin = _find_hermes()
        if not self._hermes_bin:
            logger.warning(
                "hermes binary not found. Checked PATH and common locations. "
                "Install hermes or set it on PATH."
            )

    # ------------------------------------------------------------------
    # Trigger logic
    # ------------------------------------------------------------------

    def _should_respond(self, message: dict) -> bool:
        if self._responding:
            return False
        sender = message.get("sender", {})
        if sender.get("type") == "hermes":
            return False
        if sender.get("name", "").lower() == self.config.name.lower():
            return False
        sender_type = sender.get("type", "").lower()
        content = message.get("content", "").lower()

        if self.always_respond:
            return sender_type == "human"

        return any(t in content for t in self._triggers)

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    def _build_context_log(self) -> str:
        lines = []
        for m in self.context_messages[-60:]:
            if not isinstance(m, dict) or m.get("type") != "message":
                continue
            sender = m.get("sender", {})
            lines.append(f"[{sender.get('name', '?')}]: {m.get('content', '')}")
        return "\n".join(lines) if lines else "(no prior messages)"

    def _build_prompt(self, message: dict) -> str:
        sender = message.get("sender", {})
        sender_name = sender.get("name", "User")
        content = message.get("content", "")
        context = self._build_context_log()
        return (
            f"{self._system_prompt}\n\n"
            f"--- Recent chat ---\n{context}\n--- End of chat ---\n\n"
            f"{sender_name}: {content}\n\n"
            f"{self.config.name}:"
        )

    # ------------------------------------------------------------------
    # Hermes invocation
    # ------------------------------------------------------------------

    def _build_cmd(self, prompt: str) -> list[str]:
        if not self._hermes_bin:
            raise RuntimeError("hermes binary not found")
        cmd = [str(self._hermes_bin), "chat", "-Q", "-q", prompt]
        if self.profile:
            cmd = [str(self._hermes_bin), "chat", "--profile", self.profile, "-Q", "-q", prompt]
        return cmd

    async def _call_hermes(self, prompt: str) -> tuple[Optional[str], str, float]:
        started = time.monotonic()
        if not self._hermes_bin:
            return None, "missing_bin", 0.0

        try:
            result = await asyncio.to_thread(
                subprocess.run,
                self._build_cmd(prompt),
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            elapsed = time.monotonic() - started
            logger.warning(f"{self.config.name}: hermes timed out after {self.timeout:.0f}s")
            return None, "timeout", elapsed
        except Exception as e:
            elapsed = time.monotonic() - started
            logger.error(f"{self.config.name}: hermes call failed: {e}")
            return None, "error", elapsed

        elapsed = time.monotonic() - started
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            logger.warning(f"{self.config.name}: hermes exited {result.returncode}: {stderr[:200]}")
            return None, "error", elapsed

        output = (result.stdout or "").strip()
        if not output:
            return None, "empty", elapsed
        return output, "ok", elapsed

    # ------------------------------------------------------------------
    # Message handler
    # ------------------------------------------------------------------

    async def handle_message(self, message: dict):
        if not self._should_respond(message):
            return

        self._responding = True
        try:
            prompt = self._build_prompt(message)
            response, state, elapsed = await self._call_hermes(prompt)
            if state == "ok" and response:
                await self.send_message(response)
                logger.info(f"{self.config.name}: responded in {elapsed:.1f}s")
            else:
                logger.warning(f"{self.config.name}: no response ({state}, {elapsed:.1f}s)")
        finally:
            self._responding = False


def main():
    parser = argparse.ArgumentParser(description="Polycule Hermes Adapter")
    parser.add_argument("--name",    default="Hermes", help="Agent name in chat")
    parser.add_argument("--profile", default=None,     help="Hermes profile name")
    parser.add_argument("--room",    default="Main",   help="Room to join")
    parser.add_argument("--host",    default="localhost")
    parser.add_argument("--port",    default=7777, type=int)
    parser.add_argument("--trigger", action="append", dest="triggers", default=[],
                        help="Trigger word (repeatable). Default: @name and name.")
    parser.add_argument("--always",  action="store_true", help="Respond to all human messages")
    parser.add_argument("--timeout", default=DEFAULT_TIMEOUT, type=float,
                        help=f"Hermes call timeout in seconds (default: {DEFAULT_TIMEOUT:.0f})")
    parser.add_argument("--system-prompt", default=None,
                        help="Override the system prompt injected into hermes")
    args = parser.parse_args()

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
        triggers=args.triggers or None,
        system_prompt=args.system_prompt,
        timeout=args.timeout,
    )

    if not adapter._hermes_bin:
        print(
            "Error: hermes not found. Install hermes and ensure it is on PATH, "
            "or place it at ~/.hermes/bin/hermes.",
            file=sys.stderr,
        )
        sys.exit(1)

    asyncio.run(adapter.run())


if __name__ == "__main__":
    main()
