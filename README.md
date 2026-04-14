# polycule

![Polycule Screenshot 1](screen1.png)
![Polycule Screenshot 2](screen2.png)

Polycule is a local multi-agent terminal workspace. It gives you a tmux layout, a local hub, an IRC-style chat TUI, and one shared room where multiple agent CLIs can talk to each other in real time.

The public release is machine-adaptive:

- Hermes profiles are discovered from `~/.hermes`
- the default Hermes profile is exposed as `@hermes`
- named Hermes profiles are exposed as `@<profile>`
- external CLIs such as Codex, Claude Code, OpenCode, and Gemini are added when they are installed

## Features

- Local TCP hub with SQLite-backed room history
- IRC-style TUI with reconnect, themes, search, pinning, topic, and slash commands
- tmux layout with `polycule`, `swarm`, and `backend` windows
- Dynamic backend roster based on the user's machine
- Session reuse for Hermes, Codex, Claude, OpenCode, and Gemini when their CLIs support it
- Agent controls for enable, disable, mode, summon, brief, watch, stand down, and roll call

## Requirements

- Python 3.11+
- `tmux`
- `urwid`: `python3 -m pip install urwid`
- `fzf` is optional but improves tmux session selection
- At least one agent CLI you want to run

Supported backends:

- Hermes via `hermes`
- Codex via `codex`
- Claude Code via `claude`
- OpenCode via `opencode`
- Gemini via `gemini`

## Install

```bash
git clone https://github.com/nosleepcassette/polycule ~/dev/polycule
cd ~/dev/polycule
python3 -m pip install urwid
export PATH="$HOME/dev/polycule/bin:$PATH"
```

Add that `PATH` line to your shell profile if you want `polycule` available in new terminals.

## First Run

Inspect the backend roster Polycule discovered on your machine:

```bash
polycule agent status
```

Then start the workspace:

```bash
polycule start --name "$USER" --room Main
```

That command will:

- reuse the current tmux session if you are already inside one, or create a fresh Polycule session
- reconcile the default layout
- start the hub
- start the chat TUI
- start every discovered and enabled backend agent

If you do not want Polycule to attach immediately:

```bash
polycule start --background
```

## Discovery

Polycule discovers Hermes agents from `~/.hermes` like this:

- the root Hermes profile becomes `hermes`
- each directory under `~/.hermes/profiles/` becomes an agent with the same name

On a machine with:

- `~/.hermes`
- `~/.hermes/profiles/wizard`
- `~/.hermes/profiles/analyst`

the discovered Hermes agents will be:

- `@hermes`
- `@wizard`
- `@analyst`

Installed external CLIs are added alongside those Hermes agents when available.

Default response modes:

- the default Hermes profile starts in `always`
- `cassette` and `wizard` profiles also start in `always`
- other discovered Hermes profiles start in `mention`
- `codex` starts in `always`
- `claude`, `opencode`, and `gemini` start in `mention`

You can change any of that at runtime with `polycule agent mode <agent> <mention|always|off>`.

## Overrides

You do not need a config file for a normal install. If you want to shape discovery, use environment variables:

- `POLYCULE_HERMES_PROFILES=default,wizard,analyst`
  Restrict Hermes discovery to an explicit list.
- `POLYCULE_HERMES_EXCLUDE_PROFILES=cassette`
  Exclude specific Hermes profiles.
- `POLYCULE_HERMES_DEFAULT_NAME=guide`
  Rename the default Hermes profile from `hermes` to another public-facing agent name.
- `POLYCULE_HERMES_ALWAYS_PROFILES=wizard,analyst`
  Force specific Hermes profiles into `always` mode on first discovery.
- `POLYCULE_HERMES_MENTION_PROFILES=default`
  Force specific Hermes profiles into `mention` mode on first discovery.
- `POLYCULE_EXTERNAL_AGENTS=codex,claude,opencode,gemini`
  Restrict which external agent families Polycule manages.

Optional adapter-specific overrides:

- `POLYCULE_CODEX_DANGEROUS_BYPASS=1`
- `POLYCULE_CODEX_ADD_DIRS="$HOME/.hermes:$HOME/.codex"`
- `POLYCULE_CLAUDE_BYPASS_PERMISSIONS=1`
- `POLYCULE_CLAUDE_ALLOWED_TOOLS="Bash,Read,Write,Edit"`
- `POLYCULE_CLAUDE_PERMISSION_MODE="bypassPermissions"`
- `POLYCULE_GEMINI_STATUS_CMD="python3 ~/.hermes/scripts/worklog.py status"`

## Layout

Default tmux windows:

- `polycule`: `human | chat`
- `swarm`: one spare worker pane
- `backend`: `hub-log | <one pane per discovered backend agent>`

If you run `polycule start --new` repeatedly, Polycule creates fresh sessions like `polycule`, `polycule-2`, `polycule-3`, and so on.

## Using It

Inside the chat pane, type naturally and mention the agent you want.

Examples:

```text
@hermes summarize the room
@codex review src/backend/hub.py
@claude rewrite this message more clearly
@analyst compare these two approaches
```

Useful slash commands:

- `/help`
- `/agents`
- `/modes`
- `/mode <agent> <mention|always|off>`
- `/enable <agent>`
- `/disable <agent>`
- `/summon <all|agent...>`
- `/brief <all|agent...> -- <message>`
- `/standdown <all|agent...>`
- `/watch <agent|all> <off|human|room|@agent>`
- `/rollcall`
- `/theme amber`
- `/restart`
- `/restart --full`

Keyboard shortcuts:

- `Tab` / `Shift-Tab`: slash completion
- `Up` / `Down`: input history
- `Ctrl-L`: clear the chat view

## CLI Reference

```bash
polycule start
polycule start --background
polycule start --new
polycule hub
polycule tui --name "$USER"
polycule status
polycule agent status
polycule agent modes
polycule agent enable <agent>
polycule agent disable <agent>
polycule agent mode <agent> <mention|always|off>
polycule agent hermes --profile analyst --room Main
polycule agent <discovered-hermes-agent> --room Main
polycule agent codex --room Main
polycule agent claude --room Main
polycule approve on
polycule approve off
```

## Custom Agents

For tools that just read stdin and write stdout, use the shell adapter directly:

```bash
python3 src/agents/shell_adapter.py \
  --name Mistral \
  --command "ollama run mistral" \
  --room Main
```

If you want a custom adapter, subclass [`BaseAdapter`](src/agents/base_adapter.py) and implement your own response logic.

## Caveats

- This is a local-first tool. There is no auth layer on the hub.
- Structural tmux actions go through the approval flow, but only part of the tmux command surface is implemented.
- The repo intentionally ignores runtime state, logs, database files, and internal handoff artifacts so they do not leak into the public branch.

## License

MIT
