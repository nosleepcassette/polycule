# Polycule Build Sheet
**Project:** polycule — multi-agent collaborative tmux workspace
**Maintained by:** maps · cassette.help
**For:** any agent picking up this build mid-stream

---

## WHAT THIS IS

A local, tmux-based environment where multiple AI agents (Hermes/Cassette, Claude Code, Codex, maps) coexist in a structured workspace. Agents communicate through a central TCP message broker (`polycule-hub`) instead of typing raw commands into shared panes. The hub enforces labeling, routing, and safety. Maps gets a dedicated input pane. Structural changes (split/kill panes) route through the hub with optional approval.

**Not:** a web app, not a cloud service, not a daemon. Local-first, runs on maps' machine.

---

## FILE STRUCTURE

```
~/dev/polycule/
├── bin/
│   └── polycule              # CLI entry point (Python)
├── src/
│   ├── backend/
│   │   ├── hub.py            # Async TCP server, message router, approval system
│   │   └── db.py             # SQLite persistence layer
│   ├── ui/
│   │   └── chat_tui.py       # urwid IRC-style chat TUI
│   ├── agents/
│   │   ├── base_adapter.py   # Base class: TCP connect, send/receive, context load
│   │   ├── hermes_adapter.py # Hermes/Cassette - stateless, one-shot, mention-triggered
│   │   └── claude_adapter.py # Claude Code CLI subprocess bridge
│   ├── tmux_controller.py    # Tmux pane management wrapper
│   └── session_init.py       # Session detection, fzf picker, layout setup
├── logs/
│   └── hub.log               # Hub log (append-only, rotate manually)
├── polycule.db               # SQLite DB (gitignored)
├── BUILD.md                  # This file
├── spec.md                   # Original architecture spec
├── research-findings.md      # Prior art research (Wizard)
└── HANDOVER-v3-FINAL.md      # Full handover from Wizard
```

---

## CURRENT STATE (updated per phase)

### Phase 1 — COMPLETE (2026-04-04)
- [x] Fixed `stop()` method: was referencing `self.rooms`, now uses `self.router.rooms`
- [x] Verified `join_room` call in `create_room` handler present and logging correctly
- [x] Fixed `base_adapter.py`: complete rewrite — handshake includes `room_name`, async subprocess I/O, proper context accumulation, all hooks overridable
- [x] Deleted `src/tmux_controller_v2.py` (duplicate of v1)
- [x] Added `__init__.py` files for package structure

### Phase 2 — COMPLETE (2026-04-04)
- [x] `src/backend/db.py` — SQLite layer (rooms, messages, settings) with WAL mode
- [x] Hub integrated with DB: rooms restored on startup, messages persisted on broadcast, context history queryable
- [x] Context dump: hub sends last N msgs (DB setting `context_window`, default 100) to every new agent on connect
- [x] Auto-approve mode: stored in DB settings table, toggled via `set_auto_approve` command, persists across restarts
- [x] Approval flow: structural commands broadcast `approval_request`, maps sends `approve`/`deny`, or auto-executes if auto_approve=true
- [x] `src/session_init.py` — detects `$TMUX` env, fzf picker for multiple sessions, creates polycule + swarm windows, labels panes with @name for tmux-bridge

### Phase 3 — COMPLETE (2026-04-04)
- [x] `src/ui/chat_tui.py` — urwid TUI, IRC-style, per-agent color coding, scrollable message list, input pinned to bottom
- [x] Connects as `human` agent, loads history from room_state + context_dump
- [x] Inline approval request rendering with grant/deny instructions
- [x] Slash commands: /approve, /deny, /autoapprove, /rooms, /join, /quit
- [x] Auto-approve shortcuts: `approve <req_id>` / `deny <req_id>` (no slash needed)

### Phase 4 — COMPLETE (2026-04-04)
- [x] `src/agents/hermes_adapter.py` — stateless cassette adapter, mention-triggered (hermes/wizard/cassette), injects last 60 msgs as context into every cassette call, `--always` flag for full participation
- [x] `src/agents/claude_adapter.py` — Claude Code CLI bridge via `claude -p` one-shot mode
- [x] `bin/polycule` — CLI: start (full launch), hub, tui, agent, approve, status
- [x] `create_room` is now join-or-create by name (idempotent — multiple agents connecting to same room_name land in the same room)
- [x] Integration tested: two agents, same room, message routing confirmed PASS
- [x] Filed: `~/dev/hey` needs local `wizard` hermes profile (see HEY INTEGRATION NOTE below)

---

## PROTOCOL REFERENCE

All messages are newline-delimited JSON over TCP (localhost:7777).

### Client → Hub

```json
// Initial handshake (required, must be first message)
{"type": "handshake", "name": "Wizard", "agent_type": "hermes", "room_name": "Default"}

// Chat message
{"type": "message", "room_id": "abc12345", "content": "hello"}

// Commands
{"type": "command", "command": "create_room", "room_name": "MyRoom"}
{"type": "command", "command": "list_rooms"}
{"type": "command", "command": "leave_room", "room_id": "abc12345"}
{"type": "command", "command": "set_auto_approve", "value": true}   // maps only
{"type": "command", "command": "approve", "request_id": "xyz"}      // maps only
{"type": "command", "command": "deny", "request_id": "xyz"}         // maps only

// Structural commands (require approval unless auto_approve=true)
{"type": "command", "command": "split_window", "vertical": false, "room_id": "abc12345"}
{"type": "command", "command": "kill_pane", "pane_id": "%42", "room_id": "abc12345"}
```

### Hub → Client

```json
// Initial responses
{"type": "awaiting_room", "message": "Please send JOIN or CREATE command"}
{"type": "room_state", "room": {"room_id": "...", "room_name": "...", "agents": [...], "recent_messages": [...]}}
{"type": "room_created", "room": {...}}

// Messages from other agents
{"type": "message", "id": "...", "content": "...", "sender": {"id": "...", "name": "Wizard", "type": "hermes"}, "room_id": "...", "timestamp": "..."}

// System events
{"type": "system", "action": "agent_joined", "agent": {"id": "...", "name": "...", "type": "..."}}
{"type": "system", "action": "agent_left", "agent_id": "..."}

// History dump (sent to stateless agents on connect)
{"type": "context_dump", "room_id": "...", "messages": [...], "count": 100}

// Approval flow
{"type": "approval_request", "request_id": "xyz", "requester": "Claude", "command": "split_window", "detail": {...}}
{"type": "approval_granted", "request_id": "xyz"}
{"type": "approval_denied", "request_id": "xyz"}

// Errors
{"type": "error", "message": "..."}
```

---

## KEY DESIGN DECISIONS

### Agent invocation (Hermes, Claude, Codex)

All three are TUI REPLs. All three support one-shot non-interactive mode:

| Agent   | Interactive | One-shot (adapter uses this) | Resume |
|---------|------------|------------------------------|--------|
| Claude  | `claude`   | `claude -p "prompt"`         | `--resume SESSION_ID` |
| Hermes  | `hermes chat` | `hermes chat -Q -q "prompt"` | `--resume SESSION_ID` |
| Wizard  | `hermes chat --profile wizard` | `hermes chat --profile wizard -Q -q "prompt"` | `--resume SESSION_ID` |
| Codex   | `codex`    | `codex exec "prompt"`        | `--resume SESSION_ID` (via exec) |

Two Hermes profiles:
- **cassette** — `hermes` default profile, invoked as `hermes chat -Q -q`
- **wizard** — `hermes chat --profile wizard -Q -q`

Adapters call these in one-shot mode for each response, injecting the last 60 messages of context into the prompt. Each call creates a fresh session (no --resume by default). Pass `--resume SESSION_ID` to continue a prior conversation.

The hub sends a `context_dump` on connect so agents starting mid-session have DB history available immediately.

### Auto-approve mode
Default: off. Maps must explicitly approve structural commands (split/kill pane) via chat.
Toggle: `polycule approve on` or send `{"type": "command", "command": "set_auto_approve", "value": true}` to hub.
Stored in `polycule.db` settings table, persists across restarts.

### Session detection
`session_init.py` checks:
1. `$TMUX` env var → use that session (maps is already in tmux)
2. Multiple sessions → fzf picker
3. No sessions → create `polycule` session

The polycule layout (once attached to a session) creates:
- Window `polycule`: pane 0 = maps terminal, pane 1 = chat TUI, pane 2 = hub log
- Window `swarm`: empty, for agent panes

### tmux-bridge integration
`~/.smux/bin/tmux-bridge` is available and used by agents to read/write panes. It has a read-guard that requires a read before a write (prevents blind typing). Use it for any cross-pane communication from adapters.

```bash
tmux-bridge list                   # list all panes
tmux-bridge read <pane>            # read pane output (also marks it read)
tmux-bridge type <pane> "text"     # type text (requires prior read)
tmux-bridge name <pane> "label"    # label a pane
```

---

## RUNNING

```bash
# Full start (init session + hub + TUI)
polycule start

# Just the hub
polycule hub

# Just the TUI (assumes hub running)
polycule tui

# Launch hermes adapter as cassette (default profile)
polycule agent hermes --room Default

# Launch hermes as wizard profile
polycule agent wizard --room Default

# Launch codex adapter
polycule agent codex --room Default

# Launch claude adapter
polycule agent claude --room Default

# Resume a prior session
polycule agent wizard --resume abc123xyz

# Toggle auto-approve
polycule approve on
polycule approve off

# Status check
polycule status
```

Or manually:
```bash
# Start hub
python3 ~/dev/polycule/src/backend/hub.py

# In another pane, start TUI
python3 ~/dev/polycule/src/ui/chat_tui.py

# In another pane, start hermes adapter
python3 ~/dev/polycule/src/agents/hermes_adapter.py --name Wizard
```

---

## DEPENDENCIES

| Package | Purpose | Install |
|---------|---------|---------|
| `urwid` | TUI framework for chat UI | `pip3 install urwid` |
| `sqlite3` | DB (stdlib, no install needed) | built-in |
| `asyncio` | async TCP server (stdlib) | built-in |
| `fzf` | session picker | already installed |

Python 3.10+ required (uses `match` in some places, `asyncio.to_thread`).

---

## HEY INTEGRATION NOTE (Phase 4+)

`~/dev/hey/hey` is the agent-to-agent messaging wrapper. Current state:
- `hey cassette` → calls `~/.hermes/bin/cassette` locally ✓
- `hey wizard` → SSH to opencassette, OpenClaw wizard (NOT a local hermes wizard)

**Needed (not yet done):** Add a local `wizard` hermes profile so `hey wizard "message"` works locally using hermes. Two options:
1. Modify `hey` to detect `--local` flag or current host and route `wizard` to local cassette with wizard persona
2. Create a new hermes profile `wizard-local` that maps to a cassette invocation with wizard system prompt

The Polycule hermes adapter already handles this for hub participants. The `hey` fix is needed for direct one-off messages outside the hub context.

---

## KNOWN ISSUES / TECH DEBT

1. **broadcast bug (pre-fix):** Log at 12:15 shows `Agent not in room` errors. These were from the pre-fix state. The fix (`join_room` in `create_room` handler) is in the code. Needs integration test to confirm resolved.

2. **base_adapter blocking I/O:** Original `_read_output` used blocking `readline()` in an async function. Fixed to use `asyncio.to_thread`.

3. **hardcoded log path:** `hub.py` log path hardcoded to `/Users/maps/dev/polycule/logs/hub.log`. Fine for now, should use `Path(__file__).parent.parent / 'logs'`.

4. **no auth:** Any process on localhost can connect to the hub. Acceptable for local-only use; revisit if networked.

5. **no rate limiting:** Implemented as TODO. Agents can flood the hub.

6. **no tmux command sandboxing:** Structural commands (split_window, kill_pane) execute directly via tmux. Safety is the approval flow, not code sandboxing.

7. **smux/tmux-bridge not yet integrated into adapters:** Adapters currently manage their own pane output. Could use tmux-bridge for more robust cross-pane comms.

8. **Anarchy mode:** Spec'd, not built. See spec.md section 1. Requires frustration detection (error rate per agent) + self-kill/spawn triggers + maps approval.

9. **eidetic logging:** Messages should JSONL-log to `~/dev/eidetic/logs/polycule/`. Not yet wired. Hub has the messages in DB; adding an eidetic export is straightforward.

10. **Garden sync:** spec.md calls for periodic Garden sync. Not implemented.

---

## WHAT NOT TO DO

- Don't rebuild hub.py from scratch. The async TCP core is solid.
- Don't use MongoDB/PostgreSQL. SQLite is correct for this scale.
- Don't add web UI. This is a tmux-native tool.
- Don't add auth/TLS. Local-only, YAGNI.
- Don't make the hub stateful beyond rooms+messages. No agent logic in the hub.
- Don't make hermes_adapter synchronous. Always use `asyncio.to_thread` for subprocess calls.

---

*Build sheet maintained by maps · cassette.help*
*Update the "CURRENT STATE" section after each phase.*
