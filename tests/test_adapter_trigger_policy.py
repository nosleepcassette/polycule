import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agents.codex_adapter import CodexAdapter
from agents.claude_adapter import ClaudeAdapter
from agents.gemini_adapter import GeminiAdapter
from agents.hermes_adapter import HermesAdapter
from agents.opencode_adapter import OpenCodeAdapter


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

    def test_codex_handoff_mode_accepts_plaintext_agent_handoffs(self):
        adapter = CodexAdapter(name='Codex', room='Demo', agent_handoffs=True)

        self.assertTrue(adapter._should_respond(_msg('claude', 'Claude', 'codex check this')))

    def test_always_mode_is_human_only(self):
        adapter = CodexAdapter(name='Codex', room='Demo', always_respond=True)
        self.assertTrue(adapter._should_respond(_msg('human', 'maps', 'anything at all')))
        self.assertFalse(adapter._should_respond(_msg('hermes', 'Wizard', 'anything at all')))

    def test_watch_human_responds_to_human_without_mention(self):
        adapter = CodexAdapter(name='Codex', room='Demo')
        adapter._set_watch_state('human')
        self.assertTrue(adapter._should_respond(_msg('human', 'maps', 'status update')))
        self.assertTrue(adapter._should_respond(_msg('human', 'other', 'status update')))
        self.assertFalse(adapter._should_respond(_msg('claude', 'Claude', 'status update')))

    def test_always_all_mode_responds_to_all_non_self_messages(self):
        adapter = CodexAdapter(name='Codex', room='Demo', always_all=True)
        self.assertTrue(adapter._should_respond(_msg('hermes', 'Wizard', 'anything at all')))

    def test_wizard_profile_does_not_answer_cassette_mentions(self):
        adapter = HermesAdapter(name='Wizard', profile='wizard', room='Demo')

        self.assertTrue(adapter._should_respond(_msg('human', 'maps', '@wizard check this')))
        self.assertFalse(adapter._should_respond(_msg('human', 'maps', '@cassette check this')))
        self.assertFalse(adapter._should_respond(_msg('claude', 'Claude', 'wizard check this')))

    def test_wizard_handoff_mode_accepts_plaintext_agent_handoffs(self):
        adapter = HermesAdapter(name='Wizard', profile='wizard', room='Demo', agent_handoffs=True)

        self.assertTrue(adapter._should_respond(_msg('claude', 'Claude', 'wizard check this')))

    def test_hermes_profiles_can_hear_each_other_when_addressed(self):
        adapter = HermesAdapter(name='Cassette', profile='cassette', room='Demo')

        self.assertTrue(adapter._should_respond(_msg('hermes', 'Wizard', '@cassette check this')))

    def test_hermes_ffa_responds_to_other_hermes_profiles_not_self(self):
        adapter = HermesAdapter(name='Cassette', profile='cassette', room='Demo', always_all=True)

        self.assertTrue(adapter._should_respond(_msg('hermes', 'Wizard', 'anything at all')))
        self.assertFalse(adapter._should_respond(_msg('hermes', 'Cassette', 'anything at all')))

    def test_dynamic_hermes_profile_uses_own_triggers(self):
        adapter = HermesAdapter(name='Imp', profile='imp', room='Demo')

        self.assertTrue(adapter._should_respond(_msg('human', 'maps', '@imp check this')))
        self.assertTrue(adapter._should_respond(_msg('human', 'maps', 'imp check this')))
        self.assertFalse(adapter._should_respond(_msg('human', 'maps', '@cassette check this')))

    def test_mode_changed_updates_external_adapter_flags(self):
        for cls, name in (
            (CodexAdapter, 'Codex'),
            (ClaudeAdapter, 'Claude'),
            (OpenCodeAdapter, 'OpenCode'),
            (GeminiAdapter, 'Gemini'),
        ):
            adapter = cls(name=name, room='Demo')
            adapter._on_mode_changed('ffa')
            self.assertTrue(adapter.always_all)
            self.assertFalse(adapter.always_respond)
            self.assertFalse(adapter.agent_handoffs)

            adapter._on_mode_changed('handoff')
            self.assertFalse(adapter.always_all)
            self.assertFalse(adapter.always_respond)
            self.assertTrue(adapter.agent_handoffs)


if __name__ == '__main__':
    unittest.main()
