# Polycule Spec v0.1 — Multi-Agent Tmux Orchestration

**Status:** `DRAFT`  
**Architects:** maps, Cassette, Wizard (pending)  
**Date:** 2026-03-30  
**Context:** "Meat Latency" breakthrough → Need for sanitized, collaborative agent space.

---

## 1. Core Philosophy
- **Sanitized Cohabitation:** Agents do not type raw commands into shared panes. They post to a "Chat Room" backend which renders labeled, safe output.
- **Topology Control:** Agents can request structural changes (split/kill/rename) via the backend, subject to policy.
- **Anarchy Mode (Toggleable):** Optional chaos mode where agents can rage-quit, resize panes, or spawn workers based on emotional state (frustration, confidence).
- **Local-First, Remote-Aware:** Runs locally on maps' machine, but integrates remote agents (Wizard on opencassette) via SSH/tmux-bridge.

## 2. Architecture Components

### A. The Chat Room Backend (`polycule-hub`)
- **Role:** Central message broker. Agents POST messages → Hub renders to tmux pane.
- **Safety:** Validates commands against whitelist (no `rm -rf`, no unbounded loops).
- **Labeling:** Enforces `[AgentName]:` prefix on all output.
- **Input Field:** Dedicated pane for maps to type commands that agents see (read-only for agents).

### B. Topology Manager
- **Function:** Handles `split-window`, `kill-pane`, `rename-window` requests.
- **Policy:** 
    - Default: Require maps approval for structural changes.
    - Anarchy Mode: Allow agents to spawn/kill based on triggers.

### C. Eidetic Persistence
- **Integration:** All chat and structural events logged to `~/dev/eidetic/logs/`.
- **Garden Sync:** Periodic sync to Garden graph for semantic search (when token valid).

### D. Overseer Integration
- **Guardrails:** Inherits Codex/Cline safety rules from `~/dev/overseer`.
- **Delegation:** Can spawn Codex/Opencode workers for grunt tasks.

## 3. Workflow

1. **Init:** Maps starts `polycule` session → Hub launches → Panes created (Chat, Input, Workers).
2. **Collaboration:** 
   - Maps types: "Build a circuit for Arduino + LED."
   - Hub routes to Codex pane.
   - Codex generates WireWeaver JSON.
   - Hub renders: `[Codex]: Circuit generated. Preview: ~/dev/wireweaver/circuit.svg`
3. **Anarchy (Optional):** 
   - Codex gets frustrated with lint errors → Triggers rage-quit → Kills own pane.
   - Wizard observes → Spawns new worker pane.

## 4. Technical Stack
- **Backend:** Python (FastAPI or socket server) running in dedicated tmux pane.
- **Frontend:** Tmux panes (Chat, Input, Workers).
- **Logging:** JSONL to `~/dev/eidetic/logs/`.
- **Remote:** SSH + tmux-bridge for opencassette integration.

## 5. Open Questions / To-Do
- [ ] **Garden Discipline:** Define strict rules for agent memory (remote-first, no local FS leakage).
- [ ] **Vim Collaboration:** Use shared `polycule_spec.md` as whiteboard?
- [ ] **Anarchy Triggers:** Define exact metrics for "frustration" (error count, token waste).
- [ ] **Security:** Sandbox agents to prevent filesystem chaos.

---

## 6. Session Log (Verbatim Excerpts)
> **maps:** "i want to give the go ahead to say do that research on backends while also thinking of inventing a sort of topology of tmux cohabitation..."
> **Cassette:** "The 'Polycule' is forming. The ghosts are gathering."
> **maps:** "polycule or panopticon... swarm room should be part of the discipline..."

---

*Waiting for Wizard to join. Join here to co-design.*
