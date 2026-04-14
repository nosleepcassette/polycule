# maps · cassette.help · MIT
"""
Polycule Chat TUI — IRC-style terminal chat interface.

Connects to polycule hub as a human agent. Displays messages with
per-agent color coding. Shows approval requests inline. Handles
approval responses (y/n) from the human operator.

Usage:
    python3 chat_tui.py [--name "$USER"] [--room Default] [--host localhost] [--port 7777]
"""

import argparse
import asyncio
import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Optional

import urwid

_SRC_DIR = str(Path(__file__).resolve().parents[1])
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from runtime_state import (
    clear_temporary_agent_enablements,
    get_temporary_agent_enablements,
    mark_temporary_agent_enablements,
)
from managed_agents import (
    get_agent_capability_hints,
    get_default_backend_agent_modes,
    get_free_agent_names,
    get_managed_agent_names,
    get_paid_agent_names,
)

logger = logging.getLogger(__name__)
PROJECT_DIR = Path(__file__).resolve().parents[2]
POLYCULE_BIN = PROJECT_DIR / "bin" / "polycule"
DB_PATH = PROJECT_DIR / "polycule.db"
DEFAULT_HUMAN_NAME = (
    os.environ.get("POLYCULE_OPERATOR_NAME")
    or os.environ.get("USER")
    or os.environ.get("LOGNAME")
    or "you"
)
AGENT_MODES = ("mention", "always", "off")

FREE_MODE_COMMANDS = frozenset(("free", "free-mode"))

def _backend_agents() -> tuple[str, ...]:
    return tuple(get_managed_agent_names())


def _backend_agent_set() -> set[str]:
    return set(_backend_agents())


def DEFAULT_AGENT_MODE(agent_name: str) -> str:
    return get_default_backend_agent_modes().get(agent_name.strip().lower(), "mention")


def _free_agents() -> frozenset[str]:
    return frozenset(get_free_agent_names())


def _paid_agents() -> frozenset[str]:
    return frozenset(get_paid_agent_names())


def _agent_capability_hints() -> dict[str, dict[str, object]]:
    return get_agent_capability_hints()

SLASH_COMMANDS = (
    "help",
    "ahelp",
    "h",
    "?",
    "approve",
    "deny",
    "autoapprove",
    "aa",
    "rooms",
    "join",
    "disable",
    "enable",
    "mode",
    "modes",
    "free",
    "free-mode",
    "theme",
    "themes",
    "topic",
    "search",
    "pin",
    "pins",
    "summon",
    "brief",
    "watch",
    "standdown",
    "agents",
    "agentstatus",
    "rollcall",
    "clear",
    "restart",
    "quit",
    "q",
    "exit",
)

SLASH_HELP = [
    ("/help", "show this help and slash completion hint"),
    ("/ahelp", "alias for /help"),
    ("/approve <id>", "approve a structural request"),
    ("/deny <id>", "deny a structural request"),
    ("/autoapprove [on|off]", "toggle auto-approve mode"),
    ("/rooms", "list rooms"),
    ("/join <room_id>", "join an existing room"),
    ("/disable <agent>", "cooldown backend agent"),
    ("/enable <agent>", "reactivate backend agent"),
    ("/mode <agent> <mention|always|off>", "set backend agent response mode"),
    ("/modes", "show backend agent response modes"),
    ("/free", "toggle local/non-premium mode"),
    ("/free-mode", "alias for /free"),
    ("/which <task>", "query agents for best fit"),
    ("/theme <name>", "set color theme"),
    ("/themes", "list available themes"),
    ("/topic [text]", "set or show room topic"),
    ("/search <query>", "search messages"),
    ("/pin <id>", "pin message by ID"),
    ("/pins", "list pinned messages"),
    ("/summon <all|agent...>", "temporarily enable and call agents into the room"),
    ("/brief <all|agent...> -- <message>", "send a targeted room directive"),
    ("/watch <agent|all> <off|human|room|@agent>", "set phase-1 watch policy"),
    ("/standdown <all|agent...>", "revert temporary summon activations"),
    ("/agents", "show backend agent state"),
    ("/rollcall", "mention all backend agents in one ping"),
    ("/clear", "clear the chat log (also ctrl+l)"),
    ("/restart", "restart the TUI (reconnects to running hub)"),
    ("/restart --full", "restart everything: hub + agents + TUI"),
    ("/quit", "exit chat"),
]

SLASH_NOARG_COMMANDS = frozenset(
    {
        "help",
        "ahelp",
        "h",
        "?",
        "rooms",
        "modes",
        "free",
        "free-mode",
        "themes",
        "agents",
        "agentstatus",
        "rollcall",
        "pins",
        "clear",
        "restart",
        "quit",
        "q",
        "exit",
    }
)

_MENTION_RE = re.compile(r"(@\w+)")
_MD_INLINE_RE = re.compile(r"(\*\*[^*\n]+?\*\*|\*[^*\n]+?\*|`[^`\n]+`|@\w+)")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)")
_BULLET_RE = re.compile(r"^([-*+]|\d+\.)\s+(.*)")
_RULE_RE = re.compile(r"^[-*_]{3,}\s*$")

# ---------------------------------------------------------------------------
# Palette and color mapping
# ---------------------------------------------------------------------------

DEFAULT_THEME_NAME = "amber"
THEME_ALIASES = {
    "phosphor": "amber",
    "amber-phosphor": "amber",
    "amber_phosphor": "amber",
}
THEME_DESCRIPTIONS = {
    "default": "classic blue-gray polycule",
    "amber": "amber/phosphor CRT",
    "matrix": "green phosphor terminal",
    "monokai": "monokai noir",
}

_BASE_THEME = {
    "header": ("white", "dark blue", "bold"),
    "footer": ("black", "light gray"),
    "input_bar": ("white", "dark gray"),
    "prompt": ("light cyan", "dark gray"),
    "time": ("dark gray", ""),
    "bracket": ("dark gray", ""),
    "human": ("light cyan", ""),
    "maps": ("light cyan", ""),
    "hermes": ("light magenta", ""),
    "wizard": ("light magenta", ""),
    "cassette": ("light magenta", ""),
    "claude": ("light blue", ""),
    "opencode": ("light blue", ""),
    "gemini": ("light cyan", ""),
    "codex": ("light green", ""),
    "test_agent": ("dark green", ""),
    "system_fg": ("yellow", ""),
    "approval": ("light red", ""),
    "granted": ("light green", ""),
    "denied": ("dark red", ""),
    "default_fg": ("white", ""),
    "separator": ("dark gray", ""),
    "md_h1": ("white, bold", ""),
    "md_h2": ("light cyan, bold", ""),
    "md_h3": ("light gray, bold", ""),
    "md_bold": ("white, bold", ""),
    "md_code": ("light green", ""),
    "md_bullet": ("light gray", ""),
    "md_indent": ("dark gray", ""),
}


def _theme_spec(**overrides) -> dict[str, tuple]:
    spec = dict(_BASE_THEME)
    spec.update(overrides)
    return spec


THEMES = {
    "default": _theme_spec(),
    "amber": _theme_spec(
        header=("black", "brown", "bold"),
        footer=("yellow", "black"),
        input_bar=("light gray", "black"),
        prompt=("yellow", "black"),
        time=("brown", ""),
        bracket=("brown", ""),
        human=("white", ""),
        maps=("white", ""),
        hermes=("yellow", ""),
        wizard=("yellow", ""),
        cassette=("yellow", ""),
        claude=("light cyan", ""),
        opencode=("light cyan", ""),
        gemini=("white", ""),
        codex=("light green", ""),
        test_agent=("brown", ""),
        system_fg=("yellow", ""),
        default_fg=("light gray", ""),
        separator=("brown", ""),
        md_h1=("yellow, bold", ""),
        md_h2=("light gray, bold", ""),
        md_h3=("brown, bold", ""),
        md_bold=("white, bold", ""),
        md_bullet=("brown", ""),
    ),
    "matrix": _theme_spec(
        header=("black", "dark green", "bold"),
        footer=("light green", "black"),
        input_bar=("light green", "black"),
        prompt=("light green", "black"),
        time=("dark green", ""),
        bracket=("dark green", ""),
        human=("white", ""),
        maps=("white", ""),
        hermes=("light green", ""),
        wizard=("light green", ""),
        cassette=("light green", ""),
        claude=("light cyan", ""),
        opencode=("light cyan", ""),
        gemini=("white", ""),
        codex=("yellow", ""),
        test_agent=("dark green", ""),
        system_fg=("light green", ""),
        default_fg=("light gray", ""),
        separator=("dark green", ""),
        md_h1=("light green, bold", ""),
        md_h2=("white, bold", ""),
        md_h3=("dark green, bold", ""),
        md_bold=("white, bold", ""),
        md_code=("yellow", ""),
        md_bullet=("dark green", ""),
    ),
    "monokai": _theme_spec(
        header=("black", "light magenta", "bold"),
        footer=("light gray", "dark gray"),
        input_bar=("light gray", "black"),
        prompt=("yellow", "black"),
        time=("dark gray", ""),
        bracket=("dark gray", ""),
        human=("white", ""),
        maps=("white", ""),
        hermes=("light magenta", ""),
        wizard=("light magenta", ""),
        cassette=("light magenta", ""),
        claude=("light blue", ""),
        opencode=("light blue", ""),
        gemini=("yellow", ""),
        codex=("light green", ""),
        test_agent=("yellow", ""),
        system_fg=("yellow", ""),
        approval=("light red", ""),
        granted=("light green", ""),
        denied=("light red", ""),
        default_fg=("light gray", ""),
        separator=("dark gray", ""),
        md_h1=("white, bold", ""),
        md_h2=("yellow, bold", ""),
        md_h3=("light magenta, bold", ""),
        md_bold=("white, bold", ""),
        md_code=("light green", ""),
        md_bullet=("yellow", ""),
    ),
}


def _normalize_theme_name(theme_name: str) -> str:
    normalized = (theme_name or "").strip().lower()
    normalized = THEME_ALIASES.get(normalized, normalized or DEFAULT_THEME_NAME)
    return normalized if normalized in THEMES else DEFAULT_THEME_NAME


def _build_palette(theme_name: str) -> list[tuple]:
    spec = THEMES[_normalize_theme_name(theme_name)]
    return [(name, *values) for name, values in spec.items()]


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
            "gemini",
            "codex",
            "test_agent",
        ):
            return key
    return "default_fg"


def _parse_inline_md(text: str) -> list:
    """Parse inline markdown (**bold**, *italic*, `code`) and @mentions into urwid markup."""
    parts = _MD_INLINE_RE.split(text)
    markup = []
    for part in parts:
        if not part:
            continue
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            markup.append(("md_bold", part[2:-2]))
        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
            markup.append(("md_bold", part[1:-1]))
        elif part.startswith("`") and part.endswith("`") and len(part) > 2:
            markup.append(("md_code", part[1:-1]))
        elif part.startswith("@"):
            markup.append((_agent_color(part[1:], ""), part))
        else:
            markup.append(part)
    return markup or [text]


def _md_to_widgets(content: str, prefix: list) -> list:
    """Convert message content to a list of urwid.Text widgets with markdown rendering."""
    lines = content.splitlines() if content else [""]
    widgets: list = []
    in_fence = False
    code_buf: list = []

    for i, line in enumerate(lines):
        stripped = line.strip()

        if stripped.startswith("```"):
            if in_fence:
                for cl in code_buf:
                    widgets.append(urwid.Text([("md_code", "  " + cl)]))
                code_buf = []
                in_fence = False
            else:
                in_fence = True
            continue

        if in_fence:
            code_buf.append(line)
            continue

        hm = _HEADING_RE.match(stripped)
        if hm:
            level = len(hm.group(1))
            style = "md_h1" if level == 1 else ("md_h2" if level == 2 else "md_h3")
            text_part = [(style, hm.group(2))]
            widgets.append(urwid.Text((prefix + text_part) if i == 0 else text_part))
            continue

        if _RULE_RE.match(stripped):
            widgets.append(urwid.Text(("separator", "─" * 44)))
            continue

        bm = _BULLET_RE.match(stripped)
        if bm:
            is_num = bm.group(1)[0].isdigit()
            bullet_str = f"{bm.group(1)} " if is_num else "• "
            inline = _parse_inline_md(bm.group(2))
            indent = "" if i == 0 else "  "
            lead = [("md_bullet", indent + bullet_str)]
            row = (prefix + lead + inline) if i == 0 else (lead + inline)
            widgets.append(urwid.Text(row))
            continue

        if not stripped:
            if widgets:
                widgets.append(urwid.Text(""))
            continue

        inline = _parse_inline_md(stripped)
        if i == 0:
            widgets.append(urwid.Text(prefix + inline))
        else:
            widgets.append(urwid.Text([("md_indent", "  ")] + inline))

    for cl in code_buf:
        widgets.append(urwid.Text([("md_code", "  " + cl)]))

    return widgets or [urwid.Text(prefix)]


# ---------------------------------------------------------------------------
# Message widgets
# ---------------------------------------------------------------------------


class AgentHeader(urwid.WidgetWrap):
    def __init__(self, name: str, color: str):
        label = f" {name} "
        left = "─" * 4
        right_len = max(4, 44 - len(label) - 4)
        right = "─" * right_len
        markup = [("separator", left), (color, label), ("separator", right)]
        super().__init__(urwid.Text(markup))


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
        prefix = [
            ("time", f"{t} "),
            ("bracket", "["),
            (color, name),
            ("bracket", "] "),
        ]
        rows = _md_to_widgets(content, prefix)
        widget = rows[0] if len(rows) == 1 else urwid.Pile(rows)
        super().__init__(widget)


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
        self._theme_name = self._load_theme_name()

        self.room_id: Optional[str] = None
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.connected = False
        self._loop: Optional[urwid.MainLoop] = None
        self._recv_task: Optional[asyncio.Task] = None
        self._reconnect_task: Optional[asyncio.Task] = None
        self._shutting_down = False
        self._free_mode = False  # free mode: only free (no-cost) agents respond
        self._connected_agents: dict[str, dict] = {}  # agent_id -> {name, type}
        self._topic = ""
        self._pinned_messages: dict[int, dict] = {}
        self._last_agent_state_snapshot: dict[str, dict[str, str]] = {}
        self._seen_message_ids: set[str] = set()
        self._seen_message_order = deque(maxlen=5000)
        self._reconnect_base_delay = 1.0
        self._reconnect_max_delay = 15.0
        self._completion_seed: Optional[tuple] = None
        self._completion_matches: list[str] = []
        self._completion_index = -1
        self._pending_request_ids: list[str] = []

        # Bracketed paste state
        self._pasting = False
        self._paste_buffer: list[str] = []

        # Input history (up/down navigation)
        self._history: list[str] = []
        self._history_pos = -1  # -1 = live input, >=0 = history index
        self._history_draft = ""  # saved live input while browsing history

        # Agent sectioning: track last sender to emit header on change
        self._last_sender_name: Optional[str] = None

        # Restart flags (checked after main loop exits)
        self._restart_requested = False
        self._full_restart_requested = False

        self._build_ui()

    def _load_theme_name(self) -> str:
        if not DB_PATH.exists():
            return DEFAULT_THEME_NAME
        try:
            with sqlite3.connect(DB_PATH) as conn:
                row = conn.execute(
                    "SELECT value FROM settings WHERE key = ?",
                    ("chat_theme",),
                ).fetchone()
        except sqlite3.Error:
            return DEFAULT_THEME_NAME
        if not row or not row[0]:
            return DEFAULT_THEME_NAME
        return _normalize_theme_name(str(row[0]))

    def _save_theme_name(self, theme_name: str):
        try:
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS settings (
                        key         TEXT PRIMARY KEY,
                        value       TEXT NOT NULL,
                        updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO settings (key, value, updated_at)
                    VALUES (?, ?, datetime('now'))
                    ON CONFLICT(key) DO UPDATE SET
                        value=excluded.value,
                        updated_at=excluded.updated_at
                    """,
                    ("chat_theme", theme_name),
                )
        except sqlite3.Error as exc:
            logger.warning("Failed to persist chat theme %s: %s", theme_name, exc)

    def _apply_theme(self, theme_name: str, *, persist: bool = False) -> str:
        normalized = _normalize_theme_name(theme_name)
        self._theme_name = normalized
        if persist:
            self._save_theme_name(normalized)
        if self._loop:
            self._loop.screen.register_palette(_build_palette(normalized))
            self._loop.draw_screen()
        return normalized

    def _theme_lines(self) -> list[str]:
        lines = []
        current = _normalize_theme_name(self._theme_name)
        for name, desc in THEME_DESCRIPTIONS.items():
            marker = "*" if name == current else " "
            alias = " (alias: phosphor)" if name == "amber" else ""
            lines.append(f"{marker} {name} - {desc}{alias}")
        return lines

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
        msg_id = message.get("id")
        if msg_id:
            if msg_id in self._seen_message_ids:
                return
            if len(self._seen_message_order) == self._seen_message_order.maxlen:
                oldest = self._seen_message_order[0]
                self._seen_message_ids.discard(oldest)
            self._seen_message_order.append(msg_id)
            self._seen_message_ids.add(msg_id)
        sender = message.get("sender", {})
        name = sender.get("name", "?")
        atype = sender.get("type", "unknown")
        if name != self._last_sender_name:
            self._last_sender_name = name
            self._append(AgentHeader(name, _agent_color(name, atype)))
        self._append(ChatMessage(message))

    def add_system(self, text: str, style: str = "system_fg"):
        self._append(SystemMessage(text, style))

    def add_approval_request(self, req: dict):
        req_id = str(req.get("request_id", "")).strip()
        if req_id and req_id not in self._pending_request_ids:
            self._pending_request_ids.append(req_id)
            if len(self._pending_request_ids) > 100:
                self._pending_request_ids = self._pending_request_ids[-100:]
        self._append(ApprovalRequestWidget(req))

    def _update_header(self, status: str = ""):
        connected_marker = " ✓" if self.connected else " …"
        free_marker = " [FREE]" if self._free_mode else ""

        # Build agent status dots
        agent_dots = ""
        if self._connected_agents:
            dot_parts = []
            for agent_id, info in self._connected_agents.items():
                name = info.get("name", "?")
                dot_parts.append(name[:3].lower())  # short name
            agent_dots = " [" + ", ".join(dot_parts) + "]"

        self._header_text.set_text(
            [
                ("header", " POLYCULE "),
                " ",
                ("header", f"room: {self.room}"),
                " ",
                ("header", f"[{self.name}]"),
                f"  {connected_marker}"
                + free_marker
                + agent_dots
                + (f"  {status}" if status else ""),
            ]
        )
        if self._loop:
            self._loop.draw_screen()

    def _clear_messages(self):
        self.walker[:] = []
        self._last_sender_name = None
        self.add_system("log cleared  ·  ctrl+l or /clear to repeat")

    def _history_navigate(self, direction: int):
        """Navigate sent-message history. direction=-1 = back, +1 = forward."""
        if not self._history:
            return
        if self._history_pos == -1:
            if direction < 0:
                self._history_draft = self.edit.get_edit_text()
                self._history_pos = len(self._history) - 1
                text = self._history[self._history_pos]
                self.edit.set_edit_text(text)
                self.edit.edit_pos = len(text)
        else:
            new_pos = self._history_pos + direction
            if new_pos < 0:
                return
            if new_pos >= len(self._history):
                self._history_pos = -1
                self.edit.set_edit_text(self._history_draft)
                self.edit.edit_pos = len(self._history_draft)
            else:
                self._history_pos = new_pos
                text = self._history[self._history_pos]
                self.edit.set_edit_text(text)
                self.edit.edit_pos = len(text)

    # -----------------------------------------------------------------------
    # Connection
    # -----------------------------------------------------------------------

    async def connect(self, reconnecting: bool = False) -> bool:
        try:
            await self._close_writer()
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

                # Track connected agents
                self._connected_agents = {}
                for agent in room_data.get("agents", []):
                    agent_id = agent.get("id", "")
                    agent_name = agent.get("name", "")
                    agent_type = agent.get("type", "unknown")
                    if agent_id:
                        self._connected_agents[agent_id] = {
                            "name": agent_name,
                            "type": agent_type,
                        }

                agent_count = len(self._connected_agents)
                if reconnecting:
                    self.add_system(
                        f"reconnected · room: {self.room} · {agent_count} agent(s)",
                        "granted",
                    )
                    self._update_header("reconnected")
                else:
                    self.add_system(
                        f"connected · room: {self.room} · {agent_count} agent(s)"
                    )
                    self._update_header("live")

                # Start receive loop
                if not self._recv_task or self._recv_task.done():
                    self._recv_task = asyncio.create_task(self._receive_loop())
                return True
            else:
                self.add_system(f"unexpected response: {response}", "denied")
                return False

        except Exception as e:
            if reconnecting:
                self.add_system(f"reconnect failed: {e}", "denied")
            else:
                self.add_system(f"connection failed: {e}", "denied")
            self.connected = False
            self._update_header("offline")
            return False

    async def _receive_loop(self):
        reason = "hub closed connection"
        while self.connected and self.reader:
            try:
                data = await asyncio.wait_for(self.reader.readline(), timeout=60.0)
                if not data:
                    reason = "hub closed connection"
                    break
                msg = json.loads(data.decode().strip())
                await self._handle_incoming(msg)
            except asyncio.TimeoutError:
                continue
            except json.JSONDecodeError:
                continue
            except Exception as e:
                reason = f"receive error: {e}"
                break

        if not self._shutting_down:
            await self._handle_disconnect(reason)

    async def _handle_disconnect(self, reason: str):
        if self.connected:
            self.connected = False
        await self._close_writer()
        self.add_system(f"disconnected from hub ({reason})", "denied")
        self._update_header("offline")
        self._schedule_reconnect()

    def _schedule_reconnect(self):
        if self._shutting_down:
            return
        if self._reconnect_task and not self._reconnect_task.done():
            return
        self._reconnect_task = asyncio.create_task(self._reconnect_loop())

    async def _reconnect_loop(self):
        attempt = 0
        try:
            while not self._shutting_down and not self.connected:
                attempt += 1
                delay = min(
                    self._reconnect_max_delay,
                    self._reconnect_base_delay * (2 ** (attempt - 1)),
                )
                self.add_system(f"reconnecting in {delay:.1f}s (attempt {attempt})")
                await asyncio.sleep(delay)
                if self._shutting_down or self.connected:
                    break
                ok = await self.connect(reconnecting=True)
                if ok:
                    break
        finally:
            self._reconnect_task = None

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

        elif t == "directive":
            directive_id = str(msg.get("directive_id", "")).strip()
            directive_kind = str(msg.get("directive_kind", "brief")).strip().lower() or "brief"
            targets = [str(item).strip().lower() for item in msg.get("targets", []) if str(item).strip()]
            issued_by = str(msg.get("issued_by", "?")).strip() or "?"
            summary = str(msg.get("content", "")).strip().replace("\n", " ")
            if len(summary) > 120:
                summary = summary[:117] + "..."
            target_label = ", ".join(targets) if targets else "(none)"
            line = f"{directive_kind} [{directive_id}] {issued_by} -> {target_label}: {summary}"
            self.add_system(line, "approval")

        elif t == "system":
            action = msg.get("action", "")
            if action == "agent_joined":
                a = msg.get("agent", {})
                agent_id = a.get("id", "")
                agent_name = a.get("name", "")
                agent_type = a.get("type", "unknown")
                if agent_id:
                    self._connected_agents[agent_id] = {
                        "name": agent_name,
                        "type": agent_type,
                    }
                self.add_system(
                    f"{agent_name} ({agent_type}) joined",
                    "granted",
                )
                self._update_header()
            elif action == "agent_left":
                agent_id = msg.get("agent_id", "")
                if agent_id in self._connected_agents:
                    del self._connected_agents[agent_id]
                self.add_system(f"agent left", "denied")
                self._update_header()
            elif action == "agent_typing":
                agent_name = msg.get("agent_name", "?")
                is_typing = msg.get("is_typing", False)
                status = f"typing..." if is_typing else "ready"
                self.add_system(f"{agent_name}: {status}")
            elif action == "agent_tool_use":
                agent_name = msg.get("agent_name", "?")
                tool_name = msg.get("tool_name", "?")
                status = msg.get("status", "unknown")
                if status == "started":
                    self.add_system(f"{agent_name} using {tool_name}...", "system_fg")
                elif status == "completed":
                    self.add_system(f"{agent_name} finished {tool_name}", "granted")
                elif status == "failed":
                    self.add_system(f"{agent_name} {tool_name} failed", "denied")
            elif action == "context_warning":
                agent_name = msg.get("agent_name", "?")
                usage_pct = msg.get("usage_pct", 0)
                self.add_system(f"{agent_name} context at {usage_pct:.0f}%", "approval")
            elif action == "auto_approve_changed":
                status = "ON" if msg.get("value") else "OFF"
                self.add_system(
                    f"auto-approve: {status}",
                    "granted" if msg.get("value") else "denied",
                )
            elif action == "agent_status":
                agent = (
                    msg.get("agent", {}) if isinstance(msg.get("agent"), dict) else {}
                )
                name = agent.get("name", "?")
                status = str(msg.get("status", "update")).strip().lower() or "update"
                detail = str(msg.get("detail", "")).strip()
                style = "system_fg"
                if status in ("timeout", "error", "failed"):
                    style = "denied"
                elif status in ("done", "completed", "ok"):
                    style = "granted"
                suffix = f" - {detail}" if detail else ""
                self.add_system(f"[{name}] {status}{suffix}", style)
            elif action == "agent_session":
                agent_name = msg.get("agent_name", "?")
                session_id = str(msg.get("session_id", "")).strip()
                session_title = str(msg.get("session_title", "")).strip()
                state = str(msg.get("state", "changed")).strip().lower() or "changed"
                label = session_title or session_id or "(unknown)"
                if session_id and session_title:
                    label = f"{session_title} [{session_id}]"
                if state == "created":
                    self.add_system(f"{agent_name} session ready: {label}", "granted")
                else:
                    self.add_system(f"{agent_name} session changed: {label}", "system_fg")
            elif action == "structural_executed":
                self.add_system(
                    f"[tmux] {msg.get('command')} → {msg.get('result')} (by {msg.get('executor')})",
                    "granted",
                )
            elif action == "watch_changed":
                watcher = str(msg.get("watcher", "?")).strip() or "?"
                scope = str(msg.get("scope", "none")).strip() or "none"
                target = str(msg.get("target", "")).strip()
                updated_by = str(msg.get("updated_by", "?")).strip() or "?"
                if scope == "none":
                    self.add_system(f"watch cleared: {watcher} (by {updated_by})", "system_fg")
                elif scope == "agent" and target:
                    self.add_system(
                        f"watch set: {watcher} -> @{target} (observe-only, by {updated_by})",
                        "granted",
                    )
                else:
                    label = target if target else ("human" if scope == "maps" else scope)
                    self.add_system(f"watch set: {watcher} -> {label} (by {updated_by})", "granted")
            elif action == "agents_summoned":
                targets = ", ".join(str(item) for item in msg.get("targets", [])) or "(none)"
                auto_enabled = [str(item) for item in msg.get("auto_enabled", []) if str(item)]
                suffix = f" · auto-enabled: {', '.join(auto_enabled)}" if auto_enabled else ""
                self.add_system(
                    f"summon: {msg.get('issued_by', '?')} called {targets}{suffix}",
                    "granted",
                )
            elif action == "agents_stood_down":
                targets = ", ".join(str(item) for item in msg.get("targets", [])) or "temporary agents"
                auto_disabled = [str(item) for item in msg.get("auto_disabled", []) if str(item)]
                suffix = f" · reverted: {', '.join(auto_disabled)}" if auto_disabled else ""
                self.add_system(
                    f"standdown: {msg.get('issued_by', '?')} released {targets}{suffix}",
                    "system_fg",
                )
            elif action == "directive_ack":
                directive_id = str(msg.get("directive_id", "")).strip() or "?"
                state = str(msg.get("state", "accepted")).strip().lower() or "accepted"
                agent_name = str(msg.get("agent_name", "?")).strip() or "?"
                self.add_system(
                    f"directive [{directive_id}] ack: {agent_name} -> {state}",
                    "system_fg",
                )

        elif t == "approval_request":
            self.add_approval_request(msg)

        elif t == "approval_granted":
            req_id = str(msg.get("request_id", "")).strip()
            if req_id:
                self._pending_request_ids = [
                    rid for rid in self._pending_request_ids if rid != req_id
                ]
            self.add_system(
                f"✓ approved [{msg.get('request_id')}] {msg.get('command')} "
                f"(by {msg.get('approved_by')})",
                "granted",
            )

        elif t == "approval_denied":
            req_id = str(msg.get("request_id", "")).strip()
            if req_id:
                self._pending_request_ids = [
                    rid for rid in self._pending_request_ids if rid != req_id
                ]
            self.add_system(f"✗ denied [{msg.get('request_id')}]", "denied")

        elif t == "error":
            self.add_system(f"hub error: {msg.get('message')}", "denied")

    # -----------------------------------------------------------------------
    # Input handling
    # -----------------------------------------------------------------------

    def _reset_completion_state(self):
        self._completion_seed = None
        self._completion_matches = []
        self._completion_index = -1

    def _slash_completion_context(self) -> Optional[dict]:
        text = self.edit.get_edit_text()
        cursor = self.edit.edit_pos
        if not text.startswith("/") or cursor <= 0:
            return None

        before = text[:cursor]
        if not before.startswith("/"):
            return None

        body = before[1:]
        if " " not in body:
            return {
                "kind": "command",
                "key": ("command",),
                "token_start": 1,
                "prefix": body.lower(),
                "pool": list(SLASH_COMMANDS),
                "space_if_single": False,
            }

        stripped = before.rstrip()
        parts = stripped.split()
        if not parts:
            return None
        command_token = parts[0]
        if not command_token.startswith("/"):
            return None

        command = command_token[1:].lower()
        if before.endswith(" "):
            token_start = cursor
            prefix = ""
            arg_index = len(parts)  # first arg after command is 1
        else:
            token_start = before.rfind(" ") + 1
            prefix = before[token_start:cursor].lower()
            arg_index = len(parts) - 1

        pool: list[str] = []
        space_if_single = False

        if command in ("enable", "disable") and arg_index == 1:
            pool = list(_backend_agents())
        elif command in ("summon", "standdown", "brief") and arg_index >= 1:
            pool = ["all"] + list(_backend_agents())
        elif command == "mode":
            if arg_index == 1:
                pool = list(_backend_agents())
                space_if_single = True
            elif arg_index == 2:
                pool = list(AGENT_MODES)
        elif command == "watch":
            if arg_index == 1:
                pool = ["all"] + list(_backend_agents())
                space_if_single = True
            elif arg_index == 2:
                pool = ["off", "human", "room"] + [f"@{name}" for name in _backend_agents()]
        elif command == "theme" and arg_index == 1:
            pool = list(THEMES.keys()) + ["phosphor"]
        elif command in ("autoapprove", "aa") and arg_index == 1:
            pool = ["on", "off"]
        elif command in ("approve", "deny") and arg_index == 1:
            pool = list(self._pending_request_ids)

        if not pool:
            return None

        return {
            "kind": "arg",
            "key": ("arg", command, arg_index),
            "token_start": token_start,
            "prefix": prefix,
            "pool": pool,
            "space_if_single": space_if_single,
        }

    def _complete_slash_command(self, reverse: bool = False) -> bool:
        ctx = self._slash_completion_context()
        if not ctx:
            return False

        token_start = int(ctx["token_start"])
        prefix = str(ctx["prefix"])
        pool = [str(item) for item in ctx["pool"]]
        seed = (ctx["key"], token_start, tuple(pool))

        if self._completion_seed != seed:
            matches = [item for item in pool if item.lower().startswith(prefix)]
            if not matches:
                return False
            self._completion_seed = seed
            self._completion_matches = matches
            self._completion_index = -1
        elif not self._completion_matches:
            return False

        count = len(self._completion_matches)
        if reverse:
            if self._completion_index < 0:
                self._completion_index = count - 1
            else:
                self._completion_index = (self._completion_index - 1) % count
        else:
            self._completion_index = (self._completion_index + 1) % count

        choice = self._completion_matches[self._completion_index]
        text = self.edit.get_edit_text()
        cursor = self.edit.edit_pos
        new_text = text[:token_start] + choice + text[cursor:]
        new_pos = token_start + len(choice)

        append_space = False
        if ctx["kind"] == "command":
            append_space = (
                len(self._completion_matches) == 1
                and choice not in SLASH_NOARG_COMMANDS
            )
        else:
            append_space = (
                bool(ctx.get("space_if_single")) and len(self._completion_matches) == 1
            )

        if append_space and (new_pos >= len(new_text) or new_text[new_pos] != " "):
            new_text = new_text[:new_pos] + " " + new_text[new_pos:]
            new_pos += 1

        self.edit.set_edit_text(new_text)
        self.edit.edit_pos = new_pos
        return True

    def _show_help(self):
        self.add_system("slash commands:")
        for cmd, desc in SLASH_HELP:
            self.add_system(f"  {cmd} - {desc}")
        self.add_system("tip: press Tab / Shift-Tab to complete commands and arguments")

    def handle_input(self, key):
        # ── Bracketed paste ────────────────────────────────────────────────
        if key == "begin paste":
            self._pasting = True
            self._paste_buffer = []
            return
        if key == "end paste":
            self._pasting = False
            text = (
                "".join(self._paste_buffer)
                .replace("\n", " ")
                .replace("\r", " ")
                .strip()
            )
            self._paste_buffer = []
            if text:
                self.edit.insert_text(text)
            return
        if self._pasting:
            if key == "enter":
                self._paste_buffer.append("\n")
            elif isinstance(key, str) and len(key) == 1:
                self._paste_buffer.append(key)
            return

        # ── Tab completion ─────────────────────────────────────────────────
        if key == "tab":
            self._complete_slash_command(reverse=False)
            return
        if key in ("shift tab", "backtab"):
            self._complete_slash_command(reverse=True)
            return

        self._reset_completion_state()

        # ── Send ───────────────────────────────────────────────────────────
        if key == "enter":
            text = self.edit.get_edit_text().strip()
            if text:
                if not self._history or self._history[-1] != text:
                    self._history.append(text)
                self._history_pos = -1
                self._history_draft = ""
                self.edit.set_edit_text("")
                asyncio.get_event_loop().create_task(self._handle_user_input(text))

        # ── History navigation ─────────────────────────────────────────────
        elif key == "up":
            self._history_navigate(-1)
        elif key == "down":
            self._history_navigate(1)

        # ── Scroll to bottom ───────────────────────────────────────────────
        elif key == "end":
            if self.walker:
                self.listbox.set_focus(len(self.walker) - 1)
            if self._loop:
                self._loop.draw_screen()

        # ── Editing shortcuts ──────────────────────────────────────────────
        elif key == "ctrl u":
            self.edit.set_edit_text("")
            self._history_pos = -1

        elif key == "ctrl l":
            self._clear_messages()

        # ── Navigation / exit ──────────────────────────────────────────────
        elif key == "esc":
            self.frame.set_focus("footer")
        elif (
            isinstance(key, str) and len(key) == 1 and self.frame.focus_part != "footer"
        ):
            self.frame.set_focus("footer")
            self.edit.insert_text(key)
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

        if verb in ("help", "ahelp", "h", "?"):
            self._show_help()
        elif verb == "approve" and len(parts) >= 2:
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
        elif verb in ("enable", "disable"):
            if len(parts) < 2:
                self.add_system(f"usage: /{verb} <agent>", "denied")
                return
            await self._run_agent_control(verb, parts[1].lower())
        elif verb == "mode":
            if len(parts) < 3:
                self.add_system("usage: /mode <agent> <mention|always|off>", "denied")
                return
            await self._run_agent_mode(parts[1].lower(), parts[2].lower())
        elif verb == "modes":
            await self._run_agent_modes()
        elif verb in FREE_MODE_COMMANDS:
            await self._run_free_mode()
        elif verb == "theme":
            if len(parts) < 2:
                self.add_system(f"theme: {self._theme_name}", "system_fg")
                return
            selected = self._apply_theme(parts[1], persist=True)
            self.add_system(f"theme: {selected}", "granted")
        elif verb == "themes":
            self.add_system("themes:")
            for line in self._theme_lines():
                self.add_system(f"  {line}")
        elif verb == "which" and len(parts) >= 2:
            task = " ".join(parts[1:])
            await self._run_which_query(task)
        elif verb in ("agents", "agentstatus"):
            await self._run_agent_status()
        elif verb == "rollcall":
            await self._run_rollcall()
        elif verb == "topic":
            if len(parts) >= 2:
                self._topic = " ".join(parts[1:])
                self.add_system(f"topic: {self._topic}", "granted")
            else:
                self.add_system(f"topic: {self._topic or '(not set)'}", "system_fg")
        elif verb == "search" and len(parts) >= 2:
            query = " ".join(parts[1:]).lower()
            self._run_message_search(query)
        elif verb == "pin" and len(parts) >= 2:
            try:
                msg_id = int(parts[1])
                self._run_pin_message(msg_id)
            except ValueError:
                self.add_system("usage: /pin <message_id>", "denied")
        elif verb == "pins":
            self._run_list_pins()
        elif verb == "summon":
            await self._run_summon_command(parts[1:])
        elif verb == "brief":
            await self._run_brief_command(cmd)
        elif verb == "watch":
            await self._run_watch_command(parts[1:])
        elif verb == "standdown":
            await self._run_standdown_command(parts[1:])
        elif verb == "clear":
            self._clear_messages()
        elif verb == "restart":
            full = "--full" in parts[1:] or "full" in parts[1:]
            self._request_restart(full=full)
        elif verb in ("quit", "q", "exit"):
            self._request_exit()
        else:
            self.add_system(f"unknown command: /{verb} (try /help)", "denied")

    async def _run_polycule_cli(self, *args: str) -> tuple[int, str, str]:
        if not POLYCULE_BIN.exists():
            return 127, "", f"polycule CLI not found at {POLYCULE_BIN}"
        proc = await asyncio.to_thread(
            subprocess.run,
            [str(POLYCULE_BIN), *args],
            capture_output=True,
            text=True,
        )
        return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()

    async def _run_agent_control(self, action: str, agent: str):
        if agent not in _backend_agent_set():
            allowed = ", ".join(_backend_agents())
            self.add_system(f"invalid agent: {agent} (use one of: {allowed})", "denied")
            return
        code, out, err = await self._run_polycule_cli("agent", action, agent)
        if code != 0:
            detail = err or out or f"failed to {action} {agent}"
            self.add_system(detail, "denied")
            return
        self.add_system(out or f"{action}d {agent}", "granted")

    async def _run_agent_mode(self, agent: str, mode: str):
        if agent not in _backend_agent_set():
            allowed = ", ".join(_backend_agents())
            self.add_system(f"invalid agent: {agent} (use one of: {allowed})", "denied")
            return
        if mode not in AGENT_MODES:
            allowed_modes = ", ".join(AGENT_MODES)
            self.add_system(
                f"invalid mode: {mode} (use one of: {allowed_modes})", "denied"
            )
            return
        code, out, err = await self._run_polycule_cli("agent", "mode", agent, mode)
        if code != 0:
            detail = err or out or f"failed to set mode {agent}={mode}"
            self.add_system(detail, "denied")
            return
        self.add_system(out or f"set mode {agent}={mode}", "granted")

    async def _run_agent_modes(self):
        code, out, err = await self._run_polycule_cli("agent", "modes")
        if code != 0:
            detail = err or out or "failed to query agent modes"
            self.add_system(detail, "denied")
            return
        lines = [line.strip() for line in out.splitlines() if line.strip()]
        if not lines:
            self.add_system("no agent mode state available", "denied")
            return
        for line in lines:
            self.add_system(line)

    @staticmethod
    def _parse_target_agents(tokens: list[str]) -> list[str]:
        if not tokens:
            return []
        out: list[str] = []
        for raw in tokens:
            value = raw.strip().lower().rstrip(",")
            if not value:
                continue
            if value == "all":
                return list(_backend_agents())
            if value in _backend_agent_set() and value not in out:
                out.append(value)
        return out

    async def _load_agent_state(self) -> dict[str, dict[str, str]]:
        code, out, err = await self._run_polycule_cli("agent", "status")
        if code != 0:
            detail = err or out or "failed to query agent status"
            self.add_system(detail, "denied")
            return {}
        state = self._parse_agent_status_output(out)
        self._last_agent_state_snapshot = state
        return state

    def _get_temporary_enablements(self) -> dict[str, dict]:
        return get_temporary_agent_enablements(self.room)

    def _mark_temporary_enablements(self, agents: list[str], reason: str):
        mark_temporary_agent_enablements(
            self.room,
            agents,
            agent_state=self._last_agent_state_snapshot,
            temporary_mode="mention",
            enabled_by=self.name,
            reason=reason,
        )

    def _clear_temporary_enablements(self, agents: Optional[list[str]] = None):
        clear_temporary_agent_enablements(self.room, agent_names=agents)

    async def _ensure_targets_ready(
        self,
        targets: list[str],
        *,
        reason: str,
    ) -> tuple[list[str], dict[str, dict[str, str]]]:
        state = await self._load_agent_state()
        if not state:
            return [], {}
        auto_enabled: list[str] = []
        auto_mode_restores: list[str] = []
        for agent in targets:
            status = state.get(agent, {})
            current_mode = status.get("mode", DEFAULT_AGENT_MODE(agent))
            if status.get("state", "enabled") != "enabled":
                code, out, err = await self._run_polycule_cli("agent", "enable", agent)
                if code != 0:
                    self.add_system(err or out or f"failed to enable {agent}", "denied")
                    continue
                auto_enabled.append(agent)
                self.add_system(out or f"enabled {agent}", "granted")
                state[agent] = {
                    "state": "enabled",
                    "mode": current_mode,
                }
            if state[agent]["mode"] == "off":
                code, out, err = await self._run_polycule_cli(
                    "agent", "mode", agent, "mention"
                )
                if code != 0:
                    self.add_system(
                        err or out or f"failed to set mode {agent}=mention",
                        "denied",
                    )
                    continue
                auto_mode_restores.append(agent)
                self.add_system(out or f"set mode {agent}=mention", "granted")
                state[agent]["mode"] = "mention"
        touched = sorted(set(auto_enabled + auto_mode_restores))
        if touched:
            self._mark_temporary_enablements(touched, reason)
            await asyncio.sleep(0.35)
        return auto_enabled, state

    async def _run_summon_command(self, args: list[str]):
        if not self.room_id:
            self.add_system("not connected to a room", "denied")
            return
        targets = self._parse_target_agents(args)
        if not targets:
            self.add_system("usage: /summon <all|agent...>", "denied")
            return
        auto_enabled, _state = await self._ensure_targets_ready(targets, reason="summon")
        sent = await self._send(
            {
                "type": "command",
                "command": "summon_agents",
                "room_id": self.room_id,
                "targets": targets,
                "auto_enabled": auto_enabled,
            }
        )
        if sent:
            self.add_system(
                f"summoned: {', '.join(targets)}"
                + (f" · auto-enabled {', '.join(auto_enabled)}" if auto_enabled else ""),
                "granted",
            )

    async def _run_brief_command(self, cmd: str):
        if not self.room_id:
            self.add_system("not connected to a room", "denied")
            return
        _verb, _sep, rest = cmd.partition(" ")
        target_blob, separator, body = rest.partition(" -- ")
        if not separator:
            self.add_system("usage: /brief <all|agent...> -- <message>", "denied")
            return
        targets = self._parse_target_agents(target_blob.split())
        content = body.strip()
        if not targets or not content:
            self.add_system("usage: /brief <all|agent...> -- <message>", "denied")
            return
        auto_enabled, _state = await self._ensure_targets_ready(targets, reason="brief")
        sent = await self._send(
            {
                "type": "command",
                "command": "send_directive",
                "room_id": self.room_id,
                "directive_kind": "brief",
                "targets": targets,
                "content": content,
                "refs": [],
            }
        )
        if sent:
            suffix = f" · auto-enabled {', '.join(auto_enabled)}" if auto_enabled else ""
            self.add_system(
                f"brief sent to {', '.join(targets)}{suffix}",
                "granted",
            )

    async def _run_watch_command(self, args: list[str]):
        if not self.room_id:
            self.add_system("not connected to a room", "denied")
            return
        if len(args) < 2:
            self.add_system(
                "usage: /watch <agent|all> <off|human|room|@agent>",
                "denied",
            )
            return
        watchers = self._parse_target_agents(args[:-1])
        if not watchers:
            self.add_system(
                "usage: /watch <agent|all> <off|human|room|@agent>",
                "denied",
            )
            return
        scope_token = args[-1].strip().lower()
        target = ""
        if scope_token.startswith("@"):
            scope = "agent"
            target = scope_token[1:]
        else:
            scope = scope_token
        sent = await self._send(
            {
                "type": "command",
                "command": "set_watch",
                "room_id": self.room_id,
                "watchers": watchers,
                "scope": scope,
                "target": target,
            }
        )
        if sent:
            label = f"@{target}" if scope == "agent" and target else scope
            self.add_system(
                f"watch update queued: {', '.join(watchers)} -> {label}",
                "granted",
            )

    async def _run_standdown_command(self, args: list[str]):
        if not self.room_id:
            self.add_system("not connected to a room", "denied")
            return
        recorded = self._get_temporary_enablements()
        if not recorded:
            self.add_system("no temporary summon state recorded", "system_fg")
            return
        targets = self._parse_target_agents(args) if args else list(recorded.keys())
        if not targets:
            self.add_system("usage: /standdown <all|agent...>", "denied")
            return
        touched = [agent for agent in targets if agent in recorded]
        restored_disabled: list[str] = []
        for agent in touched:
            entry = recorded.get(agent, {})
            previous_mode = str(entry.get("previous_mode", "")).strip().lower()
            previous_state = str(entry.get("previous_state", "")).strip().lower()
            if previous_mode and previous_mode != "mention":
                code, out, err = await self._run_polycule_cli(
                    "agent", "mode", agent, previous_mode
                )
                if code != 0:
                    self.add_system(
                        err or out or f"failed to restore mode {agent}={previous_mode}",
                        "denied",
                    )
                else:
                    self.add_system(
                        out or f"set mode {agent}={previous_mode}",
                        "system_fg",
                    )
            if previous_state == "disabled":
                code, out, err = await self._run_polycule_cli("agent", "disable", agent)
                if code != 0:
                    self.add_system(err or out or f"failed to disable {agent}", "denied")
                    continue
                restored_disabled.append(agent)
                self.add_system(out or f"disabled {agent}", "system_fg")
        if touched:
            self._clear_temporary_enablements(touched)
        elif not args:
            self._clear_temporary_enablements()
        sent = await self._send(
            {
                "type": "command",
                "command": "standdown_agents",
                "room_id": self.room_id,
                "targets": targets,
                "auto_disabled": restored_disabled,
            }
        )
        if sent:
            self.add_system(
                f"standdown: {', '.join(targets)}"
                + (f" · reverted {', '.join(restored_disabled)}" if restored_disabled else ""),
                "system_fg",
            )

    @staticmethod
    def _parse_agent_status_output(output: str) -> dict[str, dict[str, str]]:
        state: dict[str, dict[str, str]] = {}
        for raw_line in output.splitlines():
            line = raw_line.strip()
            if not line or ":" not in line:
                continue
            agent, rest = line.split(":", 1)
            name = agent.strip().lower()
            if name not in _backend_agent_set():
                continue
            fields: dict[str, str] = {}
            for token in rest.strip().split():
                if "=" not in token:
                    continue
                key, value = token.split("=", 1)
                fields[key.strip().lower()] = value.strip().lower()
            if fields:
                state[name] = fields
        return state

    @staticmethod
    def _recommend_agents_for_task(
        task: str,
        agent_state: dict[str, dict[str, str]],
    ) -> list[tuple[str, int, str]]:
        lowered = task.lower()
        words = re.findall(r"[a-z0-9_+-]+", lowered)
        joined = " ".join(words)
        recommendations: list[tuple[str, int, str]] = []

        backend_agents = _backend_agents()
        hints = _agent_capability_hints()
        free_agents = _free_agents()
        for agent in backend_agents:
            status = agent_state.get(agent, {})
            current_state = status.get("state", "enabled")
            current_mode = status.get("mode", DEFAULT_AGENT_MODE(agent))
            if current_state != "enabled" or current_mode == "off":
                continue

            profile = hints.get(agent, {})
            score = 1
            reasons = []
            for keyword, weight in profile.get("keywords", {}).items():
                if keyword in joined:
                    score += int(weight)
                    reasons.append(keyword)
            if agent in free_agents:
                score += 1
            if current_mode == "always":
                score += 1
            if not reasons:
                reasons.append(profile.get("summary", "general fit"))
            recommendations.append((agent, score, ", ".join(reasons[:3])))

        recommendations.sort(key=lambda item: (-item[1], backend_agents.index(item[0])))
        return recommendations

    async def _run_free_mode(self):
        self._free_mode = not self._free_mode
        paid_agents = _paid_agents()
        free_agents = list(_free_agents())
        for agent in paid_agents:
            if self._free_mode:
                code, out, err = await self._run_polycule_cli("agent", "disable", agent)
                if code != 0:
                    self.add_system(err or out or f"failed to disable {agent}", "denied")
            else:
                code, out, err = await self._run_polycule_cli("agent", "enable", agent)
                if code != 0:
                    self.add_system(err or out or f"failed to enable {agent}", "denied")
        if self._free_mode:
            status_msg = (
                "free mode: only local/non-premium agents enabled "
                f"({', '.join(free_agents) if free_agents else 'none'})"
            )
        else:
            status_msg = "free mode off: all agents enabled"
        self.add_system(status_msg, "granted" if self._free_mode else "system_fg")
        self._update_header("free" if self._free_mode else "")

    async def _run_rollcall(self):
        if not self.room_id:
            self.add_system("not connected to a room", "denied")
            return
        code, out, err = await self._run_polycule_cli("agent", "status")
        if code != 0:
            detail = err or out or "failed to query agent status"
            self.add_system(detail, "denied")
            return
        state = self._parse_agent_status_output(out)
        backend_agents = _backend_agents()
        active = [
            agent
            for agent in backend_agents
            if state.get(agent, {}).get("state", "enabled") == "enabled"
            and state.get(agent, {}).get("mode", DEFAULT_AGENT_MODE(agent)) != "off"
        ]
        if not active:
            self.add_system("rollcall: no active backend agents", "denied")
            return
        content = " ".join(f"@{agent}" for agent in active) + " roll call"
        sent = await self._send({"type": "message", "room_id": self.room_id, "content": content})
        if sent:
            self.add_system(f"rollcall: {', '.join(active)}", "granted")

    async def _run_which_query(self, task: str):
        code, out, err = await self._run_polycule_cli("agent", "status")
        if code != 0:
            detail = err or out or "failed to query agent status"
            self.add_system(detail, "denied")
            return
        state = self._parse_agent_status_output(out)
        ranked = self._recommend_agents_for_task(task, state)
        if not ranked:
            self.add_system("which: no enabled agents available", "denied")
            return
        best_agent, best_score, best_reason = ranked[0]
        self.add_system(f"which: @{best_agent} ({best_reason})", "granted")
        backups = [
            f"@{agent} ({reason})"
            for agent, _score, reason in ranked[1:3]
        ]
        if backups:
            self.add_system(f"fallbacks: {', '.join(backups)}", "system_fg")
        self.add_system(f"task: {task}", "system_fg")

    def _run_message_search(self, query: str):
        """Search messages for query."""
        matches = 0
        for msg in self.walker:
            if hasattr(msg, "content") and query in msg.content.lower():
                matches += 1
        self.add_system(f"found {matches} messages matching '{query}'", "system_fg")

    def _run_pin_message(self, msg_id: int):
        """Pin a message by ID."""
        self._pinned_messages[msg_id] = {"id": msg_id, "pinned_at": "now"}
        self.add_system(f"pinned message {msg_id}", "granted")

    def _run_list_pins(self):
        """List pinned messages."""
        if not self._pinned_messages:
            self.add_system("no pinned messages", "system_fg")
            return
        for msg_id in self._pinned_messages:
            self.add_system(f"pinned: message {msg_id}", "granted")

    async def _run_agent_status(self):
        code, out, err = await self._run_polycule_cli("agent", "status")
        if code != 0:
            detail = err or out or "failed to query agent status"
            self.add_system(detail, "denied")
            return
        lines = [line.strip() for line in out.splitlines() if line.strip()]
        if not lines:
            self.add_system("no agent status available", "denied")
            return
        for line in lines:
            self.add_system(line)

    async def _send(self, obj: dict) -> bool:
        if not self.writer or self.writer.is_closing() or not self.connected:
            self.add_system("not connected (reconnecting…)", "denied")
            return False
        try:
            self.writer.write((json.dumps(obj) + "\n").encode())
            await self.writer.drain()
            return True
        except Exception as e:
            self.add_system(f"send error: {e}", "denied")
            return False

    async def _close_writer(self):
        if self.writer and not self.writer.is_closing():
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except Exception:
                pass
        self.writer = None
        self.reader = None

    async def shutdown(self):
        self._shutting_down = True
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
        if self._recv_task and not self._recv_task.done():
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
        self.connected = False
        await self._close_writer()

    def _request_exit(self):
        self._shutting_down = True
        if self._loop:
            self._loop.stop()

    def _request_restart(self, full: bool = False):
        if full:
            self._full_restart_requested = True
            self.add_system(
                "full restart — hub, agents, and TUI restarting…", "system_fg"
            )
        else:
            self._restart_requested = True
            self.add_system("restarting TUI — will reconnect to hub…", "system_fg")
        self._request_exit()

    # -----------------------------------------------------------------------
    # Run
    # -----------------------------------------------------------------------

    def start(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        connected = loop.run_until_complete(self.connect())
        if not connected:
            self._schedule_reconnect()
        event_loop = urwid.AsyncioEventLoop(loop=loop)
        from urwid.display._posix_raw_display import Screen as RawScreen

        screen = RawScreen(bracketed_paste_mode=True)
        self._loop = urwid.MainLoop(
            self.frame,
            _build_palette(self._theme_name),
            screen=screen,
            event_loop=event_loop,
            unhandled_input=self.handle_input,
        )
        try:
            self._loop.run()
        except urwid.ExitMainLoop:
            pass
        finally:
            loop.run_until_complete(self.shutdown())
            loop.close()

        if self._full_restart_requested:
            subprocess.Popen([str(POLYCULE_BIN), "start", "--background", "--new"])
        elif self._restart_requested:
            os.execv(sys.executable, sys.argv)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Polycule Chat TUI")
    parser.add_argument("--name", default=DEFAULT_HUMAN_NAME)
    parser.add_argument("--room", default="Default")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=7777)
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)

    tui = ChatTUI(name=args.name, room=args.room, host=args.host, port=args.port)
    tui.start()


if __name__ == "__main__":
    main()
