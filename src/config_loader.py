# Polycule · MIT
"""Typed configuration loading for Polycule.

The machine-local project config lives at ``polycule.toml`` and is intentionally
gitignored. If it is absent, ``~/.config/polycule/config.toml`` is used as the
machine-global fallback. Environment variables then override loaded values.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import os
from pathlib import Path
from typing import Any, Mapping

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python <3.11 fallback path
    tomllib = None  # type: ignore[assignment]


AGENT_MODE_VALUES = ("mention", "always", "handoff", "ffa", "off")
DEFAULT_AGENT_ORDER = ("hermes", "codex", "claude", "opencode", "gemini")
DEFAULT_EXTERNAL_AGENTS = ("codex", "claude", "opencode", "gemini")
DEFAULT_HERMES_PROFILES = ("hermes",)


@dataclass(frozen=True)
class OperatorConfig:
    name: str = ""
    room: str = "Default"


@dataclass(frozen=True)
class HubConfig:
    host: str = "localhost"
    port: int = 7777
    hub_timeout: float = 10.0


@dataclass(frozen=True)
class ThemeConfig:
    name: str = "amber"


@dataclass(frozen=True)
class AgentConfig:
    enabled: bool = True
    mode: str = "mention"
    alias: str = ""


@dataclass(frozen=True)
class HermesConfig:
    home: str = ""
    profiles: tuple[str, ...] = ()
    always_profiles: tuple[str, ...] = ("hermes",)


@dataclass(frozen=True)
class ExternalConfig:
    agents: tuple[str, ...] = ()


@dataclass(frozen=True)
class AutocompleteConfig:
    max_file_candidates: int = 60
    show_hidden: bool = True


@dataclass(frozen=True)
class PolyculeConfig:
    operator: OperatorConfig = field(default_factory=OperatorConfig)
    hub: HubConfig = field(default_factory=HubConfig)
    theme: ThemeConfig = field(default_factory=ThemeConfig)
    agents: dict[str, AgentConfig] = field(default_factory=dict)
    hermes: HermesConfig = field(default_factory=HermesConfig)
    external: ExternalConfig = field(default_factory=ExternalConfig)
    autocomplete: AutocompleteConfig = field(default_factory=AutocompleteConfig)
    path: Path | None = None

    @property
    def agent_names(self) -> tuple[str, ...]:
        return tuple(self.agents.keys())

    @property
    def disabled_agent_names(self) -> tuple[str, ...]:
        return tuple(name for name, cfg in self.agents.items() if not cfg.enabled)

    def mode_for(self, agent_name: str, default: str = "mention") -> str:
        cfg = self.agents.get(agent_name.strip().lower())
        if not cfg:
            return default
        return cfg.mode or default


def project_config_path(project_dir: Path | None = None) -> Path:
    root = project_dir or Path.cwd()
    return root / "polycule.toml"


def global_config_path() -> Path:
    return Path.home() / ".config" / "polycule" / "config.toml"


def find_config_path(project_dir: Path | None = None) -> Path | None:
    project_path = project_config_path(project_dir)
    if project_path.exists():
        return project_path
    global_path = global_config_path()
    if global_path.exists():
        return global_path
    return None


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _as_int(value: Any, default: int, *, minimum: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if minimum is not None and parsed < minimum:
        return default
    return parsed


def _as_float(value: Any, default: float, *, minimum: float | None = None) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if minimum is not None and parsed < minimum:
        return default
    return parsed


def _as_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        parts = value.split(",")
    elif isinstance(value, (list, tuple, set)):
        parts = list(value)
    else:
        return ()
    out: list[str] = []
    seen: set[str] = set()
    for part in parts:
        item = str(part).strip().lower()
        if item and item not in seen:
            out.append(item)
            seen.add(item)
    return tuple(out)


def _display_name(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text.replace("-", " ").replace("_", " ").title().replace(" ", "-")


def normalize_agent_mode(value: str, default: str = "mention") -> str:
    mode = str(value or "").strip().lower()
    aliases = {
        "free": "ffa",
        "freeforall": "ffa",
        "free-for-all": "ffa",
        "swarm": "ffa",
        "collab": "handoff",
        "relay": "handoff",
    }
    mode = aliases.get(mode, mode)
    return mode if mode in AGENT_MODE_VALUES else default


def _default_agents() -> dict[str, AgentConfig]:
    return {
        "hermes": AgentConfig(enabled=True, mode="always", alias="Hermes"),
        "codex": AgentConfig(enabled=False, mode="mention", alias="Codex"),
        "claude": AgentConfig(enabled=False, mode="mention", alias="Claude"),
        "opencode": AgentConfig(enabled=False, mode="mention", alias="OpenCode"),
        "gemini": AgentConfig(enabled=False, mode="mention", alias="Gemini"),
    }


def _load_raw(path: Path | None) -> dict[str, Any]:
    if not path:
        return {}
    if tomllib is None:
        raise RuntimeError("polycule.toml requires Python 3.11+ tomllib")
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return data if isinstance(data, dict) else {}


def _apply_env(config: PolyculeConfig, env: Mapping[str, str]) -> PolyculeConfig:
    operator = config.operator
    hub = config.hub
    theme = config.theme
    hermes = config.hermes
    external = config.external
    autocomplete = config.autocomplete

    if env.get("POLYCULE_OPERATOR_NAME"):
        operator = replace(operator, name=env["POLYCULE_OPERATOR_NAME"].strip())
    if env.get("POLYCULE_ROOM"):
        operator = replace(operator, room=env["POLYCULE_ROOM"].strip() or operator.room)
    if env.get("POLYCULE_HUB_HOST"):
        hub = replace(hub, host=env["POLYCULE_HUB_HOST"].strip() or hub.host)
    if env.get("POLYCULE_HUB_PORT"):
        hub = replace(hub, port=_as_int(env["POLYCULE_HUB_PORT"], hub.port, minimum=1))
    if env.get("POLYCULE_HUB_TIMEOUT"):
        hub = replace(
            hub,
            hub_timeout=_as_float(env["POLYCULE_HUB_TIMEOUT"], hub.hub_timeout, minimum=0.1),
        )
    if env.get("POLYCULE_THEME"):
        theme = replace(theme, name=env["POLYCULE_THEME"].strip() or theme.name)
    if env.get("POLYCULE_HERMES_HOME"):
        hermes = replace(hermes, home=env["POLYCULE_HERMES_HOME"].strip())
    if env.get("POLYCULE_HERMES_PROFILES"):
        hermes = replace(hermes, profiles=_as_tuple(env["POLYCULE_HERMES_PROFILES"]))
    if env.get("POLYCULE_HERMES_ALWAYS_PROFILES"):
        hermes = replace(
            hermes,
            always_profiles=_as_tuple(env["POLYCULE_HERMES_ALWAYS_PROFILES"]),
        )
    if env.get("POLYCULE_EXTERNAL_AGENTS"):
        external = replace(external, agents=_as_tuple(env["POLYCULE_EXTERNAL_AGENTS"]))
    if env.get("POLYCULE_AUTOCOMPLETE_MAX_FILE_CANDIDATES"):
        autocomplete = replace(
            autocomplete,
            max_file_candidates=_as_int(
                env["POLYCULE_AUTOCOMPLETE_MAX_FILE_CANDIDATES"],
                autocomplete.max_file_candidates,
                minimum=1,
            ),
        )
    if env.get("POLYCULE_AUTOCOMPLETE_SHOW_HIDDEN"):
        autocomplete = replace(
            autocomplete,
            show_hidden=_as_bool(
                env["POLYCULE_AUTOCOMPLETE_SHOW_HIDDEN"],
                autocomplete.show_hidden,
            ),
        )

    return replace(
        config,
        operator=operator,
        hub=hub,
        theme=theme,
        hermes=hermes,
        external=external,
        autocomplete=autocomplete,
    )


def load_config(
    project_dir: Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> PolyculeConfig:
    env = env or os.environ
    path = find_config_path(project_dir)
    raw = _load_raw(path)

    operator_raw = _as_mapping(raw.get("operator"))
    hub_raw = _as_mapping(raw.get("hub"))
    theme_raw = _as_mapping(raw.get("theme"))
    hermes_raw = _as_mapping(raw.get("hermes"))
    external_raw = _as_mapping(raw.get("external"))
    autocomplete_raw = _as_mapping(raw.get("autocomplete"))

    fallback_name = env.get("POLYCULE_OPERATOR_NAME") or env.get("USER") or env.get("LOGNAME") or "you"
    operator = OperatorConfig(
        name=str(operator_raw.get("name") or fallback_name).strip() or "you",
        room=str(operator_raw.get("room") or "Default").strip() or "Default",
    )
    hub = HubConfig(
        host=str(hub_raw.get("host") or "localhost").strip() or "localhost",
        port=_as_int(hub_raw.get("port"), 7777, minimum=1),
        hub_timeout=_as_float(hub_raw.get("hub_timeout"), 10.0, minimum=0.1),
    )
    theme = ThemeConfig(name=str(theme_raw.get("name") or "amber").strip() or "amber")
    hermes = HermesConfig(
        home=str(hermes_raw.get("home") or "").strip(),
        profiles=_as_tuple(hermes_raw.get("profiles")),
        always_profiles=_as_tuple(hermes_raw.get("always_profiles")) or ("hermes",),
    )
    external = ExternalConfig(agents=_as_tuple(external_raw.get("agents")))
    autocomplete = AutocompleteConfig(
        max_file_candidates=_as_int(
            autocomplete_raw.get("max_file_candidates"),
            60,
            minimum=1,
        ),
        show_hidden=_as_bool(autocomplete_raw.get("show_hidden"), True),
    )

    agents = _default_agents()
    for name, section in _as_mapping(raw.get("agents")).items():
        agent_name = str(name).strip().lower()
        if not agent_name:
            continue
        section_map = _as_mapping(section)
        existing = agents.get(agent_name, AgentConfig(alias=_display_name(agent_name)))
        agents[agent_name] = AgentConfig(
            enabled=_as_bool(section_map.get("enabled"), existing.enabled),
            mode=normalize_agent_mode(section_map.get("mode", existing.mode), existing.mode),
            alias=str(section_map.get("alias") or existing.alias or _display_name(agent_name)).strip(),
        )

    # Keep configured Hermes profiles and external agents addressable even if
    # they are not in the default six-agent set.
    for profile in hermes.profiles:
        agents.setdefault(
            profile,
            AgentConfig(
                enabled=True,
                mode="always" if profile in hermes.always_profiles else "mention",
                alias=_display_name(profile),
            ),
        )
    for agent in external.agents:
        if agent in DEFAULT_EXTERNAL_AGENTS:
            agents.setdefault(
                agent,
                AgentConfig(enabled=True, mode="mention", alias=_display_name(agent)),
            )

    # Stable ordering: defaults first, then any config-defined extras.
    ordered_agents: dict[str, AgentConfig] = {}
    for name in DEFAULT_AGENT_ORDER:
        if name in agents:
            ordered_agents[name] = agents[name]
    for name in agents:
        if name not in ordered_agents:
            ordered_agents[name] = agents[name]

    config = PolyculeConfig(
        operator=operator,
        hub=hub,
        theme=theme,
        agents=ordered_agents,
        hermes=hermes,
        external=external,
        autocomplete=autocomplete,
        path=path,
    )
    return _apply_env(config, env)


def write_default_config(path: Path | None = None, *, env: Mapping[str, str] | None = None) -> Path:
    """Write a conservative first-run config if no config exists."""
    env = env or os.environ
    target = path or global_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    name = env.get("POLYCULE_OPERATOR_NAME") or env.get("USER") or env.get("LOGNAME") or "you"
    target.write_text(
        f"""# Polycule local configuration
# polycule wraps existing CLI tools; it does not install providers for you.

[operator]
name = "{name}"
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

[agents.claude]
enabled = false
mode = "mention"
alias = "Claude"

[agents.opencode]
enabled = false
mode = "mention"
alias = "OpenCode"

[agents.gemini]
enabled = false
mode = "mention"
alias = "Gemini"

[hermes]
home = ""
profiles = []
always_profiles = ["hermes"]

[external]
agents = []

[autocomplete]
max_file_candidates = 60
show_hidden = true
""",
        encoding="utf-8",
    )
    return target
