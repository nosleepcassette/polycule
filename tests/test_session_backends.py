import sqlite3
import sys
import tempfile
import unittest
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from session_backends import (
    claude_project_slug,
    gemini_project_slug,
    gemini_session_exists,
    newest_claude_session_id,
    newest_codex_session_id,
    newest_gemini_session_id,
    newest_opencode_session_id,
    snapshot_claude_sessions,
    snapshot_codex_sessions,
    snapshot_gemini_sessions,
    snapshot_opencode_sessions,
)


class SessionBackendsTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_codex_snapshot_filters_by_title_prefix(self):
        db_path = self.root / "codex.sqlite"
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE threads (
                    id TEXT PRIMARY KEY,
                    cwd TEXT NOT NULL,
                    title TEXT NOT NULL,
                    updated_at INTEGER NOT NULL,
                    archived INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.executemany(
                "INSERT INTO threads (id, cwd, title, updated_at, archived) VALUES (?, ?, ?, ?, 0)",
                [
                    ("sess-a", "/repo", "polycule prompt one", 10),
                    ("sess-b", "/repo", "other prompt", 20),
                    ("sess-c", "/repo", "polycule prompt two", 30),
                ],
            )

        snapshot = snapshot_codex_sessions("/repo", title_prefix="polycule", state_db=db_path)
        self.assertEqual({"sess-a": 10, "sess-c": 30}, snapshot)
        self.assertEqual(
            "sess-c",
            newest_codex_session_id("/repo", title_prefix="polycule", state_db=db_path),
        )

    def test_claude_snapshot_filters_by_content_hint(self):
        projects_dir = self.root / "projects"
        project_dir = projects_dir / claude_project_slug("/repo")
        project_dir.mkdir(parents=True)
        (project_dir / "sess-a.jsonl").write_text("Polycule Claude marker\n", encoding="utf-8")
        (project_dir / "sess-b.jsonl").write_text("Manual session\n", encoding="utf-8")

        snapshot = snapshot_claude_sessions(
            "/repo",
            content_hint="Polycule Claude marker",
            projects_dir=projects_dir,
        )

        self.assertEqual(["sess-a"], sorted(snapshot.keys()))
        self.assertEqual(
            "sess-a",
            newest_claude_session_id(
                "/repo",
                content_hint="Polycule Claude marker",
                projects_dir=projects_dir,
            ),
        )

    def test_opencode_snapshot_filters_by_title(self):
        db_path = self.root / "opencode.sqlite"
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE session (
                    id TEXT PRIMARY KEY,
                    directory TEXT NOT NULL,
                    title TEXT NOT NULL,
                    time_updated INTEGER NOT NULL,
                    time_archived INTEGER
                )
                """
            )
            conn.executemany(
                "INSERT INTO session (id, directory, title, time_updated, time_archived) VALUES (?, ?, ?, ?, 0)",
                [
                    ("ses-1", "/repo", "polycule:opencode:Default", 10),
                    ("ses-2", "/repo", "manual", 20),
                    ("ses-3", "/repo", "polycule:opencode:Default", 30),
                ],
            )

        snapshot = snapshot_opencode_sessions(
            "/repo",
            title="polycule:opencode:Default",
            state_db=db_path,
        )
        self.assertEqual({"ses-1": 10, "ses-3": 30}, snapshot)
        self.assertEqual(
            "ses-3",
            newest_opencode_session_id(
                "/repo",
                title="polycule:opencode:Default",
                state_db=db_path,
            ),
        )

    def test_gemini_snapshot_filters_by_content_hint(self):
        projects_file = self.root / "projects.json"
        tmp_dir = self.root / "tmp"
        slug = "repo"
        projects_file.write_text(
            json.dumps({"projects": {"/repo": slug}}),
            encoding="utf-8",
        )
        chats_dir = tmp_dir / slug / "chats"
        chats_dir.mkdir(parents=True)
        (tmp_dir / slug / ".project_root").write_text("/repo\n", encoding="utf-8")
        (chats_dir / "session-a.json").write_text(
            json.dumps(
                {
                    "sessionId": "gem-a",
                    "messages": [
                        {"type": "user", "content": [{"text": "Gemini Polycule marker"}]},
                        {"type": "gemini", "content": "ok"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        (chats_dir / "session-b.json").write_text(
            json.dumps(
                {
                    "sessionId": "gem-b",
                    "messages": [
                        {"type": "user", "content": [{"text": "manual"}]},
                        {"type": "gemini", "content": "ok"},
                    ],
                }
            ),
            encoding="utf-8",
        )

        snapshot = snapshot_gemini_sessions(
            "/repo",
            content_hint="Gemini Polycule marker",
            projects_file=projects_file,
            tmp_dir=tmp_dir,
        )

        self.assertEqual("repo", gemini_project_slug("/repo", projects_file=projects_file, tmp_dir=tmp_dir))
        self.assertEqual(["gem-a"], sorted(snapshot.keys()))
        self.assertTrue(gemini_session_exists("/repo", "gem-a", projects_file=projects_file, tmp_dir=tmp_dir))
        self.assertEqual(
            "gem-a",
            newest_gemini_session_id(
                "/repo",
                content_hint="Gemini Polycule marker",
                projects_file=projects_file,
                tmp_dir=tmp_dir,
            ),
        )


if __name__ == "__main__":
    unittest.main()
