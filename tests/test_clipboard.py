"""System clipboard writes.

The TUI runs inside tmux for most of its life, and tmux swallows the OSC 52
escape Textual's own `copy_to_clipboard` emits — the copy silently vanishes.
So we shell out to a real clipboard tool when one exists, and keep OSC 52
only as the last resort (a remote terminal with no local clipboard).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from gtd import clipboard


@pytest.mark.parametrize(
    ('available', 'expected'),
    [
        (['wl-copy', 'xclip', 'clip.exe'], 'wl-copy'),
        (['xclip', 'clip.exe'], 'xclip'),
        (['clip.exe'], 'clip.exe'),
    ],
)
def test_picks_the_first_available_tool(
    monkeypatch: pytest.MonkeyPatch,
    available: list[str],
    expected: str,
) -> None:
    monkeypatch.setattr(
        clipboard.shutil,
        'which',
        lambda name: f'/usr/bin/{name}' if name in available else None,
    )
    run = MagicMock(return_value=MagicMock(returncode=0))
    monkeypatch.setattr(clipboard.subprocess, 'run', run)

    assert clipboard.copy_text('hello') is True
    assert run.call_args.args[0][0] == expected
    assert run.call_args.kwargs['input'] == b'hello'


def test_never_captures_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """Capturing makes wl-copy/xclip hang until the timeout — a frozen TUI."""
    monkeypatch.setattr(
        clipboard.shutil, 'which', lambda name: f'/usr/bin/{name}'
    )
    run = MagicMock(return_value=MagicMock(returncode=0))
    monkeypatch.setattr(clipboard.subprocess, 'run', run)

    clipboard.copy_text('hello')
    kwargs = run.call_args.kwargs
    assert not kwargs.get('capture_output')
    assert kwargs['stdout'] is clipboard.subprocess.DEVNULL
    assert kwargs['stderr'] is clipboard.subprocess.DEVNULL


def test_returns_false_when_no_tool_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(clipboard.shutil, 'which', lambda name: None)  # noqa: ARG005
    assert clipboard.copy_text('hello') is False


def test_returns_false_when_the_tool_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        clipboard.shutil, 'which', lambda name: f'/usr/bin/{name}'
    )
    monkeypatch.setattr(
        clipboard.subprocess,
        'run',
        MagicMock(side_effect=OSError('boom')),
    )
    assert clipboard.copy_text('hello') is False


def test_tui_falls_back_to_osc52(monkeypatch: pytest.MonkeyPatch) -> None:
    """No local tool → Textual's OSC 52 still gets a chance."""
    from gtd import gtd_tui

    monkeypatch.setattr(gtd_tui.clipboard, 'copy_text', lambda _: False)
    app = MagicMock()
    widget = gtd_tui.BaseEntryContent.__new__(gtd_tui.BaseEntryContent)
    monkeypatch.setattr(
        type(widget), 'app', property(lambda _: app), raising=False
    )
    widget._current_entry = _fake_entry  # noqa: SLF001
    widget._notes = {}  # noqa: SLF001
    widget.action_copy_context()
    app.copy_to_clipboard.assert_called_once()


def test_tui_skips_osc52_when_a_tool_worked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gtd import gtd_tui

    monkeypatch.setattr(gtd_tui.clipboard, 'copy_text', lambda _: True)
    app = MagicMock()
    widget = gtd_tui.BaseEntryContent.__new__(gtd_tui.BaseEntryContent)
    monkeypatch.setattr(
        type(widget), 'app', property(lambda _: app), raising=False
    )
    widget._current_entry = _fake_entry  # noqa: SLF001
    widget._notes = {}  # noqa: SLF001
    widget.action_copy_context()
    app.copy_to_clipboard.assert_not_called()


def _fake_entry() -> object:
    from gtd.notion.models import ProjectEntry

    return ProjectEntry(
        page_id='page-1',
        header='Ship the thing',
        status='Current Project',
        context='@Computer',
        next_step='Draft spec',
        success_condition='',
        due_date=None,
        follow_up_date=None,
        created_date='',
    )
