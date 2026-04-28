# Polycule · MIT
"""
Runtime state helpers backed by the local Polycule SQLite settings table.

This module is intentionally small and sync-only. It is used by the CLI and
agent adapters to persist lightweight runtime state such as managed agent
session ids.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from backend.db import PolyculeDB

PROJECT_DIR = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_DIR / "polycule.db"
SESSION_REGISTRY_KEY = "agent_session_registry"
WATCH_REGISTRY_KEY = "agent_watch_registry"
TEMP_ENABLEMENT_REGISTRY_KEY = "temporary_agent_enablements"
CHAT_SESSION_TITLE_BASE = "chat"
LEGACY_SESSION_TITLE_PREFIX = "polycule:"


def _db(db_path: Optional[Path] = None) -> PolyculeDB:
    return PolyculeDB(db_path=db_path or DB_PATH)


def load_json_setting(
    key: str,
    default: Any,
    *,
    db_path: Optional[Path] = None,
) -> Any:
    raw = _db(db_path).get_setting(key, "") or ""
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


def save_json_setting(
    key: str,
    value: Any,
    *,
    db_path: Optional[Path] = None,
):
    _db(db_path).set_setting(key, json.dumps(value, sort_keys=True))


def make_agent_session_key(agent_family: str, room: str, *, profile: str = "") -> str:
    family = agent_family.strip().lower()
    normalized_profile = profile.strip().lower() or "default"
    normalized_room = room.strip().lower() or "default"
    return f"{family}:{normalized_profile}:{normalized_room}"


def normalize_session_title(title: Any) -> str:
    return " ".join(str(title or "").split()).strip()


def _iter_chat_titles(base: str = CHAT_SESSION_TITLE_BASE):
    normalized = normalize_session_title(base) or CHAT_SESSION_TITLE_BASE
    yield normalized
    suffix = 2
    while True:
        yield f"{normalized} {suffix}"
        suffix += 1


def load_agent_session_registry(*, db_path: Optional[Path] = None) -> dict[str, dict]:
    data = load_json_setting(SESSION_REGISTRY_KEY, {}, db_path=db_path)
    if not isinstance(data, dict):
        return {}
    out: dict[str, dict] = {}
    for key, value in data.items():
        if isinstance(key, str) and isinstance(value, dict):
            out[key] = dict(value)
    return out


def get_or_allocate_agent_session_title(
    key: str,
    *,
    db_path: Optional[Path] = None,
    base_title: str = CHAT_SESSION_TITLE_BASE,
) -> str:
    registry = load_agent_session_registry(db_path=db_path)
    entry = registry.get(key)
    if isinstance(entry, dict):
        stored_title = normalize_session_title(entry.get("title", ""))
        if stored_title and not stored_title.lower().startswith(LEGACY_SESSION_TITLE_PREFIX):
            return stored_title

    taken_titles: set[str] = set()
    for other_key, other_entry in registry.items():
        if other_key == key or not isinstance(other_entry, dict):
            continue
        other_title = normalize_session_title(other_entry.get("title", ""))
        if not other_title or other_title.lower().startswith(LEGACY_SESSION_TITLE_PREFIX):
            continue
        taken_titles.add(other_title.casefold())

    for candidate in _iter_chat_titles(base_title):
        if candidate.casefold() not in taken_titles:
            return candidate
    return CHAT_SESSION_TITLE_BASE


def save_agent_session_registry(
    registry: dict[str, dict],
    *,
    db_path: Optional[Path] = None,
):
    save_json_setting(SESSION_REGISTRY_KEY, registry, db_path=db_path)


def get_agent_session_entry(
    key: str,
    *,
    db_path: Optional[Path] = None,
) -> Optional[dict]:
    registry = load_agent_session_registry(db_path=db_path)
    entry = registry.get(key)
    if not isinstance(entry, dict):
        return None
    return dict(entry)


def update_agent_session_entry(
    key: str,
    *,
    db_path: Optional[Path] = None,
    **fields: Any,
) -> dict:
    registry = load_agent_session_registry(db_path=db_path)
    entry = dict(registry.get(key) or {})
    for field_name, field_value in fields.items():
        if field_value is not None:
            entry[field_name] = field_value
    entry["updated_at"] = datetime.now(timezone.utc).isoformat()
    registry[key] = entry
    save_agent_session_registry(registry, db_path=db_path)
    return dict(entry)


def clear_agent_session_entry(
    key: str,
    *,
    db_path: Optional[Path] = None,
):
    registry = load_agent_session_registry(db_path=db_path)
    if key in registry:
        del registry[key]
        save_agent_session_registry(registry, db_path=db_path)


def make_agent_watch_key(agent_name: str, room: str) -> str:
    normalized_agent = normalize_session_title(agent_name).lower() or "unknown"
    normalized_room = normalize_session_title(room).lower() or "default"
    return f"{normalized_agent}:{normalized_room}"


def _load_registry(
    setting_key: str,
    *,
    db_path: Optional[Path] = None,
) -> dict[str, dict]:
    data = load_json_setting(setting_key, {}, db_path=db_path)
    if not isinstance(data, dict):
        return {}
    out: dict[str, dict] = {}
    for key, value in data.items():
        if isinstance(key, str) and isinstance(value, dict):
            out[key] = dict(value)
    return out


def _save_registry(
    setting_key: str,
    registry: dict[str, dict],
    *,
    db_path: Optional[Path] = None,
):
    save_json_setting(setting_key, registry, db_path=db_path)


def normalize_watch_scope(scope: Any, target: Any = "") -> tuple[str, str]:
    normalized_scope = normalize_session_title(scope).lower() or "none"
    normalized_target = normalize_session_title(target).lower()

    if normalized_scope in ("none", "off", "clear"):
        return "none", ""
    if normalized_scope in ("maps", "human"):
        return "human", ""
    if normalized_scope == "room":
        return "room", ""
    if normalized_scope.startswith("@"):
        return "agent", normalized_scope[1:]
    if normalized_scope.startswith("agent:"):
        return "agent", normalized_scope.split(":", 1)[1].strip().lower()
    if normalized_scope == "agent" and normalized_target:
        if normalized_target.startswith("@"):
            normalized_target = normalized_target[1:]
        return "agent", normalized_target
    return "none", ""


def load_agent_watch_registry(*, db_path: Optional[Path] = None) -> dict[str, dict]:
    return _load_registry(WATCH_REGISTRY_KEY, db_path=db_path)


def save_agent_watch_registry(
    registry: dict[str, dict],
    *,
    db_path: Optional[Path] = None,
):
    _save_registry(WATCH_REGISTRY_KEY, registry, db_path=db_path)


def get_agent_watch_entry(
    agent_name: str,
    room: str,
    *,
    db_path: Optional[Path] = None,
) -> Optional[dict]:
    registry = load_agent_watch_registry(db_path=db_path)
    key = make_agent_watch_key(agent_name, room)
    entry = registry.get(key)
    if not isinstance(entry, dict):
        return None
    return dict(entry)


def update_agent_watch_entry(
    agent_name: str,
    room: str,
    *,
    scope: Any,
    target: Any = "",
    updated_by: str = "",
    db_path: Optional[Path] = None,
) -> dict:
    normalized_scope, normalized_target = normalize_watch_scope(scope, target)
    key = make_agent_watch_key(agent_name, room)
    registry = load_agent_watch_registry(db_path=db_path)
    entry = dict(registry.get(key) or {})
    entry.update(
        {
            "agent_name": normalize_session_title(agent_name).lower(),
            "room": normalize_session_title(room) or "Default",
            "scope": normalized_scope,
            "target": normalized_target,
            "updated_by": normalize_session_title(updated_by),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    registry[key] = entry
    save_agent_watch_registry(registry, db_path=db_path)
    return dict(entry)


def clear_agent_watch_entry(
    agent_name: str,
    room: str,
    *,
    db_path: Optional[Path] = None,
):
    key = make_agent_watch_key(agent_name, room)
    registry = load_agent_watch_registry(db_path=db_path)
    if key in registry:
        del registry[key]
        save_agent_watch_registry(registry, db_path=db_path)


def load_temporary_agent_enablements(
    *,
    db_path: Optional[Path] = None,
) -> dict[str, dict]:
    return _load_registry(TEMP_ENABLEMENT_REGISTRY_KEY, db_path=db_path)


def save_temporary_agent_enablements(
    registry: dict[str, dict],
    *,
    db_path: Optional[Path] = None,
):
    _save_registry(TEMP_ENABLEMENT_REGISTRY_KEY, registry, db_path=db_path)


def get_temporary_agent_enablements(
    room: str,
    *,
    db_path: Optional[Path] = None,
) -> dict[str, dict]:
    registry = load_temporary_agent_enablements(db_path=db_path)
    room_key = normalize_session_title(room).lower() or "default"
    entry = registry.get(room_key)
    if not isinstance(entry, dict):
        return {}
    out: dict[str, dict] = {}
    for key, value in entry.items():
        if isinstance(key, str) and isinstance(value, dict):
            out[key] = dict(value)
    return out


def mark_temporary_agent_enablements(
    room: str,
    agent_names: list[str],
    *,
    agent_state: Optional[dict[str, dict[str, Any]]] = None,
    temporary_mode: str = "",
    enabled_by: str = "",
    reason: str = "summon",
    db_path: Optional[Path] = None,
) -> dict[str, dict]:
    normalized_room = normalize_session_title(room) or "Default"
    room_key = normalized_room.lower()
    registry = load_temporary_agent_enablements(db_path=db_path)
    room_entry = dict(registry.get(room_key) or {})
    timestamp = datetime.now(timezone.utc).isoformat()
    for raw_name in agent_names:
        agent_name = normalize_session_title(raw_name).lower()
        if not agent_name:
            continue
        current = dict(room_entry.get(agent_name) or {})
        state_snapshot = dict((agent_state or {}).get(agent_name) or {})
        previous_state = normalize_session_title(
            current.get("previous_state", state_snapshot.get("state", "enabled"))
        ).lower() or "enabled"
        previous_mode = normalize_session_title(
            current.get("previous_mode", state_snapshot.get("mode", "mention"))
        ).lower() or "mention"
        room_entry[agent_name] = {
            "agent_name": agent_name,
            "room": normalized_room,
            "enabled_by": normalize_session_title(enabled_by),
            "reason": normalize_session_title(reason) or "summon",
            "previous_state": previous_state,
            "previous_mode": previous_mode,
            "temporary_mode": normalize_session_title(temporary_mode).lower(),
            "updated_at": timestamp,
        }
    registry[room_key] = room_entry
    save_temporary_agent_enablements(registry, db_path=db_path)
    return get_temporary_agent_enablements(normalized_room, db_path=db_path)


def clear_temporary_agent_enablements(
    room: str,
    *,
    agent_names: Optional[list[str]] = None,
    db_path: Optional[Path] = None,
) -> dict[str, dict]:
    normalized_room = normalize_session_title(room) or "Default"
    room_key = normalized_room.lower()
    registry = load_temporary_agent_enablements(db_path=db_path)
    room_entry = dict(registry.get(room_key) or {})
    if not room_entry:
        return {}

    if not agent_names:
        registry.pop(room_key, None)
    else:
        for raw_name in agent_names:
            agent_name = normalize_session_title(raw_name).lower()
            if agent_name:
                room_entry.pop(agent_name, None)
        if room_entry:
            registry[room_key] = room_entry
        else:
            registry.pop(room_key, None)
    save_temporary_agent_enablements(registry, db_path=db_path)
    return get_temporary_agent_enablements(normalized_room, db_path=db_path)
