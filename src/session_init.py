# maps · cassette.help · MIT
"""
Polycule Session Init

Detects maps' active tmux session (or lets her pick one via fzf),
then creates the polycule window layout within it.

Layout:
  Window 'polycule':
    pane 0  maps terminal (left half)
    pane 1  chat TUI     (right top)
    pane 2  hub log      (right bottom)
  Window 'swarm':
    empty — agent panes spawn here
"""
import os
import subprocess
import sys
from typing import Optional


def _tmux(*args, check=False) -> subprocess.CompletedProcess:
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
    """Return the tmux session maps is currently in, or None."""
    if not os.environ.get('TMUX'):
        return None
    result = _tmux('display-message', '-p', '#{session_name}')
    if result.returncode == 0:
        return _out(result) or None
    return None


def pick_session(sessions: list) -> Optional[str]:
    """
    Let maps pick a session.
    Uses fzf if available, otherwise numbered prompt.
    Returns session name, or None to create a new one.
    """
    names = [f"{name}{' [active]' if attached else ''}" for name, attached in sessions]
    choices = names + ['[new polycule session]']

    # Try fzf
    try:
        result = subprocess.run(
            ['fzf', '--prompt', 'tmux session > ', '--height', '40%', '--border'],
            input='\n'.join(choices),
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            chosen = result.stdout.strip()
            if chosen == '[new polycule session]':
                return None
            # Strip the [active] suffix
            return chosen.replace(' [active]', '')
    except FileNotFoundError:
        pass

    # Fallback: numbered list
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

def polycule_window_exists(session: str) -> bool:
    result = _tmux('list-windows', '-t', session, '-F', '#{window_name}')
    return 'polycule' in result.stdout.splitlines()


def swarm_window_exists(session: str) -> bool:
    result = _tmux('list-windows', '-t', session, '-F', '#{window_name}')
    return 'swarm' in result.stdout.splitlines()


def setup_polycule_layout(session: str) -> dict:
    """
    Create polycule and swarm windows in session.
    Returns pane targets: {'maps': target, 'chat': target, 'hub_log': target, 'swarm': target}
    """
    panes = {}

    if polycule_window_exists(session):
        print(f"  polycule window already exists in '{session}', reusing")
        # Find existing panes by label
        result = _tmux('list-panes', '-t', f'{session}:polycule', '-F',
                       '#{pane_id} #{@name}')
        for line in result.stdout.strip().splitlines():
            parts = line.split(None, 1)
            if len(parts) == 2:
                pane_id, label = parts
                if label in ('maps', 'chat', 'hub-log'):
                    panes[label.replace('-', '_')] = pane_id
        return panes

    # Create polycule window
    _tmux('new-window', '-t', session, '-n', 'polycule')

    # Split: pane 0 (left, maps) | pane 1 (right, will split again)
    _tmux('split-window', '-t', f'{session}:polycule', '-h')

    # Split right half vertically: pane 1 (chat top) | pane 2 (hub log bottom)
    _tmux('split-window', '-t', f'{session}:polycule.1', '-v', '-p', '30')

    # Label panes
    result = _tmux('list-panes', '-t', f'{session}:polycule', '-F', '#{pane_id}')
    pane_ids = result.stdout.strip().splitlines()

    if len(pane_ids) >= 3:
        maps_pane, chat_pane, log_pane = pane_ids[0], pane_ids[1], pane_ids[2]
        _tmux('select-pane', '-t', maps_pane, '-T', 'maps')
        _tmux('select-pane', '-t', chat_pane, '-T', 'chat')
        _tmux('select-pane', '-t', log_pane, '-T', 'hub-log')
        _tmux('set-option', '-p', '-t', maps_pane, '@name', 'maps')
        _tmux('set-option', '-p', '-t', chat_pane, '@name', 'chat')
        _tmux('set-option', '-p', '-t', log_pane, '@name', 'hub-log')
        panes = {'maps': maps_pane, 'chat': chat_pane, 'hub_log': log_pane}
    else:
        panes = {}

    # Create swarm window (if missing)
    if not swarm_window_exists(session):
        _tmux('new-window', '-t', session, '-n', 'swarm')
        result = _tmux('list-panes', '-t', f'{session}:swarm', '-F', '#{pane_id}')
        swarm_ids = result.stdout.strip().splitlines()
        if swarm_ids:
            _tmux('set-option', '-p', '-t', swarm_ids[0], '@name', 'swarm')
            panes['swarm'] = swarm_ids[0]

    # Focus chat pane
    _tmux('select-window', '-t', f'{session}:polycule')
    if 'chat' in panes:
        _tmux('select-pane', '-t', panes['chat'])

    print(f"  polycule layout ready: maps | chat | hub-log  +  swarm window")
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

    # Already inside tmux?
    session = current_session()
    if session and not force_new:
        print(f"  detected session: {session}")
        panes = setup_polycule_layout(session)
        return session, panes

    sessions = get_tmux_sessions()

    if not sessions or force_new:
        print("  creating new 'polycule' session")
        _tmux('new-session', '-d', '-s', 'polycule', check=False)
        panes = setup_polycule_layout('polycule')
        return 'polycule', panes

    if len(sessions) == 1 and not force_new:
        session = sessions[0][0]
        print(f"  using existing session: {session}")
        panes = setup_polycule_layout(session)
        return session, panes

    # Multiple sessions → picker
    chosen = pick_session(sessions)
    if chosen is None:
        _tmux('new-session', '-d', '-s', 'polycule', check=False)
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
