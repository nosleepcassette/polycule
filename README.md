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
- `uv` is recommended for managed installs, or install `urwid` manually
- `fzf` is optional but improves tmux session selection
- At least one agent CLI you want to run

Polycule does not install agent CLIs for you. It discovers tools already
available on your machine and keeps missing or disabled backends visible as
inactive panes instead of failing the whole workspace.

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
uv sync
export PATH="$HOME/dev/polycule/bin:$PATH"
```

Add that `PATH` line to your shell profile if you want `polycule` available in new terminals.
You can also run commands with `uv run polycule ...`.

## First Run

On first start, Polycule writes a local configuration if none exists. The config
can live at `polycule.toml` in the project root or at
`~/.config/polycule/config.toml`.

Inspect the backend roster Polycule discovered on your machine:

```bash
polycule agent status
```

Then start the workspace:

```bash
polycule start --name "$USER" --room Main
```

For a quick readiness check without attaching to tmux, run:

```bash
polycule status --json
```

That command will:

- create or reuse the `polycule` tmux session, prompting before replacing an existing one
- reconcile the default layout
- start the hub
- start the chat TUI
- start every configured backend pane, with disabled/missing agents showing an inline notice

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
- `~/.hermes/profiles/planner`
- `~/.hermes/profiles/analyst`

the discovered Hermes agents will be:

- `@hermes`
- `@planner`
- `@analyst`

Installed external CLIs are added alongside those Hermes agents when available.

Default response modes:

- the default Hermes profile starts in `always`
- discovered Hermes profiles start in `mention` unless configured otherwise
- external CLIs are listed but disabled until enabled in config or at runtime
- `claude`, `opencode`, and `gemini` start in `mention`

You can change any of that at runtime with `polycule agent mode <agent> <mention|always|handoff|ffa|off>`.

## Overrides

Configuration priority is:

1. `polycule.toml` in the project root
2. `~/.config/polycule/config.toml`
3. environment variables
4. CLI flags

See `polycule.toml.example` for the full schema. Environment variables still work:

- `POLYCULE_HERMES_PROFILES=default,planner,analyst`
  Restrict Hermes discovery to an explicit list.
- `POLYCULE_HERMES_EXCLUDE_PROFILES=old-profile`
  Exclude specific Hermes profiles.
- `POLYCULE_HERMES_DEFAULT_NAME=guide`
  Rename the default Hermes profile from `hermes` to another public-facing agent name.
- `POLYCULE_HERMES_ALWAYS_PROFILES=analyst`
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
- `POLYCULE_GEMINI_STATUS_CMD="python3 ~/scripts/agent-status.py"`

Permission-bypass flags are off by default in the public repo. Only set the
adapter-specific override variables when you intentionally want those CLI
tools to run with broader local permissions.

## Layout

Default tmux windows:

- `polycule`: `human | chat`
- `swarm`: one spare worker pane
- `backend`: `hub-log | <one pane per discovered backend agent>`

If a `polycule` tmux session already exists, `polycule start` prompts to attach,
restart, or quit. Use `--attach`, `--restart`, or `--fresh` to skip the prompt.

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
- `/mode <agent> <mention|always|handoff|ffa|off>`
- `/enable <agent>`
- `/disable <agent>`
- `/summon <all|agent...>`
- `/brief <all|agent...> -- <message>`
- `/standdown <all|agent...>`
- `/watch <agent|all> <off|human|room|@agent>`
- `/rollcall`
- `/theme amber`
- `/pin <message_id|prefix|last>`
- `/unpin <message_id|prefix>`
- `/which <task>`
- `/restart`
- `/restart --full`
- `/quit`

Keyboard shortcuts:

- `Tab` / `Shift-Tab`: slash completion
- `Tab` / `Shift-Tab`: filesystem completion for `~/...` and `/...` paths
- `Up` / `Down`: input history
- `Ctrl-U`: clear the current input line
- `Ctrl-C`: clear the input line, or show a `/quit` hint if empty
- `Ctrl-L`: clear the chat view

## CLI Reference

```bash
polycule start
polycule start --background
polycule start --attach
polycule start --restart
polycule start --fresh
polycule hub
polycule tui --name "$USER"
polycule status
polycule status --json
polycule kill
polycule kill --hub-only
polycule kill --panes-only
polycule agent status
polycule agent modes
polycule agent enable <agent>
polycule agent disable <agent>
polycule agent mode <agent> <mention|always|handoff|ffa|off>
polycule agent hermes --room Main
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
