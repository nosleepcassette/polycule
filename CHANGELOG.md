# Changelog

## 2026-04-09

### Added

- Phase-1 agency/watch foundation for structured room coordination.
- Persistent watch registry in `runtime_state`.
- Persistent temporary summon-activation registry in `runtime_state`.
- Hub support for:
  - `set_watch`
  - `summon_agents`
  - `send_directive`
  - `ack_directive`
  - `standdown_agents`
- TUI commands:
  - `/summon`
  - `/brief`
  - `/watch`
  - `/standdown`
- Room/system event rendering for directives, watch changes, summon/standdown notices, and directive acks.
- Adapter directive handling so targeted agents can respond to structured briefs without manual mention syntax.

### Changed

- Human-trigger policy can now be widened by phase-1 watch state (`maps` / `room`) without enabling full peer chatter.
- Room orchestration is now able to temporarily activate disabled agents for a summon/brief workflow.
- Temporary summon activation now restores original `mode` and disabled/enabled state on `/standdown`.

### Deferred

- Peer-reactive watch behavior
- pane activity monitoring
- mood / agency presets
- worker spawning and tmux interference capabilities
