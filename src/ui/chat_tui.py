# maps · cassette.help · MIT
"""
Polycule Chat TUI — IRC-style terminal chat interface.

Connects to polycule hub as a human agent. Displays messages with
per-agent color coding. Shows approval requests inline. Handles
approval responses (y/n) from maps.

Usage:
    python3 chat_tui.py [--name maps] [--room Default] [--host localhost] [--port 7777]
"""

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime
from typing import Optional

import urwid

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Palette and color mapping
# ---------------------------------------------------------------------------

PALETTE = [
    ("header", "white", "dark blue", "bold"),
    ("footer", "black", "light gray"),
    ("input_bar", "white", "dark gray"),
    ("prompt", "light cyan", "dark gray"),
    ("time", "dark gray", ""),
    ("bracket", "dark gray", ""),
    # Agent types
    ("human", "light cyan", ""),
    ("maps", "light cyan", ""),
    ("hermes", "light magenta", ""),
    ("wizard", "light magenta", ""),
    ("cassette", "light magenta", ""),
    ("claude", "light blue", ""),
    ("opencode", "light blue", ""),
    ("codex", "light green", ""),
    ("test_agent", "dark green", ""),
    ("system_fg", "yellow", ""),
    ("approval", "light red", ""),
    ("granted", "light green", ""),
    ("denied", "dark red", ""),
    ("default_fg", "white", ""),
    ("separator", "dark gray", ""),
]


# Which urwid palette key to use for a given agent
def _agent_color(name: str, agent_type: str) -> str:
    n = name.lower()
    t = agent_type.lower()
    for key in (n, t):
        if key in (
            "maps",
            "human",
            "hermes",
            "wizard",
            "cassette",
            "claude",
            "opencode",
            "codex",
            "test_agent",
        ):
            return key
    return "default_fg"


# ---------------------------------------------------------------------------
# Message widgets
# ---------------------------------------------------------------------------


class ChatMessage(urwid.WidgetWrap):
    def __init__(self, message: dict):
        sender = message.get("sender", {})
        name = sender.get("name", "?")
        atype = sender.get("type", "unknown")
        content = message.get("content", "")
        ts = message.get("timestamp", "")

        try:
            t = datetime.fromisoformat(ts).strftime("%H:%M")
        except Exception:
            t = "--:--"

        color = _agent_color(name, atype)
        markup = [
            ("time", f"{t} "),
            ("bracket", "["),
            (color, name),
            ("bracket", "] "),
            content,
        ]
        super().__init__(urwid.Text(markup))


class SystemMessage(urwid.WidgetWrap):
    def __init__(self, text: str, style: str = "system_fg"):
        super().__init__(urwid.Text([(style, f"  ··· {text}")]))


class ApprovalRequestWidget(urwid.WidgetWrap):
    def __init__(self, req: dict):
        req_id = req.get("request_id", "?")
        requester = req.get("requester", "?")
        command = req.get("command", "?")
        markup = [
            ("approval", f"  ⚡ APPROVAL REQUEST [{req_id}] "),
            ("bracket", f"{requester} wants: "),
            ("approval", command),
            ("bracket", "  — type "),
            ("granted", f"approve {req_id}"),
            ("bracket", " or "),
            ("denied", f"deny {req_id}"),
        ]
        super().__init__(urwid.Text(markup))


class Separator(urwid.WidgetWrap):
    def __init__(self, text: str = ""):
        line = f"── {text} ──" if text else "─" * 40
        super().__init__(urwid.Text(("separator", line)))


# ---------------------------------------------------------------------------
# Main TUI class
# ---------------------------------------------------------------------------


class ChatTUI:
    def __init__(self, name: str, room: str, host: str, port: int):
        self.name = name
        self.room = room
        self.host = host
        self.port = port

        self.room_id: Optional[str] = None
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.connected = False
        self._loop: Optional[urwid.MainLoop] = None

        self._build_ui()

    # -----------------------------------------------------------------------
    # UI construction
    # -----------------------------------------------------------------------

    def _build_ui(self):
        self._header_text = urwid.Text(
            [
                ("header", f" POLYCULE "),
                " ",
                ("header", f"room: {self.room}"),
                " ",
                ("header", f"[{self.name}]"),
                "  connecting…",
            ],
            align="left",
        )
        self.header = urwid.AttrMap(self._header_text, "header")

        self.walker = urwid.SimpleFocusListWalker([SystemMessage("connecting to hub…")])
        self.listbox = urwid.ListBox(self.walker)

        self.edit = urwid.Edit()
        input_row = urwid.Columns(
            [
                ("fixed", 2, urwid.Text(("prompt", "> "))),
                self.edit,
            ]
        )
        self.footer = urwid.AttrMap(input_row, "input_bar")

        self.frame = urwid.Frame(
            body=self.listbox,
            header=self.header,
            footer=self.footer,
            focus_part="footer",
        )

    # -----------------------------------------------------------------------
    # Widget helpers
    # -----------------------------------------------------------------------

    def _append(self, widget: urwid.Widget):
        self.walker.append(widget)
        self.listbox.set_focus(len(self.walker) - 1)
        if self._loop:
            self._loop.draw_screen()

    def add_message(self, message: dict):
        self._append(ChatMessage(message))

    def add_system(self, text: str, style: str = "system_fg"):
        self._append(SystemMessage(text, style))

    def add_approval_request(self, req: dict):
        self._append(ApprovalRequestWidget(req))

    def _update_header(self, status: str = ""):
        connected_marker = " ✓" if self.connected else " …"
        self._header_text.set_text(
            [
                ("header", " POLYCULE "),
                " ",
                ("header", f"room: {self.room}"),
                " ",
                ("header", f"[{self.name}]"),
                f"  {connected_marker}" + (f"  {status}" if status else ""),
            ]
        )
        if self._loop:
            self._loop.draw_screen()

    # -----------------------------------------------------------------------
    # Connection
    # -----------------------------------------------------------------------

    async def connect(self):
        try:
            self.reader, self.writer = await asyncio.open_connection(
                self.host, self.port
            )

            handshake = {
                "type": "handshake",
                "name": self.name,
                "agent_type": "human",
                "room_name": self.room,
            }
            self.writer.write((json.dumps(handshake) + "\n").encode())
            await self.writer.drain()

            data = await asyncio.wait_for(self.reader.readline(), timeout=10.0)
            response = json.loads(data.decode().strip())

            if response.get("action") == "awaiting_room":
                cmd = {
                    "type": "command",
                    "command": "create_room",
                    "room_name": self.room,
                }
                self.writer.write((json.dumps(cmd) + "\n").encode())
                await self.writer.drain()
                data = await asyncio.wait_for(self.reader.readline(), timeout=10.0)
                response = json.loads(data.decode().strip())

            if response.get("type") in ("room_created", "room_state"):
                room_data = response.get("room", {})
                self.room_id = room_data.get("room_id")
                self.room = room_data.get("room_name", self.room)
                self.connected = True

                # Load history from room_state
                for msg in room_data.get("recent_messages", []):
                    if isinstance(msg, dict) and msg.get("type") == "message":
                        self.add_message(msg)

                agent_count = len(room_data.get("agents", []))
                self.add_system(
                    f"connected · room: {self.room} · {agent_count} agent(s)"
                )
                self._update_header()

                # Start receive loop
                asyncio.create_task(self._receive_loop())
            else:
                self.add_system(f"unexpected response: {response}", "denied")

        except Exception as e:
            self.add_system(f"connection failed: {e}", "denied")

    async def _receive_loop(self):
        while self.connected and self.reader:
            try:
                data = await asyncio.wait_for(self.reader.readline(), timeout=60.0)
                if not data:
                    break
                msg = json.loads(data.decode().strip())
                await self._handle_incoming(msg)
            except asyncio.TimeoutError:
                continue
            except json.JSONDecodeError:
                continue
            except Exception as e:
                self.add_system(f"receive error: {e}", "denied")
                break

        self.connected = False
        self.add_system("disconnected from hub", "denied")
        self._update_header()

    async def _handle_incoming(self, msg: dict):
        t = msg.get("type")

        if t == "message":
            self.add_message(msg)

        elif t == "context_dump":
            msgs = msg.get("messages", [])
            if msgs:
                self._append(Separator(f"history ({len(msgs)} messages)"))
                for m in msgs:
                    if isinstance(m, dict) and m.get("type") == "message":
                        self.add_message(m)
                self._append(Separator("live"))

        elif t == "system":
            action = msg.get("action", "")
            if action == "agent_joined":
                a = msg.get("agent", {})
                self.add_system(f"{a.get('name', '?')} ({a.get('type', '?')}) joined")
            elif action == "agent_left":
                self.add_system(f"agent left")
            elif action == "auto_approve_changed":
                status = "ON" if msg.get("value") else "OFF"
                self.add_system(
                    f"auto-approve: {status}",
                    "granted" if msg.get("value") else "denied",
                )
            elif action == "structural_executed":
                self.add_system(
                    f"[tmux] {msg.get('command')} → {msg.get('result')} (by {msg.get('executor')})",
                    "granted",
                )

        elif t == "approval_request":
            self.add_approval_request(msg)

        elif t == "approval_granted":
            self.add_system(
                f"✓ approved [{msg.get('request_id')}] {msg.get('command')} "
                f"(by {msg.get('approved_by')})",
                "granted",
            )

        elif t == "approval_denied":
            self.add_system(f"✗ denied [{msg.get('request_id')}]", "denied")

        elif t == "error":
            self.add_system(f"hub error: {msg.get('message')}", "denied")

    # -----------------------------------------------------------------------
    # Input handling
    # -----------------------------------------------------------------------

    def handle_input(self, key):
        if key == "enter":
            text = self.edit.get_edit_text().strip()
            if text:
                self.edit.set_edit_text("")
                asyncio.get_event_loop().create_task(self._handle_user_input(text))
        elif key in ("ctrl d", "ctrl c", "f10"):
            raise urwid.ExitMainLoop()

    async def _handle_user_input(self, text: str):
        """Parse and dispatch user input."""
        # Local commands (start with /)
        if text.startswith("/"):
            await self._handle_slash(text[1:].strip())
            return

        # Shorthand approval commands
        parts = text.split()
        if parts[0].lower() == "approve" and len(parts) == 2:
            await self._send(
                {"type": "command", "command": "approve", "request_id": parts[1]}
            )
            return
        if parts[0].lower() == "deny" and len(parts) == 2:
            await self._send(
                {"type": "command", "command": "deny", "request_id": parts[1]}
            )
            return

        # Regular message
        if not self.room_id:
            self.add_system("not connected to a room", "denied")
            return
        await self._send({"type": "message", "room_id": self.room_id, "content": text})

    async def _handle_slash(self, cmd: str):
        parts = cmd.split()
        if not parts:
            return
        verb = parts[0].lower()

        if verb == "approve" and len(parts) >= 2:
            await self._send(
                {"type": "command", "command": "approve", "request_id": parts[1]}
            )
        elif verb == "deny" and len(parts) >= 2:
            await self._send(
                {"type": "command", "command": "deny", "request_id": parts[1]}
            )
        elif verb == "autoapprove" or verb == "aa":
            val = len(parts) < 2 or parts[1].lower() not in ("off", "0", "false")
            await self._send(
                {"type": "command", "command": "set_auto_approve", "value": val}
            )
        elif verb == "rooms":
            await self._send({"type": "request", "request": "rooms"})
        elif verb == "join" and len(parts) >= 2:
            await self._send(
                {"type": "command", "command": "join_room", "room_id": parts[1]}
            )
        elif verb in ("quit", "q", "exit"):
            raise urwid.ExitMainLoop()
        else:
            self.add_system(f"unknown command: /{verb}", "denied")

    async def _send(self, obj: dict):
        if self.writer and not self.writer.is_closing():
            try:
                self.writer.write((json.dumps(obj) + "\n").encode())
                await self.writer.drain()
            except Exception as e:
                self.add_system(f"send error: {e}", "denied")

    # -----------------------------------------------------------------------
    # Run
    # -----------------------------------------------------------------------

    def start(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self.connect())
        event_loop = urwid.AsyncioEventLoop(loop=loop)
        self._loop = urwid.MainLoop(
            self.frame,
            PALETTE,
            event_loop=event_loop,
            unhandled_input=self.handle_input,
        )
        try:
            self._loop.run()
        except urwid.ExitMainLoop:
            pass
        finally:
            loop.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Polycule Chat TUI")
    parser.add_argument("--name", default="maps")
    parser.add_argument("--room", default="Default")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=7777)
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)

    tui = ChatTUI(name=args.name, room=args.room, host=args.host, port=args.port)
    tui.start()


if __name__ == "__main__":
    main()
