# maps · cassette.help · MIT
"""
Polycule config loader.

Reads polycule.toml from (first match wins):
  1. $POLYCULE_CONFIG env var
  2. ./polycule.toml (current directory)
  3. ~/.config/polycule/config.toml
  4. Built-in defaults (hub + no agents)

Requires Python 3.11+ (tomllib stdlib) or `pip install tomli` for 3.10.
"""
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib  # type: ignore
    except ImportError:
        tomllib = None  # type: ignore


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class HubConfig:
    host: str = "localhost"
    port: int = 7777


@dataclass
class TUIConfig:
    default_room: str = "Main"
    default_name: str = "you"


@dataclass
class AgentConfig:
    name: str
    adapter: str                        # "claude", "codex", "hermes", "shell"
    room: str = "Main"
    triggers: List[str] = field(default_factory=list)
    always: bool = False
    enabled: bool = True
    command: Optional[str] = None       # shell adapter only
    profile: Optional[str] = None      # hermes adapter only
    timeout: Optional[float] = None    # hermes adapter only (seconds)


@dataclass
class PolyculeConfig:
    hub: HubConfig = field(default_factory=HubConfig)
    tui: TUIConfig = field(default_factory=TUIConfig)
    agents: List[AgentConfig] = field(default_factory=list)
    config_path: Optional[Path] = None


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

_SEARCH_PATHS = [
    Path(os.environ.get("POLYCULE_CONFIG", "")),
    Path.cwd() / "polycule.toml",
    Path.home() / ".config" / "polycule" / "config.toml",
]


def load_config() -> PolyculeConfig:
    """Load and parse polycule.toml. Returns defaults if no file found."""
    if tomllib is None:
        print(
            "WARNING: tomllib not available. "
            "Upgrade to Python 3.11+ or run: pip install tomli",
            file=sys.stderr,
        )
        return PolyculeConfig()

    for candidate in _SEARCH_PATHS:
        if candidate and candidate.exists():
            return _parse(candidate)

    return PolyculeConfig()


def _parse(path: Path) -> PolyculeConfig:
    with open(path, "rb") as f:
        raw = tomllib.load(f)

    hub_raw = raw.get("hub", {})
    hub = HubConfig(
        host=hub_raw.get("host", "localhost"),
        port=int(hub_raw.get("port", 7777)),
    )

    tui_raw = raw.get("tui", {})
    tui = TUIConfig(
        default_room=tui_raw.get("default_room", "Main"),
        default_name=tui_raw.get("default_name", "you"),
    )

    agents = []
    for a in raw.get("agent", []):
        if not a.get("enabled", True):
            continue
        agents.append(AgentConfig(
            name=a.get("name", "Agent"),
            adapter=a.get("adapter", "shell"),
            room=a.get("room", tui.default_room),
            triggers=a.get("triggers", []),
            always=a.get("always", False),
            enabled=True,
            command=a.get("command"),
            profile=a.get("profile"),
            timeout=a.get("timeout"),
        ))

    return PolyculeConfig(hub=hub, tui=tui, agents=agents, config_path=path)
