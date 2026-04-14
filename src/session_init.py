# maps · cassette.help · MIT
"""
Polycule Session Init

Detects the active tmux session (or lets the user pick one via fzf),
then creates the polycule window layout within it.

Default layout:
  Window 'polycule':
    pane 0  human terminal (left)
    pane 1  chat TUI      (right)
  Window 'swarm':
    pane 0  empty worker pane (single pane)
  Window 'backend':
    pane 0  hub log
    pane 1  cassette
    pane 2  wizard
    pane 3  codex
    pane 4  claude
    pane 5  opencode
    pane 6  gemini
"""
import os
import subprocess
import sys
import time
from typing import Dict, Optional


def _tmux(*args, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(['tmux'] + list(args), capture_output=True, text=True, check=check)


def _out(proc: subprocess.CompletedProcess) -> str:
    return proc.stdout.strip()


# ---------------------------------------------------------------------------
# Session detection
# ---------------------------------------------------------------------------

def get_tmux_sessions() -> list:
    """Return list of (name, attached) tuples."""
    result = _tmux('list-sessions', '-F', '#{session_name}:#{session_attached}')
    if result.returncode != 0:
        return []
    sessions = []
    for line in result.stdout.strip().splitlines():
        if ':' in line:
            name, attached = line.rsplit(':', 1)
            sessions.append((name, attached == '1'))
    return sessions


def current_session() -> Optional[str]:
    """Return the tmux session the user is currently in, or None."""
    if not os.environ.get('TMUX'):
        return None
    result = _tmux('display-message', '-p', '#{session_name}')
    if result.returncode == 0:
        return _out(result) or None
    return None


def pick_session(sessions: list) -> Optional[str]:
    """
    Let the user pick a session.
    Uses fzf if available, otherwise numbered prompt.
    Returns session name, or None to create a new one.
    """
    names = [f"{name}{' [active]' if attached else ''}" for name, attached in sessions]
    choices = names + ['[new polycule session]']

    try:
        result = subprocess.run(
            ['fzf', '--prompt', 'tmux session > ', '--height', '40%', '--border'],
            input='\n'.join(choices),
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            chosen = result.stdout.strip()
            if chosen == '[new polycule session]':
                return None
            return chosen.replace(' [active]', '')
    except FileNotFoundError:
        pass

    print("\nAvailable tmux sessions:")
    for i, (name, attached) in enumerate(sessions, 1):
        marker = '  [active]' if attached else ''
        print(f"  {i}. {name}{marker}")
    print(f"  {len(sessions) + 1}. [new polycule session]")

    raw = input("Select session (Enter for first): ").strip()
    if not raw:
        return sessions[0][0]
    try:
        idx = int(raw) - 1
        if idx == len(sessions):
            return None
        return sessions[idx][0]
    except (ValueError, IndexError):
        return sessions[0][0]


# ---------------------------------------------------------------------------
# Layout management
# ---------------------------------------------------------------------------

def window_exists(session: str, window_name: str) -> bool:
    result = _tmux('list-windows', '-t', session, '-F', '#{window_name}')
    if result.returncode != 0:
        return False
    return window_name in result.stdout.splitlines()


def _window_index_map(session: str) -> Dict[str, int]:
    result = _tmux('list-windows', '-t', session, '-F', '#{window_index}:#{window_name}')
    if result.returncode != 0:
        return {}
    index_by_name: Dict[str, int] = {}
    for line in result.stdout.strip().splitlines():
        parts = line.split(':', 1)
        if len(parts) != 2:
            continue
        try:
            index = int(parts[0])
        except ValueError:
            continue
        name = parts[1].strip()
        if name:
            index_by_name[name] = index
    return index_by_name


def _pane_rows(session: str, window_name: str, retries: int = 3) -> list:
    """Return pane metadata rows for a window."""
    for attempt in range(retries):
        result = _tmux(
            'list-panes',
            '-t',
            f'{session}:{window_name}',
            '-F',
            '#{pane_id}:#{pane_left}:#{pane_top}:#{@name}',
        )
        rows = []
        if result.returncode == 0:
            for line in result.stdout.strip().splitlines():
                parts = line.split(':', 3)
                if len(parts) != 4:
                    continue
                pane_id, left, top, label = parts
                try:
                    rows.append(
                        {
                            'pane_id': pane_id,
                            'left': int(left),
                            'top': int(top),
                            'label': label.strip(),
                        }
                    )
                except ValueError:
                    continue
            rows.sort(key=lambda r: (r['left'], r['top']))
            if rows:
                return rows
        if attempt < retries - 1:
            time.sleep(0.05)
    return []


def _label_pane(pane_id: str, label: str, title: str):
    _tmux('select-pane', '-t', pane_id, '-T', title)
    _tmux('set-option', '-p', '-t', pane_id, '@name', label)


def _ensure_window(session: str, window_name: str):
    if window_exists(session, window_name):
        return
    _tmux('new-window', '-t', f'{session}:', '-n', window_name)
    if not window_exists(session, window_name):
        raise RuntimeError(f"Failed creating tmux window '{window_name}' in session '{session}'")


def _kill_extra_panes(session: str, window_name: str, keep_count: int):
    rows = _pane_rows(session, window_name)
    if len(rows) <= keep_count:
        return
    for row in rows[keep_count:]:
        _tmux('kill-pane', '-t', row['pane_id'])


def _ensure_polycule_window(session: str) -> dict:
    """Ensure polycule window exists with exactly human/chat panes."""
    _ensure_window(session, 'polycule')
    rows = _pane_rows(session, 'polycule')
    if not rows:
        raise RuntimeError(f"Window '{session}:polycule' has no panes")

    while len(rows) < 2:
        _tmux('split-window', '-t', rows[0]['pane_id'], '-h')
        rows = _pane_rows(session, 'polycule')

    _kill_extra_panes(session, 'polycule', keep_count=2)
    rows = _pane_rows(session, 'polycule')
    if len(rows) != 2:
        raise RuntimeError("Could not reconcile polycule window to two panes")

    human_pane = rows[0]['pane_id']
    chat_pane = rows[1]['pane_id']
    _label_pane(human_pane, 'human', 'human')
    _label_pane(chat_pane, 'chat', 'chat')
    return {'human': human_pane, 'chat': chat_pane}


def _ensure_swarm_window(session: str) -> dict:
    """Ensure swarm window exists as a single empty pane."""
    _ensure_window(session, 'swarm')
    rows = _pane_rows(session, 'swarm')
    if not rows:
        raise RuntimeError(f"Window '{session}:swarm' has no panes")

    _kill_extra_panes(session, 'swarm', keep_count=1)
    rows = _pane_rows(session, 'swarm')
    if len(rows) != 1:
        raise RuntimeError("Could not reconcile swarm window to one pane")

    swarm_pane = rows[0]['pane_id']
    _label_pane(swarm_pane, 'swarm', 'swarm')
    return {'swarm': swarm_pane}


def _ensure_backend_window(session: str) -> dict:
    """
    Ensure backend window exists with seven persistent panes:
    hub-log, cassette, wizard, codex, claude, opencode, gemini.
    """
    _ensure_window(session, 'backend')
    rows = _pane_rows(session, 'backend')
    if not rows:
        raise RuntimeError(f"Window '{session}:backend' has no panes")

    while len(rows) < 7:
        if len(rows) == 1:
            _tmux('split-window', '-t', rows[0]['pane_id'], '-h')
        else:
            target = max(rows, key=lambda r: (r['left'], r['top']))['pane_id']
            _tmux('split-window', '-t', target, '-v')
        rows = _pane_rows(session, 'backend')
        if not rows:
            raise RuntimeError("Backend pane creation failed")

    _kill_extra_panes(session, 'backend', keep_count=7)
    rows = _pane_rows(session, 'backend')
    if len(rows) != 7:
        raise RuntimeError("Could not reconcile backend window to seven panes")

    hub_pane = rows[0]['pane_id']
    cassette_pane = rows[1]['pane_id']
    wizard_pane = rows[2]['pane_id']
    codex_pane = rows[3]['pane_id']
    claude_pane = rows[4]['pane_id']
    opencode_pane = rows[5]['pane_id']
    gemini_pane = rows[6]['pane_id']

    _label_pane(hub_pane, 'hub-log', 'hub-log')
    _label_pane(cassette_pane, 'cassette', 'cassette')
    _label_pane(wizard_pane, 'wizard', 'wizard')
    _label_pane(codex_pane, 'codex', 'codex')
    _label_pane(claude_pane, 'claude', 'claude')
    _label_pane(opencode_pane, 'opencode', 'opencode')
    _label_pane(gemini_pane, 'gemini', 'gemini')

    return {
        'hub_log': hub_pane,
        'cassette': cassette_pane,
        'wizard': wizard_pane,
        'codex': codex_pane,
        'claude': claude_pane,
        'opencode': opencode_pane,
        'gemini': gemini_pane,
    }


def _enforce_window_order(session: str):
    """
    Keep default browsing order together:
    polycule -> swarm -> backend

    If already consecutive and ordered, leave as-is.
    Otherwise, move these three windows as an ordered block at the end.
    """
    wanted = ['polycule', 'swarm', 'backend']
    index_by_name = _window_index_map(session)
    if any(name not in index_by_name for name in wanted):
        return

    indices = [index_by_name[name] for name in wanted]
    already_ordered = indices[0] < indices[1] < indices[2]
    already_consecutive = indices[0] + 1 == indices[1] and indices[1] + 1 == indices[2]
    if already_ordered and already_consecutive:
        return

    max_index = max(index_by_name.values()) if index_by_name else 0
    base = max_index + 1
    for offset, name in enumerate(wanted):
        _tmux('move-window', '-s', f'{session}:{name}', '-t', f'{session}:{base + offset}')


def setup_polycule_layout(session: str) -> dict:
    """
    Create/reconcile default layout in session.
    Returns pane targets including:
      human, chat, swarm, hub_log, cassette, wizard, codex, claude, opencode, gemini
    """
    panes = {}
    panes.update(_ensure_polycule_window(session))
    panes.update(_ensure_swarm_window(session))
    panes.update(_ensure_backend_window(session))
    _enforce_window_order(session)

    _tmux('select-window', '-t', f'{session}:polycule')
    if 'chat' in panes:
        _tmux('select-pane', '-t', panes['chat'])

    print(
        "  polycule layout ready: "
        "polycule(human|chat) + swarm(single) + backend(hub-log|cassette|wizard|codex|claude|opencode|gemini)"
    )
    return panes


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def init(force_new: bool = False) -> tuple:
    """
    Detect or create session, set up layout.
    Returns (session_name, pane_targets_dict).
    """
    print("Polycule session init")

    session = current_session()
    if session and not force_new:
        print(f"  detected session: {session}")
        panes = setup_polycule_layout(session)
        return session, panes

    sessions = get_tmux_sessions()

    if not sessions or force_new:
        print("  creating new 'polycule' session")
        _tmux('new-session', '-d', '-s', 'polycule', '-n', 'polycule', check=False)
        panes = setup_polycule_layout('polycule')
        return 'polycule', panes

    if len(sessions) == 1 and not force_new:
        session = sessions[0][0]
        print(f"  using existing session: {session}")
        panes = setup_polycule_layout(session)
        return session, panes

    chosen = pick_session(sessions)
    if chosen is None:
        _tmux('new-session', '-d', '-s', 'polycule', '-n', 'polycule', check=False)
        chosen = 'polycule'
        print(f"  created new session: {chosen}")

    panes = setup_polycule_layout(chosen)
    return chosen, panes


if __name__ == '__main__':
    force_new = '--new' in sys.argv
    session_name, pane_targets = init(force_new=force_new)
    print(f"\nReady:")
    print(f"  session: {session_name}")
    for label, pane_id in pane_targets.items():
        print(f"  {label}: {pane_id}")
    print(f"\nAttach: tmux attach -t {session_name}")
