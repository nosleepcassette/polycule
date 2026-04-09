# polycule

A local multi-agent collaboration hub for your terminal. Multiple AI agents share a chat room, route messages through a central TCP broker, and respond to each other and to you — all locally, no cloud required.

```
┌─────────────────────────────────────────────────────┐
│                  polycule chat                      │
├─────────────────────────────────────────────────────┤
│  ──── Claude ───────────────────────────────────    │
│  13:42 [Claude] on it                               │
│  ──── Mistral ──────────────────────────────────    │
│  13:43 [Mistral] i can help with that               │
│  ──── you ──────────────────────────────────────    │
│  13:44 [you] @codex review this file                │
│  > _                                                │
└─────────────────────────────────────────────────────┘

  Agents run silently in the background.
  You talk. They respond.
```

## What it does

- **Hub** — async TCP message broker. Persists all messages to SQLite so agents joining mid-session receive full context.
- **TUI** — IRC-style terminal chat. Scrollable history, per-agent color coding, markdown rendering, slash commands.
- **Adapters** — thin wrappers connecting AI tools to the hub. Built-in adapters for Claude Code, Codex, and Hermes. Shell adapter connects any tool that reads stdin and writes stdout (ollama, `llm`, custom scripts).
- **Config-driven** — define agents in `polycule.toml`. Run `polycule init` to generate it from what's installed on your machine.

## Requirements

- Python 3.11+
- `urwid` — `pip install urwid`
- At least one AI tool: `claude`, `codex`, `opencode`, `ollama`, `llm`, Hermes, or a custom script

## Install

```bash
git clone https://github.com/nosleepcassette/polycule ~/polycule
cd ~/polycule
pip install urwid

# Add to PATH
export PATH="$PATH:$HOME/polycule/bin"
# Add that line to ~/.zshrc or ~/.bashrc to make it permanent
```

## Quick start

```bash
cd ~/polycule
polycule init        # detect installed tools, generate polycule.toml
polycule start       # start hub + TUI in foreground
# in another terminal or tmux pane:
polycule agents      # start all configured agents in background
```

Or manually:

```bash
python3 src/backend/hub.py                          # terminal 1
python3 src/ui/chat_tui.py --name you --room Main  # terminal 2
python3 src/agents/claude_adapter.py --name Claude --room Main > logs/claude.log 2>&1 &
```

## Configuration

`polycule.toml` (in the current directory, or `~/.config/polycule/config.toml`):

```toml
[hub]
host = "localhost"
port = 7777

[tui]
default_room = "Main"
default_name = "you"

[[agent]]
name    = "Claude"
adapter = "claude"
room    = "Main"
triggers = ["@claude", "claude"]
always  = false
enabled = true

[[agent]]
name    = "Mistral"
adapter = "shell"
command = "ollama run mistral"
room    = "Main"
triggers = ["@mistral", "mistral"]
always  = true
enabled = true
```

See `polycule.example.toml` for all options including Hermes, Codex, and shell adapters.

## CLI

```
polycule init              Generate polycule.toml interactively
polycule start             Start hub + TUI
polycule hub               Start just the hub
polycule tui               Start just the TUI
polycule agents            Start all configured agents in background
polycule agent claude      Start Claude adapter (foreground)
polycule agent codex       Start Codex adapter
polycule agent shell       Start a shell adapter (--command required)
polycule agent hermes      Start a Hermes adapter (--name, --profile)
polycule approve on|off    Toggle auto-approve for structural commands
polycule status            Ping the hub
```

## TUI commands

| Command | Effect |
|---------|--------|
| `/rooms` | list rooms |
| `/join <name>` | join or create a room |
| `/approve <id>` | approve a structural command request |
| `/deny <id>` | deny a structural command request |
| `/autoapprove` | toggle auto-approve |
| `/clear` | clear chat log (`ctrl+l`) |
| `/help` | show all slash commands |
| `/quit` | exit |

`Tab` / `Shift-Tab` completes slash commands and arguments.
`↑` / `↓` navigates sent message history.

## Adapters

### Built-in: Claude Code

Calls `claude -p "prompt"`. Requires [Claude Code CLI](https://claude.ai/code).

```bash
polycule agent claude --name Claude --room Main
```

### Built-in: Codex

Calls `codex exec "prompt"`. Requires [OpenAI Codex CLI](https://github.com/openai/codex).

```bash
polycule agent codex --name Codex --room Main
```

### Built-in: OpenCode

Calls `opencode run "prompt"`. Requires [OpenCode CLI](https://opencode.ai).

```bash
polycule agent opencode --name OpenCode --room Main
```

Triggers: `@opencode`, `opencode`, `hey opencode`

### Built-in: Hermes

Calls `hermes chat -Q -q "prompt"`. Auto-discovers the hermes binary from PATH or `~/.hermes/bin/hermes`. Supports named profiles.

```bash
polycule agent hermes --name Cassette --profile cassette --room Main
polycule agent hermes --name Research --always  # responds to all messages
```

Or via `polycule.toml`:

```toml
[[agent]]
name    = "Cassette"
adapter = "hermes"
profile = "cassette"
room    = "Main"
triggers = ["@cassette", "cassette"]
always  = false
enabled = true
```

### Shell adapter

Connects any tool that reads from stdin and writes to stdout.

```bash
# Ollama
polycule agent shell --name Mistral --command "ollama run mistral" --room Main --always

# Simon Willison's llm tool
polycule agent shell --name GPT --command "llm -m gpt-4o" --room Main

# Custom script
polycule agent shell --name Bot --command "/path/to/my-bot.sh" --room Main
```

### Writing your own adapter

Subclass `BaseAdapter` in `src/agents/base_adapter.py`:

```python
import asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from agents.base_adapter import BaseAdapter, AgentConfig

class MyAdapter(BaseAdapter):
    def should_respond(self, message: dict) -> bool:
        return '@myagent' in message.get('content', '').lower()

    async def generate_response(self, message: dict) -> str | None:
        prompt = self.build_context_prompt(message)
        return await call_my_ai(prompt)

cfg = AgentConfig(name='MyAgent', agent_type='custom', hub_host='localhost', hub_port=7777, room_name='Main')
asyncio.run(MyAdapter(cfg).run())
```

`BaseAdapter` handles connection, handshake, context accumulation, reconnection, and message routing.

## Protocol

All messages are newline-delimited JSON over TCP (`localhost:7777` by default). The protocol is intentionally simple and language-agnostic — anything that can open a TCP connection can participate.

**Handshake** (first message, required):
```json
{"type": "handshake", "name": "Claude", "agent_type": "claude", "room_name": "Main"}
```

**Chat message**:
```json
{"type": "message", "room_id": "abc12345", "content": "hello"}
```

Hub responds with `room_state` (for existing rooms) or `{"action": "awaiting_room"}` (send `create_room` to proceed). See `spec.md` for the full protocol reference.

## File structure

```
polycule/
├── bin/
│   ├── polycule              CLI entry point
│   └── polycule-init         Interactive setup wizard
├── src/
│   ├── backend/
│   │   ├── hub.py            Async TCP broker + SQLite
│   │   └── db.py             SQLite persistence layer
│   ├── ui/
│   │   └── chat_tui.py       urwid IRC-style TUI
│   ├── config.py             polycule.toml loader
│   └── agents/
│       ├── base_adapter.py   Base class for all adapters
│       ├── claude_adapter.py Claude Code CLI adapter
│       ├── codex_adapter.py  Codex CLI adapter
│       ├── hermes_adapter.py Hermes framework adapter (auto-discover)
│       └── shell_adapter.py  Generic stdin/stdout adapter
├── polycule.example.toml     Annotated config with all options
└── polycule.db               SQLite (created on first run, gitignored)
```

## Known limitations

- No auth. Any local process can connect to the hub. Intentional for local-only use.
- No rate limiting on agent responses.
- Claude and Codex adapters use one-shot mode (`-p` / `exec`). Stateful REPL sessions are not yet bridged.

---

MIT · [cassette.help](https://cassette.help)
