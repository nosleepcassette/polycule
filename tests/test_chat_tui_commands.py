import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ui.chat_tui import ChatTUI


class ChatTUICommandTests(unittest.IsolatedAsyncioTestCase):
    def _walker_text(self, tui: ChatTUI) -> list[str]:
        out = []
        for widget in tui.walker:
            try:
                out.append(widget._w.get_text()[0])
            except Exception:
                continue
        return out

    async def test_slash_help_prints_command_summary(self):
        tui = ChatTUI(name='maps', room='Demo', host='localhost', port=7777)
        await tui._handle_slash('help')
        joined = '\n'.join(self._walker_text(tui)).lower()
        self.assertIn('slash commands:', joined)
        self.assertIn('/disable <agent>', joined)
        self.assertIn('press tab', joined)

    async def test_slash_ahelp_alias_prints_command_summary(self):
        tui = ChatTUI(name='maps', room='Demo', host='localhost', port=7777)
        await tui._handle_slash('ahelp')
        joined = '\n'.join(self._walker_text(tui)).lower()
        self.assertIn('slash commands:', joined)
        self.assertIn('/mode <agent> <mention|always|off>', joined)

    async def test_tab_completion_completes_enable(self):
        tui = ChatTUI(name='maps', room='Demo', host='localhost', port=7777)
        tui.edit.set_edit_text('/en')
        tui.edit.edit_pos = len('/en')
        ok = tui._complete_slash_command()
        self.assertTrue(ok)
        self.assertEqual('/enable ', tui.edit.get_edit_text())

    async def test_tab_completion_completes_agent_argument(self):
        tui = ChatTUI(name='maps', room='Demo', host='localhost', port=7777)
        tui.edit.set_edit_text('/disable cl')
        tui.edit.edit_pos = len('/disable cl')
        ok = tui._complete_slash_command()
        self.assertTrue(ok)
        self.assertEqual('/disable claude', tui.edit.get_edit_text())

    async def test_tab_completion_completes_mode_arguments(self):
        tui = ChatTUI(name='maps', room='Demo', host='localhost', port=7777)
        tui.edit.set_edit_text('/mode wi')
        tui.edit.edit_pos = len('/mode wi')
        ok = tui._complete_slash_command()
        self.assertTrue(ok)
        self.assertEqual('/mode wizard ', tui.edit.get_edit_text())

        tui.edit.set_edit_text('/mode wizard a')
        tui.edit.edit_pos = len('/mode wizard a')
        ok = tui._complete_slash_command()
        self.assertTrue(ok)
        self.assertEqual('/mode wizard always', tui.edit.get_edit_text())

    async def test_tab_completion_completes_watch_arguments(self):
        tui = ChatTUI(name='maps', room='Demo', host='localhost', port=7777)
        tui.edit.set_edit_text('/watch wi')
        tui.edit.edit_pos = len('/watch wi')
        ok = tui._complete_slash_command()
        self.assertTrue(ok)
        self.assertEqual('/watch wizard ', tui.edit.get_edit_text())

        tui.edit.set_edit_text('/watch wizard ro')
        tui.edit.edit_pos = len('/watch wizard ro')
        ok = tui._complete_slash_command()
        self.assertTrue(ok)
        self.assertEqual('/watch wizard room', tui.edit.get_edit_text())

    async def test_tab_completion_completes_theme_argument(self):
        tui = ChatTUI(name='maps', room='Demo', host='localhost', port=7777)
        tui.edit.set_edit_text('/theme ph')
        tui.edit.edit_pos = len('/theme ph')
        ok = tui._complete_slash_command()
        self.assertTrue(ok)
        self.assertEqual('/theme phosphor', tui.edit.get_edit_text())

    async def test_approve_completion_uses_pending_request_ids(self):
        tui = ChatTUI(name='maps', room='Demo', host='localhost', port=7777)
        await tui._handle_incoming({
            'type': 'approval_request',
            'request_id': 'abc123',
            'requester': 'wizard',
            'command': 'split_window',
        })
        tui.edit.set_edit_text('/approve a')
        tui.edit.edit_pos = len('/approve a')
        ok = tui._complete_slash_command()
        self.assertTrue(ok)
        self.assertEqual('/approve abc123', tui.edit.get_edit_text())

    async def test_mode_command_calls_cli(self):
        tui = ChatTUI(name='maps', room='Demo', host='localhost', port=7777)
        calls = []

        async def fake_run(*args):
            calls.append(args)
            return 0, 'Set mode wizard=always', ''

        tui._run_polycule_cli = fake_run  # type: ignore[assignment]
        await tui._handle_slash('mode wizard always')
        self.assertEqual([('agent', 'mode', 'wizard', 'always')], calls)
        self.assertIn('set mode wizard=always', self._walker_text(tui)[-1].lower())

    async def test_agent_status_system_event_renders(self):
        tui = ChatTUI(name='maps', room='Demo', host='localhost', port=7777)
        before = len(tui.walker)
        await tui._handle_incoming({
            'type': 'system',
            'action': 'agent_status',
            'agent': {'name': 'wizard', 'type': 'hermes'},
            'status': 'timeout',
            'detail': 'wizard profile timed out after 240.0s',
        })
        self.assertEqual(before + 1, len(tui.walker))
        self.assertIn('[wizard] timeout', self._walker_text(tui)[-1].lower())

    async def test_agent_session_system_event_renders(self):
        tui = ChatTUI(name='maps', room='Demo', host='localhost', port=7777)
        before = len(tui.walker)
        await tui._handle_incoming({
            'type': 'system',
            'action': 'agent_session',
            'agent_name': 'cassette',
            'state': 'created',
            'session_id': 'sess-123',
            'session_title': 'chat',
        })
        self.assertEqual(before + 1, len(tui.walker))
        self.assertIn('cassette session ready', self._walker_text(tui)[-1].lower())
        self.assertIn('sess-123', self._walker_text(tui)[-1])

    async def test_theme_command_applies_requested_theme(self):
        tui = ChatTUI(name='maps', room='Demo', host='localhost', port=7777)
        calls = []

        def fake_apply(theme_name: str, *, persist: bool = False) -> str:
            calls.append((theme_name, persist))
            return 'amber'

        tui._apply_theme = fake_apply  # type: ignore[assignment]
        await tui._handle_slash('theme phosphor')
        self.assertEqual([('phosphor', True)], calls)
        self.assertIn('theme: amber', self._walker_text(tui)[-1].lower())

    async def test_rollcall_sends_mentions_for_active_agents(self):
        tui = ChatTUI(name='maps', room='Demo', host='localhost', port=7777)
        tui.room_id = 'room-1'
        sent = []

        async def fake_cli(*args):
            self.assertEqual(('agent', 'status'), args)
            return (
                0,
                '\n'.join(
                    [
                        'Enabled: cassette, wizard, codex, claude',
                        'Disabled: opencode, gemini',
                        'cassette: state=enabled mode=always',
                        'wizard: state=enabled mode=always',
                        'codex: state=enabled mode=always',
                        'claude: state=enabled mode=mention',
                        'opencode: state=disabled mode=mention',
                        'gemini: state=disabled mode=mention',
                    ]
                ),
                '',
            )

        async def fake_send(payload):
            sent.append(payload)
            return True

        tui._run_polycule_cli = fake_cli  # type: ignore[assignment]
        tui._send = fake_send  # type: ignore[assignment]

        await tui._handle_slash('rollcall')

        self.assertEqual(1, len(sent))
        self.assertIn('@cassette', sent[0]['content'])
        self.assertIn('@wizard', sent[0]['content'])
        self.assertIn('@codex', sent[0]['content'])
        self.assertIn('@claude', sent[0]['content'])
        self.assertNotIn('@opencode', sent[0]['content'])
        self.assertIn('rollcall: cassette, wizard, codex, claude', self._walker_text(tui)[-1].lower())

    async def test_which_recommends_codex_for_code_task(self):
        tui = ChatTUI(name='maps', room='Demo', host='localhost', port=7777)

        async def fake_cli(*args):
            self.assertEqual(('agent', 'status'), args)
            return (
                0,
                '\n'.join(
                    [
                        'cassette: state=enabled mode=always',
                        'wizard: state=enabled mode=always',
                        'codex: state=enabled mode=always',
                        'claude: state=enabled mode=mention',
                        'opencode: state=enabled mode=mention',
                        'gemini: state=disabled mode=mention',
                    ]
                ),
                '',
            )

        tui._run_polycule_cli = fake_cli  # type: ignore[assignment]
        await tui._handle_slash('which implement a python test fix')
        joined = '\n'.join(self._walker_text(tui)).lower()
        self.assertIn('which: @codex', joined)
        self.assertIn('fallbacks:', joined)

    async def test_watch_command_sends_hub_command(self):
        tui = ChatTUI(name='maps', room='Demo', host='localhost', port=7777)
        tui.room_id = 'room-1'
        sent = []

        async def fake_send(payload):
            sent.append(payload)
            return True

        tui._send = fake_send  # type: ignore[assignment]
        await tui._handle_slash('watch wizard room')
        self.assertEqual(
            {
                'type': 'command',
                'command': 'set_watch',
                'room_id': 'room-1',
                'watchers': ['wizard'],
                'scope': 'room',
                'target': '',
            },
            sent[0],
        )

    async def test_brief_command_auto_enables_targets_and_sends_directive(self):
        tui = ChatTUI(name='maps', room='Demo', host='localhost', port=7777)
        tui.room_id = 'room-1'
        sent = []
        calls = []

        async def fake_cli(*args):
            calls.append(args)
            if args == ('agent', 'status'):
                return (
                    0,
                    '\n'.join(
                        [
                            'wizard: state=enabled mode=always',
                            'codex: state=disabled mode=always',
                            'claude: state=enabled mode=mention',
                            'cassette: state=enabled mode=always',
                            'opencode: state=enabled mode=mention',
                            'gemini: state=enabled mode=mention',
                        ]
                    ),
                    '',
                )
            if args == ('agent', 'enable', 'codex'):
                return 0, 'Enabled codex', ''
            raise AssertionError(args)

        async def fake_send(payload):
            sent.append(payload)
            return True

        tui._run_polycule_cli = fake_cli  # type: ignore[assignment]
        tui._send = fake_send  # type: ignore[assignment]
        tui._mark_temporary_enablements = lambda agents, reason: None  # type: ignore[assignment]
        await tui._handle_slash('brief wizard codex -- internalize the new docs')

        self.assertIn(('agent', 'status'), calls)
        self.assertIn(('agent', 'enable', 'codex'), calls)
        self.assertEqual('send_directive', sent[0]['command'])
        self.assertEqual(['wizard', 'codex'], sent[0]['targets'])
        self.assertEqual('internalize the new docs', sent[0]['content'])

    async def test_standdown_restores_previous_mode_and_disabled_state(self):
        tui = ChatTUI(name='maps', room='Demo', host='localhost', port=7777)
        tui.room_id = 'room-1'
        sent = []
        calls = []

        async def fake_cli(*args):
            calls.append(args)
            if args == ('agent', 'mode', 'codex', 'off'):
                return 0, 'Set mode codex=off', ''
            if args == ('agent', 'disable', 'codex'):
                return 0, 'Disabled codex', ''
            raise AssertionError(args)

        async def fake_send(payload):
            sent.append(payload)
            return True

        tui._run_polycule_cli = fake_cli  # type: ignore[assignment]
        tui._send = fake_send  # type: ignore[assignment]
        tui._get_temporary_enablements = lambda: {  # type: ignore[assignment]
            'codex': {
                'previous_mode': 'off',
                'previous_state': 'disabled',
            }
        }
        cleared = []
        tui._clear_temporary_enablements = lambda agents=None: cleared.append(list(agents or []))  # type: ignore[assignment]

        await tui._handle_slash('standdown codex')

        self.assertEqual(
            [('agent', 'mode', 'codex', 'off'), ('agent', 'disable', 'codex')],
            calls,
        )
        self.assertEqual([['codex']], cleared)
        self.assertEqual('standdown_agents', sent[0]['command'])
        self.assertEqual(['codex'], sent[0]['auto_disabled'])


if __name__ == '__main__':
    unittest.main()
