# Polycule · MIT
"""
PolyculeDB — SQLite persistence for rooms, messages, and settings.

Lightweight wrapper: synchronous reads/writes (hub calls from async context
should use asyncio.to_thread for writes if latency matters, but SQLite is
fast enough for this load that sync is fine in the hub's event loop).
"""
import sqlite3
import logging
from pathlib import Path
from typing import List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / 'polycule.db'

SCHEMA = """
CREATE TABLE IF NOT EXISTS rooms (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS messages (
    id          TEXT PRIMARY KEY,
    room_id     TEXT NOT NULL REFERENCES rooms(id),
    agent_id    TEXT NOT NULL,
    agent_name  TEXT NOT NULL,
    agent_type  TEXT NOT NULL,
    content     TEXT NOT NULL,
    timestamp   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_messages_room_ts
    ON messages (room_id, timestamp);

CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS pins (
    id          TEXT PRIMARY KEY,
    room_id     TEXT NOT NULL,
    message_id  TEXT NOT NULL,
    content     TEXT NOT NULL,
    sender_name TEXT NOT NULL,
    pinned_by   TEXT NOT NULL,
    pinned_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_pins_room
    ON pins (room_id, pinned_at);

INSERT OR IGNORE INTO settings (key, value) VALUES ('auto_approve', 'false');
INSERT OR IGNORE INTO settings (key, value) VALUES ('context_window', '100');
"""


class PolyculeDB:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init(self):
        with self._conn() as conn:
            conn.executescript(SCHEMA)
        logger.info(f"DB ready: {self.db_path}")

    # ------------------------------------------------------------------
    # Rooms
    # ------------------------------------------------------------------

    def save_room(self, room_id: str, name: str):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO rooms (id, name) VALUES (?, ?)",
                (room_id, name),
            )

    def get_all_rooms(self) -> List[dict]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM rooms").fetchall()
        return [dict(r) for r in rows]

    def room_exists(self, room_id: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM rooms WHERE id = ?", (room_id,)
            ).fetchone()
        return row is not None

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    def save_message(
        self,
        msg_id: str,
        room_id: str,
        agent_id: str,
        agent_name: str,
        agent_type: str,
        content: str,
        timestamp: Optional[str] = None,
    ):
        ts = timestamp or datetime.utcnow().isoformat()
        with self._conn() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO messages
                   (id, room_id, agent_id, agent_name, agent_type, content, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (msg_id, room_id, agent_id, agent_name, agent_type, content, ts),
            )

    def get_recent_messages(self, room_id: str, limit: int = 100) -> List[dict]:
        """Return last `limit` messages in chronological order."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT id, room_id, agent_id, agent_name, agent_type, content, timestamp
                   FROM messages
                   WHERE room_id = ?
                   ORDER BY timestamp DESC
                   LIMIT ?""",
                (room_id, limit),
            ).fetchall()
        # Reverse so oldest-first
        return [_row_to_message(dict(r)) for r in reversed(rows)]

    def message_count(self, room_id: str) -> int:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE room_id = ?", (room_id,)
            ).fetchone()
        return row[0] if row else 0

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
        return row['value'] if row else default

    def set_setting(self, key: str, value: str):
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO settings (key, value, updated_at)
                   VALUES (?, ?, datetime('now'))
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
                (key, value),
            )
        logger.info(f"Setting updated: {key}={value}")

    def auto_approve(self) -> bool:
        return self.get_setting('auto_approve', 'false').lower() == 'true'

    def set_auto_approve(self, enabled: bool):
        self.set_setting('auto_approve', 'true' if enabled else 'false')

    def context_window(self) -> int:
        try:
            return int(self.get_setting('context_window', '100'))
        except ValueError:
            return 100

    # ------------------------------------------------------------------
    # Pins
    # ------------------------------------------------------------------

    def save_pin(
        self,
        room_id: str,
        message_id: str,
        content: str,
        sender_name: str,
        pinned_by: str,
    ):
        pin_id = f"{room_id}:{message_id}"
        with self._conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO pins
                   (id, room_id, message_id, content, sender_name, pinned_by)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (pin_id, room_id, message_id, content, sender_name, pinned_by),
            )

    def get_pins(self, room_id: str) -> List[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT message_id, content, sender_name, pinned_by, pinned_at
                   FROM pins WHERE room_id = ?
                   ORDER BY pinned_at ASC""",
                (room_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_pin(self, room_id: str, message_id: str):
        pin_id = f"{room_id}:{message_id}"
        with self._conn() as conn:
            conn.execute("DELETE FROM pins WHERE id = ?", (pin_id,))

    # ------------------------------------------------------------------
    # Convenience settings
    # ------------------------------------------------------------------

    def get_last_room(self, default: str = "Default") -> str:
        return self.get_setting("last_room", default) or default

    def set_last_room(self, room_name: str):
        self.set_setting("last_room", room_name)

    def get_room_topic(self, room_id: str, default: str = "") -> str:
        return self.get_setting(f"topic:{room_id}", default) or default

    def set_room_topic(self, room_id: str, topic: str):
        self.set_setting(f"topic:{room_id}", topic)


def _row_to_message(row: dict) -> dict:
    """Convert a DB row into the hub message wire format."""
    return {
        'id': row['id'],
        'type': 'message',
        'content': row['content'],
        'sender': {
            'id': row['agent_id'],
            'name': row['agent_name'],
            'type': row['agent_type'],
        },
        'room_id': row['room_id'],
        'timestamp': row['timestamp'],
    }
