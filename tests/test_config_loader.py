import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from config_loader import load_config


class ConfigLoaderTests(unittest.TestCase):
    def test_loads_project_config_and_env_overrides(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / 'polycule.toml').write_text(
                '''
[operator]
name = "Local"
room = "Ops"

[hub]
host = "127.0.0.1"
port = 8888
hub_timeout = 3.5

[agents.codex]
enabled = true
mode = "handoff"

[agents.claude]
enabled = false
mode = "mention"

[autocomplete]
max_file_candidates = 12
show_hidden = false
''',
                encoding='utf-8',
            )

            cfg = load_config(
                project,
                env={
                    'POLYCULE_OPERATOR_NAME': 'EnvName',
                    'POLYCULE_HUB_PORT': '9999',
                },
            )

        self.assertEqual('EnvName', cfg.operator.name)
        self.assertEqual('Ops', cfg.operator.room)
        self.assertEqual(9999, cfg.hub.port)
        self.assertEqual('handoff', cfg.mode_for('codex'))
        self.assertIn('claude', cfg.disabled_agent_names)
        self.assertEqual(12, cfg.autocomplete.max_file_candidates)
        self.assertFalse(cfg.autocomplete.show_hidden)


if __name__ == '__main__':
    unittest.main()
