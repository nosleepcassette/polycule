import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import hermes_sessions
import managed_agents


class ManagedAgentDiscoveryTests(unittest.TestCase):
    def test_discover_hermes_profiles_from_home(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            hermes_home = Path(tmpdir) / ".hermes"
            (hermes_home / "profiles" / "analyst").mkdir(parents=True)
            (hermes_home / "profiles" / "wizard").mkdir(parents=True)

            with (
                patch.object(managed_agents, "HERMES_HOME", hermes_home),
                patch.object(managed_agents, "HERMES_BIN", hermes_home / "bin" / "hermes"),
                patch.dict("managed_agents.os.environ", {}, clear=True),
            ):
                self.assertEqual(
                    ["default", "analyst", "wizard"],
                    managed_agents.discover_hermes_profiles(),
                )

    def test_get_managed_agents_exposes_default_hermes_profile_as_hermes(self):
        with (
            patch("managed_agents.hermes_available", return_value=(True, "/usr/bin/hermes")),
            patch("managed_agents.discover_hermes_profiles", return_value=["default", "analyst"]),
            patch("managed_agents.external_agent_available", return_value=(False, "missing")),
            patch.dict(
                "managed_agents.os.environ",
                {
                    "POLYCULE_EXTERNAL_AGENTS": "",
                    "POLYCULE_HERMES_DEFAULT_NAME": "hermes",
                    "POLYCULE_INCLUDE_UNAVAILABLE_EXTERNALS": "0",
                    "POLYCULE_INCLUDE_UNAVAILABLE_HERMES": "0",
                },
                clear=True,
            ),
        ):
            agents = managed_agents.get_managed_agents()

        names = [agent.name for agent in agents]
        self.assertEqual(["hermes", "codex", "claude", "opencode", "gemini"], names[:5])
        self.assertIn("analyst", names)
        self.assertEqual("default", agents[0].profile)
        self.assertEqual("always", agents[0].default_mode)
        self.assertEqual("mention", next(agent.default_mode for agent in agents if agent.name == "analyst"))

    def test_default_hermes_sessions_live_under_root_home(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            hermes_home = Path(tmpdir) / ".hermes"
            with patch.object(hermes_sessions, "HERMES_HOME", hermes_home):
                self.assertEqual(
                    hermes_home / "sessions",
                    hermes_sessions.hermes_session_dir("default"),
                )
                self.assertEqual(
                    hermes_home / "sessions",
                    hermes_sessions.hermes_session_dir("hermes"),
                )
                self.assertEqual(
                    hermes_home / "profiles" / "analyst" / "sessions",
                    hermes_sessions.hermes_session_dir("analyst"),
                )


if __name__ == "__main__":
    unittest.main()
