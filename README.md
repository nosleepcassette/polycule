# polycule
![Polycule Screenshot 1](polycule.png)
![Polycule Screenshot 2](screen1.png)
![Polycule Screenshot 3](screen2.png)

Polycule is a local multi-agent terminal workspace. It gives you a tmux layout,
a local TCP hub, an IRC-style chat TUI, and one shared room where multiple agent
CLIs can talk to each other in real time.

The public release is machine-adaptive:

- Hermes profiles are discovered from `~/.hermes`
- the default Hermes profile is exposed as `@hermes`
- named Hermes profiles are exposed as `@<profile>`
- Codex, Claude Code, OpenCode, and Gemini are added when their CLIs are installed
- missing or disabled agents still get panes with clear status text

## Highlights

- Local TCP hub with SQLite-backed room history
- tmux workspace with `polycule`, `swarm`, and `backend` windows
- Dynamic backend roster based on local config and installed CLIs
- First-run config writer plus `polycule.toml` / global config support
- Health/status/kill commands for hub, panes, and configured agents
- Session reuse for Hermes, Codex, Claude, OpenCode, and Gemini where supported
- Runtime agent controls: enable, disable, modes, summon, brief, watch, stand down
- TUI features: search, topics, pins, aliases, themes, reconnect, and slash commands
- Keyboard UX: history, clear-line, slash completion, and filesystem path completion
- Cost-aware routing helpers: `/which`, `/free`, and free/paid agent tags

## Requirements

- Python 3.11+
- `tmux`
- `uv` is recommended for managed installs, or install `urwid` manually
- `fzf` is optional but improves tmux session selection
- At least one agent CLI you want to run

Polycule wraps existing standalone CLI tools. It does not install agent CLIs for
you. Each enabled backend must already be installed and runnable as its own
command.

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

Add that `PATH` line to your shell profile if you want `polycule` available in
new terminals. You can also run commands with `uv run polycule ...`.

## Quick Start

Inspect the backend roster Polycule discovered on your machine:

```bash
polycule agent status
```

Start the workspace:

```bash
polycule start --name "$USER" --room Main
```

For a quick readiness check without attaching to tmux:

```bash
polycule status --json
```

`polycule start` will:

- create a default config if no config exists
- create or reuse the `polycule` tmux session
- prompt before replacing an existing session
- reconcile the default tmux layout
- start the hub and wait for readiness
- start the chat TUI
- start every configured backend pane
- show inline notices for disabled, off-mode, or missing agents

Run in the background without attaching:

```bash
polycule start --background
```

If a `polycule` tmux session already exists, use:

```bash
polycule start --attach
polycule start --restart
polycule start --fresh
```

## Configuration

On first start, Polycule writes a local configuration if none exists. Config can
live in either place:

- `polycule.toml` in the project root for project-local settings
- `~/.config/polycule/config.toml` for machine-global defaults

Configuration priority is:

1. `polycule.toml` in the project root
2. `~/.config/polycule/config.toml`
3. environment variables
4. CLI flags

See [`polycule.toml.example`](polycule.toml.example) for the full schema.

Minimal shape:

```toml
[operator]
name = "you"
room = "Default"

[hub]
host = "localhost"
port = 7777
hub_timeout = 10.0

[theme]
name = "amber"

[agents.hermes]
enabled = true
mode = "always"
alias = "Hermes"

[agents.codex]
enabled = false
mode = "mention"
alias = "Codex"

[autocomplete]
max_file_candidates = 60
show_hidden = true
```

Useful environment overrides:

- `POLYCULE_HERMES_PROFILES=default,planner,analyst`
  Restrict Hermes discovery to an explicit list.
- `POLYCULE_HERMES_EXCLUDE_PROFILES=old-profile`
  Exclude specific Hermes profiles.
- `POLYCULE_HERMES_DEFAULT_NAME=guide`
  Rename the default Hermes profile from `hermes`.
- `POLYCULE_HERMES_ALWAYS_PROFILES=analyst`
  Put specific Hermes profiles in `always` mode by default.
- `POLYCULE_HERMES_MENTION_PROFILES=default`
  Put specific Hermes profiles in `mention` mode by default.
- `POLYCULE_EXTERNAL_AGENTS=codex,claude,opencode,gemini`
  Restrict which external agent families Polycule manages.

Adapter-specific overrides:

- `POLYCULE_CODEX_DANGEROUS_BYPASS=1`
- `POLYCULE_CODEX_ADD_DIRS="$HOME/.hermes:$HOME/.codex"`
- `POLYCULE_CLAUDE_BYPASS_PERMISSIONS=1`
- `POLYCULE_CLAUDE_ALLOWED_TOOLS="Bash,Read,Write,Edit"`
- `POLYCULE_CLAUDE_PERMISSION_MODE="bypassPermissions"`
- `POLYCULE_GEMINI_STATUS_CMD="python3 ~/scripts/agent-status.py"`

Permission-bypass flags are off by default in the public repo. Only set the
adapter-specific override variables when you intentionally want those CLI tools
to run with broader local permissions.

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
External agents are listed in public config but disabled by default until you
enable them in config or at runtime.

## Agent Skills

Polycule keeps lightweight capability hints for managed agents. The TUI uses
those hints for `/which <task>` and labels choices as `[free]` or `[paid]`.

Built-in routing hints:

- Hermes: local, general-purpose agent from `~/.hermes`
- Hermes profiles with names like `planner`, `research`, `docs`, or `writer`:
  planning, docs, review, synthesis
- Hermes profiles with names like `ops`, `shell`, `tmux`, or `infra`:
  terminal, pane, session, operations
- Codex: code implementation, patches, debugging, refactors, tests `[paid]`
- Claude: review, writing, architecture, explanation, synthesis `[paid]`
- OpenCode: local code-focused backup path
- Gemini: research, analysis, explanation, broad synthesis `[paid]`

Inside the TUI:

```text
/which refactor the hub shutdown path
/free
```

`/which` scores enabled agents against the task. `/free` toggles free mode, which
keeps local/non-premium agents active and disables paid agents until you toggle it
off.

## Response Modes

Each agent has a runtime response mode:

- `off`: keep the pane visible but do not run the adapter
- `mention`: respond to explicit mentions such as `@codex`
- `always`: respond to human messages in the room
- `handoff`: allow agent-to-agent plaintext handoffs in addition to mentions
- `ffa`: free-for-all mode; respond to all messages, useful but easy to overdo

Change modes from the shell:

```bash
polycule agent mode codex mention
polycule agent enable codex
polycule agent disable claude
```

Or from the chat TUI:

```text
/mode codex mention
/enable codex
/disable claude
```

## Layout

Default tmux windows:

- `polycule`: `human | chat`
- `swarm`: one spare worker pane
- `backend`: `hub-log | <one pane per discovered backend agent>`

Backend panes are dynamic. If your config exposes `hermes`, `codex`, `claude`,
`opencode`, `gemini`, and a `planner` Hermes profile, each gets its own backend
pane. Disabled or missing agents are deliberately visible so the layout explains
what is and is not running.

## TUI Commands

Inside the chat pane, type naturally and mention the agent you want:

```text
@hermes summarize the room
@codex review src/backend/hub.py
@claude rewrite this message more clearly
@analyst compare these two approaches
```

Room and navigation:

- `/help`
- `/room <name>`
- `/rooms`
- `/join <name>`
- `/topic [text]`
- `/search <query>`
- `/clear`
- `/quit`

Agent controls:

- `/agents`
- `/modes`
- `/mode <agent> <mention|always|handoff|ffa|off>`
- `/enable <agent>`
- `/disable <agent>`
- `/summon <all|agent...>`
- `/brief <all|agent...> -- <message>`
- `/standdown <all|agent...>`
- `/watch <agent|all> <off|human|room|@agent>`
- `/cancel`, `/detrigger`, `/stop`
- `/rollcall`

Routing and cost controls:

- `/which <task>`
- `/free`
- `/free-mode`

Memory and display:

- `/pin <message_id|prefix|last>`
- `/unpin <message_id|prefix>`
- `/pins`
- `/rename me <name>`
- `/rename <agent> <name>`
- `/theme <default|amber|matrix|monokai>`
- `/themes`

Runtime controls:

- `/restart`
- `/restart --full`
- `/restart --full --now`
- `/restart hub`
- `/quit --now`

Keyboard shortcuts:

- `Tab` / `Shift-Tab`: slash completion
- `Tab` / `Shift-Tab`: filesystem completion for `~/...` and `/...` paths
- `Up` / `Down`: input history
- `Ctrl-U`: clear the current input line
- `Ctrl-C`: clear the input line, or show a `/quit` hint if empty
- `Ctrl-L`: clear the chat view

Persisted room state includes history, last room, topic, pins, display aliases,
theme choice, response modes, watch state, and adapter session IDs.

## CLI Reference

Launch:

```bash
polycule start
polycule start --background
polycule start --attach
polycule start --restart
polycule start --fresh
polycule hub
polycule tui --name "$USER"
```

Inspect and stop:

```bash
polycule status
polycule status --json
polycule kill
polycule kill --now
polycule kill --hub-only
polycule kill --panes-only
```

Manage agents:

```bash
polycule agent status
polycule agent modes
polycule agent enable <agent>
polycule agent disable <agent>
polycule agent mode <agent> <mention|always|handoff|ffa|off>
polycule agent hermes --room Main
polycule agent <discovered-hermes-agent> --room Main
polycule agent codex --room Main
polycule agent claude --room Main
polycule agent opencode --room Main
polycule agent gemini --room Main
```

Approval mode:

```bash
polycule approve on
polycule approve off
```

## Custom Agents

For tools that read stdin and write stdout, use the shell adapter directly:

```bash
python3 src/agents/shell_adapter.py \
  --name Mistral \
  --command "ollama run mistral" \
  --room Main
```

If you want a custom adapter, subclass [`BaseAdapter`](src/agents/base_adapter.py)
and implement your own response logic. Existing adapters all share the same hub
protocol, reconnect handling, directive support, queueing, and mode/watch
behavior.

## Development

```bash
uv sync
uv run pytest -q
uv run polycule status --json
```

The package exposes a `polycule` script through `pyproject.toml`, while
`bin/polycule` remains available for direct checkout-based use.

## Caveats

- This is a local-first tool. There is no auth layer on the hub.
- The hub listens on localhost by default; keep it that way unless you know why
  you are exposing it.
- Structural tmux actions go through the approval flow, but only part of the tmux
  command surface is implemented.
- `ffa` and permission bypass settings are intentionally sharp tools.
- The repo ignores runtime state, logs, database files, local config, and
  handoff artifacts so they do not leak into the public branch.

## License

MIT
