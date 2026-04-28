# Polycule · MIT
"""
Managed backend agent discovery for public Polycule installs.

The public release should adapt to the user's machine instead of assuming a
fixed local profile roster. This module discovers Hermes profiles under
~/.hermes and combines them with any installed external agent CLIs.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import os
import re
import shutil
from pathlib import Path

from config_loader import (
    DEFAULT_EXTERNAL_AGENTS,
    DEFAULT_HERMES_PROFILES,
    load_config,
)

_PROJECT_DIR = Path(__file__).resolve().parents[1]
_CONFIG = load_config(_PROJECT_DIR)

HERMES_HOME = Path(_CONFIG.hermes.home).expanduser() if _CONFIG.hermes.home else Path.home() / ".hermes"
HERMES_BIN = HERMES_HOME / "bin" / "hermes"
_SUPPORTED_EXTERNAL_AGENTS = ("codex", "claude", "opencode", "gemini")


@dataclass(frozen=True)
class ManagedAgent:
    name: str
    display_name: str
    adapter: str
    profile: str = ""
    default_mode: str = "mention"
    billing: str = "local"
    summary: str = ""
    keywords: tuple[tuple[str, int], ...] = ()


def _env_truthy(name: str) -> bool:
    value = os.environ.get(name, "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _csv_env(name: str) -> list[str]:
    raw = os.environ.get(name, "")
    if not raw.strip():
        return []
    items: list[str] = []
    for part in raw.split(","):
        value = part.strip()
        if value:
            items.append(value)
    return items


def _slug(value: str) -> str:
    lowered = re.sub(r"[^a-z0-9_-]+", "-", str(value or "").strip().lower())
    return lowered.strip("-_") or "agent"


def _display_name(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return "Agent"
    return text.replace("-", " ").replace("_", " ").title().replace(" ", "-")


def _normalize_profile_selector(value: str) -> str:
    slug = _slug(value)
    if slug in {"", "default", "hermes"}:
        return "default"
    return slug


def hermes_available() -> tuple[bool, str]:
    if HERMES_BIN.exists():
        return True, str(HERMES_BIN)
    found = shutil.which("hermes")
    if found:
        return True, found
    return False, "requires Hermes CLI (`hermes`)"


def external_agent_available(agent_name: str) -> tuple[bool, str]:
    agent = _slug(agent_name)
    if agent == "codex":
        codex_bin = Path("/usr/local/bin/codex")
        if codex_bin.exists():
            return True, str(codex_bin)
        found = shutil.which("codex")
        if found:
            return True, found
        return False, "requires Codex CLI (`codex`)"
    found = shutil.which(agent)
    if found:
        return True, found
    return False, f"requires {agent} CLI (`{agent}`)"


def discover_hermes_profiles() -> list[str]:
    explicit = [_normalize_profile_selector(item) for item in _csv_env("POLYCULE_HERMES_PROFILES")]
    if not explicit and _CONFIG.hermes.profiles:
        explicit = [_normalize_profile_selector(item) for item in _CONFIG.hermes.profiles]
    if explicit:
        profiles = explicit
    else:
        profiles: list[str] = []
        if HERMES_HOME.exists() or "hermes" in _CONFIG.agents:
            profiles.append("default")
        profile_root = HERMES_HOME / "profiles"
        if profile_root.exists():
            for path in sorted(profile_root.iterdir(), key=lambda p: p.name.lower()):
                if path.is_dir() and not path.name.startswith("."):
                    profiles.append(_slug(path.name))

    excluded = {
        _normalize_profile_selector(item)
        for item in _csv_env("POLYCULE_HERMES_EXCLUDE_PROFILES")
    }

    out: list[str] = []
    seen: set[str] = set()
    for profile in profiles:
        normalized = _normalize_profile_selector(profile)
        if normalized in excluded or normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out


def default_hermes_agent_name() -> str:
    configured = _slug(os.environ.get("POLYCULE_HERMES_DEFAULT_NAME", "hermes"))
    return configured or "hermes"


def _hermes_default_mode(profile: str) -> str:
    always = {
        _normalize_profile_selector(item)
        for item in _csv_env("POLYCULE_HERMES_ALWAYS_PROFILES")
    }
    if not always:
        always = {
            _normalize_profile_selector(item)
            for item in _CONFIG.hermes.always_profiles
        }
    mention = {
        _normalize_profile_selector(item)
        for item in _csv_env("POLYCULE_HERMES_MENTION_PROFILES")
    }
    normalized = _normalize_profile_selector(profile)
    if normalized in always:
        return "always"
    if normalized in mention:
        return "mention"
    if normalized == "default":
        return "always"
    return "mention"


def _hermes_hint(profile: str, agent_name: str) -> tuple[str, tuple[tuple[str, int], ...]]:
    lowered = f"{profile} {agent_name}".lower()
    if any(token in lowered for token in ("plan", "planner", "research", "docs", "writer")):
        return (
            f"Hermes profile '{profile}' for planning, docs, and synthesis",
            (
                ("plan", 5),
                ("spec", 4),
                ("design", 4),
                ("research", 4),
                ("docs", 4),
                ("review", 3),
                ("summary", 3),
            ),
        )
    if any(token in lowered for token in ("ops", "shell", "tmux", "terminal", "operator", "infra")):
        return (
            f"Hermes profile '{profile}' for shell, tmux, and operations",
            (
                ("tmux", 5),
                ("shell", 5),
                ("terminal", 4),
                ("pane", 4),
                ("window", 4),
                ("layout", 4),
                ("ops", 3),
                ("session", 3),
            ),
        )
    if profile == "default":
        return (
            "General-purpose Hermes profile from ~/.hermes",
            (
                ("general", 1),
                ("analysis", 2),
                ("task", 1),
                ("help", 1),
            ),
        )
    return (
        f"Hermes profile '{profile}' discovered from ~/.hermes",
        (
            ("general", 1),
            ("task", 1),
        ),
    )


def _fixed_external_agents() -> list[ManagedAgent]:
    supported = set(_SUPPORTED_EXTERNAL_AGENTS)
    explicit = {_slug(item) for item in _csv_env("POLYCULE_EXTERNAL_AGENTS")}
    if not explicit and _CONFIG.external.agents:
        explicit = {_slug(item) for item in _CONFIG.external.agents}
    if explicit:
        supported &= explicit

    agents: list[ManagedAgent] = []
    for name in _SUPPORTED_EXTERNAL_AGENTS:
        if name not in supported:
            continue
        available, _detail = external_agent_available(name)
        configured = name in _CONFIG.agents or name in DEFAULT_EXTERNAL_AGENTS
        if not available and not configured and not _env_truthy("POLYCULE_INCLUDE_UNAVAILABLE_EXTERNALS"):
            continue
        if name == "codex":
            agents.append(
                ManagedAgent(
                    name="codex",
                    display_name="Codex",
                    adapter="codex",
                    default_mode="always",
                    billing="paid",
                    summary="strongest implementation path for code changes and tests",
                    keywords=(
                        ("code", 5),
                        ("implement", 5),
                        ("patch", 5),
                        ("fix", 4),
                        ("bug", 4),
                        ("debug", 4),
                        ("refactor", 4),
                        ("test", 4),
                        ("python", 4),
                        ("typescript", 4),
                    ),
                )
            )
        elif name == "claude":
            agents.append(
                ManagedAgent(
                    name="claude",
                    display_name="Claude",
                    adapter="claude",
                    default_mode="mention",
                    billing="paid",
                    summary="best for reviews, writing, architecture, and synthesis",
                    keywords=(
                        ("review", 5),
                        ("docs", 4),
                        ("write", 4),
                        ("architecture", 4),
                        ("explain", 4),
                        ("analysis", 4),
                        ("plan", 3),
                        ("design", 3),
                    ),
                )
            )
        elif name == "opencode":
            agents.append(
                ManagedAgent(
                    name="opencode",
                    display_name="OpenCode",
                    adapter="opencode",
                    default_mode="mention",
                    billing="local",
                    summary="coding backup path when you want another code-focused agent",
                    keywords=(
                        ("code", 4),
                        ("implement", 4),
                        ("patch", 4),
                        ("fix", 3),
                        ("debug", 3),
                        ("test", 3),
                    ),
                )
            )
        elif name == "gemini":
            agents.append(
                ManagedAgent(
                    name="gemini",
                    display_name="Gemini",
                    adapter="gemini",
                    default_mode="mention",
                    billing="paid",
                    summary="broad reasoning and research/synthesis backup with Gemini CLI",
                    keywords=(
                        ("research", 4),
                        ("analysis", 4),
                        ("explain", 4),
                        ("docs", 3),
                        ("synthesis", 3),
                        ("plan", 3),
                        ("design", 3),
                        ("review", 3),
                    ),
                )
            )
    return agents


def get_managed_agents() -> list[ManagedAgent]:
    agents: list[ManagedAgent] = []
    hermes_ok, _detail = hermes_available()
    configured_hermes = any(
        name not in _SUPPORTED_EXTERNAL_AGENTS for name in _CONFIG.agents
    )
    if hermes_ok or configured_hermes or _env_truthy("POLYCULE_INCLUDE_UNAVAILABLE_HERMES"):
        profiles = discover_hermes_profiles()
        used_names: set[str] = set()
        for profile in profiles:
            if profile == "default":
                base_name = default_hermes_agent_name()
                display_name = _display_name(os.environ.get("POLYCULE_HERMES_DEFAULT_NAME", "Hermes"))
            else:
                base_name = _slug(profile)
                display_name = _display_name(profile)

            name = base_name
            if name in used_names:
                suffix = "default" if profile == "default" else _slug(profile)
                name = _slug(f"{base_name}-{suffix}")
            used_names.add(name)

            summary, keywords = _hermes_hint(profile, name)
            agents.append(
                ManagedAgent(
                    name=name,
                    display_name=display_name,
                    adapter="hermes",
                    profile=profile,
                    default_mode=_hermes_default_mode(profile),
                    billing="local",
                    summary=summary,
                    keywords=keywords,
                )
            )

    agents.extend(_fixed_external_agents())

    known = {agent.name for agent in agents}
    for name, cfg in _CONFIG.agents.items():
        if name in known or name in _SUPPORTED_EXTERNAL_AGENTS:
            continue
        summary, keywords = _hermes_hint(name, name)
        agents.append(
            ManagedAgent(
                name=name,
                display_name=cfg.alias or _display_name(name),
                adapter="hermes",
                profile="default" if name == "hermes" else name,
                default_mode=cfg.mode,
                billing="local",
                summary=summary,
                keywords=keywords,
            )
        )

    configured_order = {name: index for index, name in enumerate(_CONFIG.agent_names)}
    configured_agents: list[ManagedAgent] = []
    for agent in agents:
        cfg = _CONFIG.agents.get(agent.name)
        if cfg:
            agent = replace(
                agent,
                display_name=cfg.alias or agent.display_name,
                default_mode=cfg.mode or agent.default_mode,
            )
        configured_agents.append(agent)

    configured_agents.sort(
        key=lambda agent: (
            configured_order.get(agent.name, len(configured_order)),
            agent.name,
        )
    )
    return configured_agents


def get_managed_agent_names() -> list[str]:
    return [agent.name for agent in get_managed_agents()]


def get_managed_agent_lookup() -> dict[str, ManagedAgent]:
    return {agent.name: agent for agent in get_managed_agents()}


def get_default_backend_agent_modes() -> dict[str, str]:
    return {agent.name: agent.default_mode for agent in get_managed_agents()}


def get_free_agent_names() -> list[str]:
    return [agent.name for agent in get_managed_agents() if agent.billing != "paid"]


def get_paid_agent_names() -> list[str]:
    return [agent.name for agent in get_managed_agents() if agent.billing == "paid"]


def get_agent_capability_hints() -> dict[str, dict[str, object]]:
    hints: dict[str, dict[str, object]] = {}
    for agent in get_managed_agents():
        hints[agent.name] = {
            "summary": agent.summary,
            "keywords": {key: weight for key, weight in agent.keywords},
        }
    return hints
