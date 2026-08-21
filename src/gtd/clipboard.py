"""Write text to the system clipboard.

Textual's own `copy_to_clipboard` emits OSC 52, which tmux swallows unless
`allow-passthrough` is on — the copy then fails silently. Shelling out to a
real clipboard tool is the reliable path; OSC 52 stays the fallback for a
terminal with no local clipboard at all (an SSH session, say).

Never capture these tools' output. `wl-copy` and `xclip` both stay resident
to serve the selection, holding the pipes open, so a captured run blocks
until it times out — five frozen seconds per tool.
"""

from __future__ import annotations

import shutil
import subprocess

# First match wins: Wayland, macOS, X11, then WSL's bridge to Windows.
_TOOLS: list[list[str]] = [
    ['wl-copy'],
    ['pbcopy'],
    ['xclip', '-selection', 'clipboard'],
    ['xsel', '--clipboard', '--input'],
    ['clip.exe'],
]


def copy_text(text: str) -> bool:
    """Copy `text`. Returns False when no tool could take it."""
    for cmd in _TOOLS:
        if not shutil.which(cmd[0]):
            continue
        try:
            result = subprocess.run(  # noqa: S603
                cmd,
                input=text.encode(),
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode == 0:
            return True
    return False
