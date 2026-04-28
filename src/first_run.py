# Polycule · MIT
"""First-run configuration picker for Polycule."""

from __future__ import annotations

import shutil
from pathlib import Path

from config_loader import global_config_path, write_default_config


ADAPTERS = (
    ("hermes", "Hermes default/local profile", "hermes"),
    ("codex", "Codex CLI coding agent", "codex"),
    ("claude", "Claude CLI agent", "claude"),
    ("opencode", "OpenCode CLI coding agent", "opencode"),
    ("gemini", "Gemini CLI agent", "gemini"),
)


def _yes_no(raw: str, default: bool) -> bool:
    value = raw.strip().lower()
    if not value:
        return default
    return value in {"y", "yes", "1", "true", "on"}


def run_first_run_picker(path: Path | None = None) -> Path:
    """Run a small terminal picker and write the selected config."""
    target = path or global_config_path()
    print("\nPolycule first-run setup")
    print("polycule wraps existing standalone CLI tools. It does not install them.")
    print("Enable only adapters whose commands are installed and callable.\n")

    name = input("Display name [$USER]: ").strip()
    room = input("Room [Default]: ").strip() or "Default"
    raw_port = input("Hub port [7777]: ").strip()
    try:
        port = int(raw_port) if raw_port else 7777
    except ValueError:
        port = 7777

    enabled: dict[str, bool] = {}
    for agent, desc, command in ADAPTERS:
        found = shutil.which(command) is not None or command == "hermes"
        default = agent == "hermes"
        marker = "found" if found else "not found"
        raw = input(f"Enable {agent} ({desc}; requires `{command}`: {marker}) [{'Y/n' if default else 'y/N'}]: ")
        enabled[agent] = _yes_no(raw, default)

    target = write_default_config(target)
    text = target.read_text(encoding="utf-8")
    if name:
        text = text.replace('name = "you"', f'name = "{name}"')
    text = text.replace('room = "Default"', f'room = "{room}"', 1)
    text = text.replace("port = 7777", f"port = {port}", 1)
    for agent, value in enabled.items():
        needle = f"[agents.{agent}]\nenabled = "
        start = text.find(needle)
        if start == -1:
            continue
        value_start = start + len(needle)
        value_end = text.find("\n", value_start)
        text = text[:value_start] + ("true" if value else "false") + text[value_end:]
    target.write_text(text, encoding="utf-8")
    print(f"\nWrote {target}")
    return target
