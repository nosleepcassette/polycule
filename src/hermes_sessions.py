# maps · cassette.help · MIT
"""
Hermes session file helpers.

Hermes persists CLI sessions to profile-specific directories under ~/.hermes.
Polycule uses these helpers to validate stored session ids and to discover the
session created by the first non-resumed adapter call.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

HERMES_HOME = Path.home() / ".hermes"
HERMES_BIN = HERMES_HOME / "bin" / "hermes"


def normalize_hermes_profile(profile: str) -> str:
    normalized = (profile or "").strip().lower()
    if normalized in ("", "default", "cassette"):
        return "cassette"
    return normalized


def hermes_session_dir(profile: str) -> Path:
    normalized = normalize_hermes_profile(profile)
    if normalized == "cassette":
        return HERMES_HOME / "sessions"
    return HERMES_HOME / "profiles" / normalized / "sessions"


def hermes_session_file(profile: str, session_id: str) -> Path:
    return hermes_session_dir(profile) / f"session_{session_id}.json"


def hermes_session_exists(profile: str, session_id: str) -> bool:
    session = (session_id or "").strip()
    if not session:
        return False
    return hermes_session_file(profile, session).exists()


def snapshot_hermes_sessions(profile: str) -> dict[str, float]:
    out: dict[str, float] = {}
    session_dir = hermes_session_dir(profile)
    if not session_dir.exists():
        return out
    for path in session_dir.glob("session_*.json"):
        session_id = path.stem.removeprefix("session_")
        try:
            out[session_id] = path.stat().st_mtime
        except OSError:
            continue
    return out


def newest_hermes_session_id(
    profile: str,
    *,
    changed_since: Optional[dict[str, float]] = None,
) -> Optional[str]:
    snapshot = snapshot_hermes_sessions(profile)
    candidates: list[tuple[float, str]] = []
    for session_id, mtime in snapshot.items():
        if changed_since is not None:
            previous_mtime = changed_since.get(session_id)
            if previous_mtime is not None and mtime <= previous_mtime:
                continue
        candidates.append((mtime, session_id))
    if not candidates:
        return None
    candidates.sort()
    return candidates[-1][1]


def rename_hermes_session(session_id: str, title: str) -> bool:
    if not HERMES_BIN.exists():
        return False
    try:
        result = subprocess.run(
            [str(HERMES_BIN), "sessions", "rename", session_id, title],
            capture_output=True,
            text=True,
            timeout=10.0,
        )
    except Exception:
        return False
    return result.returncode == 0
