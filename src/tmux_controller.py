#!/usr/bin/env python3
"""Quantum-Entangled Tmux Controller for Polycule"""
import subprocess
import asyncio
import json
import time
from typing import List, Dict, Optional, Any, Tuple

class TmuxError(Exception):
    """Raised when tmux command fails"""
    pass

class PaneInfo:
    def __init__(self, data: dict):
        self.id = data.get('id', '')
        self.index = int(data.get('index', -1))
        self.window_id = data.get('window_id', '')
        self.window_name = data.get('window_name', '')
        self.window_active = bool(data.get('window_active', False))
        self.window_index = int(data.get('window_index', -1))
        self.pane_active = bool(data.get('pane_active', False))
        self.pane_width = int(data.get('pane_width', 0))
        self.pane_height = int(data.get('pane_height', 0))
        self.pane_title = data.get('pane_title', '')
        self.pane_pid = int(data.get('pane_pid', 0))
        self.pane_current_command = data.get('pane_current_command', '')
        self.pane_current_path = data.get('pane_current_path', '')
        self.pane_dead = bool(data.get('pane_dead', False))

    def to_dict(self) -> dict:
        return vars(self)

class TmuxController:
    def __init__(self, session_name: str = "polycule"):
        self.session_name = session_name
        self._ensure_session()

    def _ensure_session(self):
        """Create tmux session if it doesn't exist"""
        if not self._session_exists():
            self._run_tmux(['new-session', '-d', '-s', self.session_name])
            print(f"Created tmux session: {self.session_name}")

    def _session_exists(self) -> bool:
        """Check if tmux session exists"""
        try:
            result = self._run_tmux(['list-sessions', '-F', '#{session_name}'], check=False)
            return self.session_name in result.stdout.strip().split('\n')
        except:
            return False

    def _run_tmux(self, args: list, **kwargs) -> subprocess.CompletedProcess:
        """Execute tmux command"""
        cmd = ['tmux'] + [str(arg) for arg in args]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise TmuxError(f"Command failed:", result.stderr)
        return result

    def create_pane(self, command: Optional[str] = None, name: str = "agent", target: str = ":0.0") -> PaneInfo:
        """Create new pane in target window"""
        window_ref = f"{self.session_name}"
        args = ['split-window', '-t', window_ref, '-h', '-c', command or 'bash']
        self._run_tmux(args)
        return self.list_panes()[-1]

    def send_keys(self, pane_id: str, keys: str, enter: bool = True):
        """Send keys to specific pane"""
        args = ['send-keys', '-t', pane_id]
        args.extend(['-l', keys])
        if enter:
            args.append('Enter')
        self._run_tmux(args)

    def list_panes(self, window_target: str = "0") -> List[PaneInfo]:
        """Get list of all panes in session"""
        fmt = '#{pane_id}:#{pane_index}:#{window_id}:#{window_name}:#{window_active}:#{window_index}:#{pane_active}:#{pane_width}:#{pane_height}:#{pane_title}:#{pane_pid}:#{pane_current_command}:#{pane_current_path}:#{pane_dead}'
        args = ['list-panes', '-a', '-F', fmt]
        result = self._run_tmux(args, check=False)
        
        panes = []
        for line in result.stdout.strip().split('\n'):
            if not line:
                continue
            parts = line.split(':')
            if len(parts) >= 14:
                panes.append(PaneInfo({
                    'id': parts[0],
                    'index': parts[1],
                    'window_id': parts[2],
                    'window_name': parts[3],
                    'window_active': parts[4],
                    'window_index': parts[5],
                    'pane_active': parts[6],
                    'pane_width': parts[7],
                    'pane_height': parts[8],
                    'pane_title': parts[9],
                    'pane_pid': parts[10],
                    'pane_current_command': parts[11],
                    'pane_current_path': parts[12],
                    'pane_dead': parts[13]
                }))
        return panes

    def kill_pane(self, pane_id: str):
        """Kill specific pane"""
        self._run_tmux(['kill-pane', '-t', pane_id])

    def rename_pane(self, pane_id: str, title: str):
        """Rename pane title"""
        self._run_tmux(['select-pane', '-t', pane_id, '-T', title])

    def capture_pane(self, pane_id: str, lines: int = 20) -> str:
        """Capture output from pane"""
        result = self._run_tmux(['capture-pane', '-p', '-t', pane_id, '-S', f'-{lines}'])
        return result.stdout

    def set_layout(self, window: str, layout: str = 'tiled'):
        """Set window layout"""
        self._run_tmux(['select-layout', '-t', f'{self.session_name}:{window}', layout])

def main():
    """Demo the controller"""
    print("🐙 Polycule Tmux Controller Demo")
    print("=" * 50)
    
    ctrl = TmuxController(session_name="polycule_demox")
    
    # Show current panes
    panes = ctrl.list_panes()
    print(f"✓ Found {len(panes)} panes")
    
    # Create agent pane
    agent = ctrl.create_pane(command="bash", name="TestAgent", target="0")
    ctrl.rename_pane(agent.id, "TestAgent")
    print(f"✓ Created agent pane: {agent.id}")
    
    # Send command
    ctrl.send_keys(agent.id, "echo 'Hello from Polycule!'")
    print("✓ Sent command")
    
    # Capture output
    output = ctrl.capture_pane(agent.id, lines=5)
    if "Hello from Polycule" in output:
        print("✓ Verified output")
    
    # Clean up
    ctrl.kill_pane(agent.id)
    print("✓ Cleaned up")
    
    print("\n✅ Demo complete!")

if __name__ == "__main__":
    main()
