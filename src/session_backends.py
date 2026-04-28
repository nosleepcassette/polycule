# Polycule · MIT
"""
Local session discovery helpers for external agent CLIs.

These helpers only inspect on-disk state. They do not invoke the model CLIs.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional

HOME = Path.home()
CODEX_STATE_DB = HOME / ".codex" / "state_5.sqlite"
CLAUDE_PROJECTS_DIR = HOME / ".claude" / "projects"
OPENCODE_DB = HOME / ".local" / "share" / "opencode" / "opencode.db"
GEMINI_HOME = HOME / ".gemini"
GEMINI_PROJECTS = GEMINI_HOME / "projects.json"
GEMINI_TMP_DIR = GEMINI_HOME / "tmp"


def normalize_cwd(cwd: str | Path) -> str:
    return str(Path(cwd).expanduser().resolve())


def _sqlite_rows(
    db_path: Path,
    query: str,
    params: tuple = (),
) -> list[dict]:
    if not db_path.exists():
        return []
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(query, params).fetchall()
    except sqlite3.Error:
        return []
    return [dict(row) for row in rows]


def _session_snapshot(rows: list[dict], updated_key: str) -> dict[str, int]:
    snapshot: dict[str, int] = {}
    for row in rows:
        session_id = str(row.get("id", "")).strip()
        updated_at = int(row.get(updated_key) or 0)
        if session_id and updated_at > 0:
            snapshot[session_id] = updated_at
    return snapshot


def _pick_newest_session_id(
    snapshot: dict[str, int],
    *,
    changed_since: Optional[dict[str, int]] = None,
) -> Optional[str]:
    candidates: list[tuple[int, str]] = []
    for session_id, updated_at in snapshot.items():
        if changed_since is not None:
            previous = int(changed_since.get(session_id, -1))
            if previous >= updated_at:
                continue
        candidates.append((updated_at, session_id))
    if not candidates:
        return None
    candidates.sort()
    return candidates[-1][1]


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def codex_session_exists(
    session_id: str,
    *,
    state_db: Path = CODEX_STATE_DB,
) -> bool:
    session = str(session_id or "").strip()
    if not session:
        return False
    rows = _sqlite_rows(
        state_db,
        "SELECT 1 FROM threads WHERE id = ? AND archived = 0 LIMIT 1",
        (session,),
    )
    return bool(rows)


def snapshot_codex_sessions(
    cwd: str | Path,
    *,
    title_prefix: str = "",
    state_db: Path = CODEX_STATE_DB,
) -> dict[str, int]:
    resolved_cwd = normalize_cwd(cwd)
    rows = _sqlite_rows(
        state_db,
        """
        SELECT id, title, updated_at
        FROM threads
        WHERE archived = 0 AND cwd = ?
        ORDER BY updated_at DESC
        """,
        (resolved_cwd,),
    )
    normalized_prefix = str(title_prefix or "").strip()
    if normalized_prefix:
        rows = [
            row
            for row in rows
            if str(row.get("title", "")).startswith(normalized_prefix)
        ]
    return _session_snapshot(rows, "updated_at")


def newest_codex_session_id(
    cwd: str | Path,
    *,
    changed_since: Optional[dict[str, int]] = None,
    title_prefix: str = "",
    state_db: Path = CODEX_STATE_DB,
) -> Optional[str]:
    snapshot = snapshot_codex_sessions(
        cwd,
        title_prefix=title_prefix,
        state_db=state_db,
    )
    return _pick_newest_session_id(snapshot, changed_since=changed_since)


def claude_project_slug(cwd: str | Path) -> str:
    resolved = Path(normalize_cwd(cwd))
    parts = list(resolved.parts)
    if parts and parts[0] == resolved.anchor:
        parts = parts[1:]
    return "-" + "-".join(parts)


def claude_project_dir(
    cwd: str | Path,
    *,
    projects_dir: Path = CLAUDE_PROJECTS_DIR,
) -> Path:
    return projects_dir / claude_project_slug(cwd)


def claude_session_exists(
    cwd: str | Path,
    session_id: str,
    *,
    projects_dir: Path = CLAUDE_PROJECTS_DIR,
) -> bool:
    session = str(session_id or "").strip()
    if not session:
        return False
    return (claude_project_dir(cwd, projects_dir=projects_dir) / f"{session}.jsonl").exists()


def _claude_matches_hint(path: Path, content_hint: str) -> bool:
    if not content_hint:
        return True
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            snippet = handle.read(32768)
    except OSError:
        return False
    return content_hint in snippet


def snapshot_claude_sessions(
    cwd: str | Path,
    *,
    content_hint: str = "",
    projects_dir: Path = CLAUDE_PROJECTS_DIR,
) -> dict[str, int]:
    project_dir = claude_project_dir(cwd, projects_dir=projects_dir)
    if not project_dir.exists():
        return {}
    snapshot: dict[str, int] = {}
    for path in project_dir.glob("*.jsonl"):
        if not _claude_matches_hint(path, content_hint):
            continue
        session_id = path.stem.strip()
        if not session_id:
            continue
        try:
            snapshot[session_id] = int(path.stat().st_mtime_ns)
        except OSError:
            continue
    return snapshot


def newest_claude_session_id(
    cwd: str | Path,
    *,
    changed_since: Optional[dict[str, int]] = None,
    content_hint: str = "",
    projects_dir: Path = CLAUDE_PROJECTS_DIR,
) -> Optional[str]:
    snapshot = snapshot_claude_sessions(
        cwd,
        content_hint=content_hint,
        projects_dir=projects_dir,
    )
    return _pick_newest_session_id(snapshot, changed_since=changed_since)


def opencode_session_exists(
    session_id: str,
    *,
    state_db: Path = OPENCODE_DB,
) -> bool:
    session = str(session_id or "").strip()
    if not session:
        return False
    rows = _sqlite_rows(
        state_db,
        """
        SELECT 1
        FROM session
        WHERE id = ? AND COALESCE(time_archived, 0) = 0
        LIMIT 1
        """,
        (session,),
    )
    return bool(rows)


def snapshot_opencode_sessions(
    cwd: str | Path,
    *,
    title: str = "",
    state_db: Path = OPENCODE_DB,
) -> dict[str, int]:
    resolved_cwd = normalize_cwd(cwd)
    rows = _sqlite_rows(
        state_db,
        """
        SELECT id, title, time_updated
        FROM session
        WHERE directory = ? AND COALESCE(time_archived, 0) = 0
        ORDER BY time_updated DESC
        """,
        (resolved_cwd,),
    )
    normalized_title = str(title or "").strip()
    if normalized_title:
        rows = [row for row in rows if str(row.get("title", "")).strip() == normalized_title]
    return _session_snapshot(rows, "time_updated")


def newest_opencode_session_id(
    cwd: str | Path,
    *,
    changed_since: Optional[dict[str, int]] = None,
    title: str = "",
    state_db: Path = OPENCODE_DB,
) -> Optional[str]:
    snapshot = snapshot_opencode_sessions(
        cwd,
        title=title,
        state_db=state_db,
    )
    return _pick_newest_session_id(snapshot, changed_since=changed_since)


def gemini_project_slug(
    cwd: str | Path,
    *,
    projects_file: Path = GEMINI_PROJECTS,
    tmp_dir: Path = GEMINI_TMP_DIR,
) -> Optional[str]:
    resolved_cwd = normalize_cwd(cwd)
    data = _load_json(projects_file)
    projects = data.get("projects", {}) if isinstance(data, dict) else {}
    if isinstance(projects, dict):
        slug = str(projects.get(resolved_cwd, "")).strip()
        if slug:
            return slug

    if not tmp_dir.exists():
        return None
    for path in tmp_dir.iterdir():
        marker = path / ".project_root"
        if not marker.exists():
            continue
        try:
            owner = marker.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if normalize_cwd(owner) == resolved_cwd:
            return path.name.strip() or None
    return None


def gemini_project_dir(
    cwd: str | Path,
    *,
    projects_file: Path = GEMINI_PROJECTS,
    tmp_dir: Path = GEMINI_TMP_DIR,
) -> Optional[Path]:
    slug = gemini_project_slug(cwd, projects_file=projects_file, tmp_dir=tmp_dir)
    if not slug:
        return None
    return tmp_dir / slug


def _gemini_session_payload(path: Path) -> dict:
    data = _load_json(path)
    return data if isinstance(data, dict) else {}


def _gemini_message_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = str(item.get("text", "")).strip()
                if text:
                    chunks.append(text)
        return "\n".join(chunks)
    return ""


def _gemini_has_conversation(payload: dict) -> bool:
    messages = payload.get("messages", [])
    if not isinstance(messages, list):
        return False
    return any(
        isinstance(message, dict)
        and str(message.get("type", "")).strip().lower() in ("user", "gemini")
        for message in messages
    )


def _gemini_matches_hint(payload: dict, content_hint: str) -> bool:
    if not content_hint:
        return True
    parts: list[str] = []
    summary = str(payload.get("summary", "")).strip()
    if summary:
        parts.append(summary)
    messages = payload.get("messages", [])
    if isinstance(messages, list):
        for message in messages[:8]:
            if not isinstance(message, dict):
                continue
            parts.append(_gemini_message_text(message.get("content")))
    haystack = "\n".join(part for part in parts if part).strip()
    return content_hint in haystack


def gemini_session_exists(
    cwd: str | Path,
    session_id: str,
    *,
    projects_file: Path = GEMINI_PROJECTS,
    tmp_dir: Path = GEMINI_TMP_DIR,
) -> bool:
    session = str(session_id or "").strip()
    if not session:
        return False
    project_dir = gemini_project_dir(cwd, projects_file=projects_file, tmp_dir=tmp_dir)
    if not project_dir:
        return False
    chats_dir = project_dir / "chats"
    if not chats_dir.exists():
        return False
    for path in chats_dir.glob("session-*.json"):
        payload = _gemini_session_payload(path)
        if str(payload.get("sessionId", "")).strip() == session:
            return True
    return False


def snapshot_gemini_sessions(
    cwd: str | Path,
    *,
    content_hint: str = "",
    projects_file: Path = GEMINI_PROJECTS,
    tmp_dir: Path = GEMINI_TMP_DIR,
) -> dict[str, int]:
    project_dir = gemini_project_dir(cwd, projects_file=projects_file, tmp_dir=tmp_dir)
    if not project_dir:
        return {}
    chats_dir = project_dir / "chats"
    if not chats_dir.exists():
        return {}

    snapshot: dict[str, int] = {}
    for path in chats_dir.glob("session-*.json"):
        payload = _gemini_session_payload(path)
        session_id = str(payload.get("sessionId", "")).strip()
        if not session_id or not _gemini_has_conversation(payload):
            continue
        if not _gemini_matches_hint(payload, content_hint):
            continue
        try:
            snapshot[session_id] = path.stat().st_mtime_ns
        except OSError:
            continue
    return snapshot


def newest_gemini_session_id(
    cwd: str | Path,
    *,
    changed_since: Optional[dict[str, int]] = None,
    content_hint: str = "",
    projects_file: Path = GEMINI_PROJECTS,
    tmp_dir: Path = GEMINI_TMP_DIR,
) -> Optional[str]:
    snapshot = snapshot_gemini_sessions(
        cwd,
        content_hint=content_hint,
        projects_file=projects_file,
        tmp_dir=tmp_dir,
    )
    return _pick_newest_session_id(snapshot, changed_since=changed_since)
