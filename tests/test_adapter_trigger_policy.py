import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agents.codex_adapter import CodexAdapter
from agents.hermes_adapter import HermesAdapter


def _msg(sender_type: str, sender_name: str, content: str) -> dict:
    return {
        'id': 'm1',
        'type': 'message',
        'sender': {'id': 's1', 'type': sender_type, 'name': sender_name},
        'content': content,
    }


class AdapterTriggerPolicyTests(unittest.TestCase):
    def test_codex_requires_explicit_mentions_for_agent_messages(self):
        adapter = CodexAdapter(name='Codex', room='Demo')

        self.assertTrue(adapter._should_respond(_msg('human', 'maps', 'codex check this')))
        self.assertTrue(adapter._should_respond(_msg('claude', 'Claude', '@codex check this')))
        self.assertFalse(adapter._should_respond(_msg('claude', 'Claude', 'codex check this')))

    def test_always_mode_is_human_only(self):
        adapter = CodexAdapter(name='Codex', room='Demo', always_respond=True)
        self.assertTrue(adapter._should_respond(_msg('human', 'maps', 'anything at all')))
        self.assertFalse(adapter._should_respond(_msg('hermes', 'Wizard', 'anything at all')))

    def test_watch_maps_responds_to_maps_without_mention(self):
        adapter = CodexAdapter(name='Codex', room='Demo')
        adapter._set_watch_state('maps')
        self.assertTrue(adapter._should_respond(_msg('human', 'maps', 'status update')))
        self.assertFalse(adapter._should_respond(_msg('human', 'other', 'status update')))

    def test_always_all_mode_responds_to_all_non_self_messages(self):
        adapter = CodexAdapter(name='Codex', room='Demo', always_all=True)
        self.assertTrue(adapter._should_respond(_msg('hermes', 'Wizard', 'anything at all')))

    def test_wizard_profile_does_not_answer_cassette_mentions(self):
        adapter = HermesAdapter(name='Wizard', profile='wizard', room='Demo')

        self.assertTrue(adapter._should_respond(_msg('human', 'maps', '@wizard check this')))
        self.assertFalse(adapter._should_respond(_msg('human', 'maps', '@cassette check this')))
        self.assertFalse(adapter._should_respond(_msg('claude', 'Claude', 'wizard check this')))


if __name__ == '__main__':
    unittest.main()
