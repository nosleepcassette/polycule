# Polycule Build State Log

## 2026-04-04 — Demo live, post-compaction fixes

### Bugs fixed (post-compaction)

- **TUI asyncio conflict**: `asyncio.run(tui.run())` + urwid `AsyncioEventLoop` hit `RuntimeError: This event loop is already running`. Fixed by replacing `async def run(self)` with synchronous `def start(self)` using `asyncio.new_event_loop()`.
- **`awaiting_room` field mismatch**: Hub sends `{'type': 'system', 'action': 'awaiting_room'}`. Both `base_adapter.py` and `chat_tui.py` were checking `response.get('type') == 'awaiting_room'` — should be `response.get('action') == 'awaiting_room'`. Fixed in both.
- **Agent pane layout**: All adapters moved out of the demo window into background processes, logging to `logs/*.log`. Demo window is now just hub + TUI.
- **README**: Created `README.md` for public consumption.

### Demo state (end of 2026-04-04)
- Hub: `localhost:7777`, room `Demo`
- TUI: pane %133, session 13, window `polycule-demo`
- Wizard, Cassette, Claude, Codex: background processes, logging to `logs/`
- All four agents confirmed connected to room per hub log

---

## 2026-04-04 — Phases 1–4 Complete

### What was built

**Phase 1 — Bug fixes**
- Fixed `PolyculeServer.stop()`: was checking `self.rooms` (doesn't exist on server), now correctly uses `self.router.rooms`
- Verified `join_room` call in `create_room` handler is present and logs correctly
- Rewrote `base_adapter.py`: proper handshake with `room_name`, async subprocess I/O via `asyncio.to_thread`, context accumulation, overridable hooks
- Deleted `src/tmux_controller_v2.py` (duplicate)
- Added `__init__.py` package files

**Phase 2 — Persistence + session init + auto-approve**
- `src/backend/db.py`: SQLite layer with WAL mode. Tables: rooms, messages, settings
- Hub integration: rooms restored from DB on startup, messages persisted on every broadcast, context history served from DB
- Context dump: hub sends last N messages (configurable, default 100) to every new agent on connect
- Auto-approve: stored in `settings` table, toggled via `set_auto_approve` hub command, persists across restarts
- Approval flow: structural commands (split_window, kill_pane, etc.) broadcast approval_request; maps sends approve/deny; or auto-executes if auto_approve=true
- `src/session_init.py`: detects `$TMUX` env, fzf picker for multiple sessions, creates polycule + swarm windows, labels panes with @name for tmux-bridge compatibility
- `create_room` command is now join-or-create by name (idempotent — tested)

**Phase 3 — Chat TUI**
- `src/ui/chat_tui.py`: urwid-based IRC-style TUI
  - Per-agent color coding (maps=cyan, hermes/wizard=magenta, claude=blue, codex=green)
  - Scrollable message list, input pinned to bottom
  - Loads history from room_state + context_dump on connect
  - Inline approval request rendering
  - Slash commands: /approve, /deny, /autoapprove, /rooms, /join, /quit
  - Shorthand: `approve <req_id>` / `deny <req_id>` without slash

**Phase 4 — Agent adapters + CLI**
- `src/agents/hermes_adapter.py`: Hermes adapter
  - Supports two profiles: `cassette` (default) and `wizard`
  - Cassette: `hermes chat -Q -q "prompt"`
  - Wizard: `hermes chat --profile wizard -Q -q "prompt"`
  - Mention-triggered (cassette triggers on: @cassette, @hermes, cassette, hermes; wizard also triggers on @wizard, wizard)
  - `--always` flag for full room participation
  - `--resume SESSION_ID` for session continuity
  - Injects last 60 messages of context into every prompt
- `src/agents/claude_adapter.py`: Claude Code adapter
  - Uses `claude -p "prompt"` one-shot mode
  - Triggers on @claude, claude mentions
  - `--model` flag, `--resume` support
- `src/agents/codex_adapter.py`: Codex adapter
  - Uses `/usr/local/bin/codex exec "prompt"` (bypasses shell wrapper to avoid post-run hooks)
  - Triggers on @codex, codex mentions
  - `--resume` support
- `bin/polycule`: CLI
  - `polycule start` — init session + hub + TUI
  - `polycule hub` — start hub
  - `polycule tui` — start TUI
  - `polycule agent hermes|cassette|wizard|claude|codex` — launch adapter
  - `polycule approve on|off` — toggle auto-approve
  - `polycule status` — check hub

### Integration test results
```
Alice room_id: c96f41a7  ✓
Bob room_id: c96f41a7   ✓  (same room, join-or-create works)
Bob received: message 'ping from alice'  ✓
Message routing: PASS
```

### Known pending / not built
- `~/dev/hey`: needs local `wizard` hermes profile (`wizard` currently routes to opencassette OpenClaw only)
- Eidetic logging to `~/dev/eidetic/logs/polycule/` not wired
- Garden sync not implemented
- Anarchy mode not built (spec'd in spec.md)
- Rate limiting not implemented
- tmux-bridge not yet integrated into adapters (adapters use subprocess directly)
- `polycule start` sends hub/TUI commands to panes but doesn't wait for hub readiness before starting TUI (1s sleep is a workaround)
- Claude/Codex adapters use one-shot `-p`/`exec` mode; no interactive REPL bridge for long-running tasks

### File tree
```
~/dev/polycule/
├── bin/polycule              ← CLI (chmod +x)
├── src/
│   ├── backend/
│   │   ├── hub.py            ← Async TCP server + approval + DB integration
│   │   └── db.py             ← SQLite persistence
│   ├── ui/
│   │   └── chat_tui.py       ← urwid IRC chat UI
│   ├── agents/
│   │   ├── base_adapter.py   ← Base class
│   │   ├── hermes_adapter.py ← Hermes/Wizard (cassette + wizard profiles)
│   │   ├── claude_adapter.py ← Claude Code
│   │   └── codex_adapter.py  ← Codex
│   ├── tmux_controller.py    ← Tmux pane management
│   └── session_init.py       ← Session detection + layout
├── logs/hub.log
├── polycule.db               ← SQLite (gitignored)
├── BUILD.md                  ← Comprehensive guide for other agents
├── STATE.md                  ← This file
└── spec.md, research-findings.md, HANDOVER-v3-FINAL.md
```
