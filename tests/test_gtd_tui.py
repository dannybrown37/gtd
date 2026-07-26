import asyncio
from unittest.mock import MagicMock, patch

import httpx

from gtd.gtd_tui import (
    _classify_network_error,
    _open_steps_editor,
    _render_entry_detail,
    _render_entry_summary,
)
from gtd.notion.models import ProjectEntry
from gtd.notion.schema import STATUS_ICONS


def _entry(**kwargs) -> ProjectEntry:
    defaults = {
        'page_id': 'abc123',
        'header': 'Buy milk',
        'status': 'Current Project',
        'context': 'Home',
        'next_step': 'Go to store',
        'success_condition': 'Fridge fully stocked',
        'due_date': None,
        'follow_up_date': None,
        'created_date': '2026-07-01T00:00:00',
        'updated_date': '',
    }
    return ProjectEntry(**{**defaults, **kwargs})


class TestRenderEntryDetail:
    def test_shows_header(self):
        result = _render_entry_detail(_entry(header='Buy milk'))
        assert 'Buy milk' in result

    def test_shows_status(self):
        result = _render_entry_detail(_entry(status='Current Project'))
        assert 'Current Project' in result

    def test_shows_context(self):
        result = _render_entry_detail(_entry(context='Work'))
        assert 'Work' in result

    def test_shows_next_step(self):
        result = _render_entry_detail(_entry(next_step='Write tests'))
        assert 'Write tests' in result

    def test_shows_due_date_when_present(self):
        result = _render_entry_detail(_entry(due_date='2026-07-20'))
        assert '2026-07-20' in result

    def test_no_due_line_when_absent(self):
        result = _render_entry_detail(_entry(due_date=None))
        assert 'Due' not in result

    def test_shows_follow_up_when_present(self):
        result = _render_entry_detail(_entry(follow_up_date='2026-07-25'))
        assert '2026-07-25' in result

    def test_loading_state_when_notes_none(self):
        result = _render_entry_detail(_entry(), notes=None)
        assert 'Loading' in result

    def test_shows_notes_content(self):
        result = _render_entry_detail(_entry(), notes='Important context here')
        assert 'Important context here' in result

    def test_no_notes_message_when_empty(self):
        result = _render_entry_detail(_entry(), notes='')
        assert 'No notes' in result

    def test_status_icon_in_output(self):
        result = _render_entry_detail(_entry(status='Current Project'))
        assert STATUS_ICONS['Current Project'] in result

    def test_triage_icon(self):
        result = _render_entry_detail(_entry(status='Triage'))
        assert STATUS_ICONS['Triage'] in result

    def test_empty_next_step_shows_none(self):
        result = _render_entry_detail(_entry(next_step=''))
        assert '(none)' in result

    def test_shows_success_condition(self):
        result = _render_entry_detail(
            _entry(success_condition='Ship the feature')
        )
        assert 'Ship the feature' in result

    def test_empty_success_condition_shows_none(self):
        result = _render_entry_detail(_entry(success_condition=''))
        assert '(none)' in result

    def test_multiline_notes_shown(self):
        result = _render_entry_detail(_entry(), notes='Line 1\nLine 2\nLine 3')
        assert 'Line 1' in result
        assert 'Line 3' in result


class TestRenderEntrySummary:
    def test_shows_header(self):
        result = _render_entry_summary(_entry(header='Buy milk'))
        assert 'Buy milk' in result

    def test_shows_context(self):
        result = _render_entry_summary(_entry(context='Work'))
        assert 'Work' in result

    def test_shows_status_icon(self):
        result = _render_entry_summary(_entry(status='Current Project'))
        assert STATUS_ICONS['Current Project'] in result

    def test_shows_due_date_when_present(self):
        result = _render_entry_summary(_entry(due_date='2026-07-20'))
        assert 'Jul 20' in result or '2026-07-20' in result

    def test_shows_next_step(self):
        result = _render_entry_summary(_entry(next_step='Write tests'))
        assert 'Write tests' in result


class TestClassifyNetworkError:
    def test_read_timeout_returns_warning(self):
        msg, severity = _classify_network_error(
            httpx.ReadTimeout('timed out'),
        )
        assert 'timed out' in msg.lower()
        assert severity == 'warning'

    def test_connect_timeout_returns_warning(self):
        msg, severity = _classify_network_error(
            httpx.ConnectTimeout('timed out')
        )
        assert severity == 'warning'
        assert msg

    def test_request_error_returns_error_severity(self):
        msg, severity = _classify_network_error(
            httpx.ConnectError('connection refused')
        )
        assert severity == 'error'
        assert msg

    def test_unrelated_exception_returns_empty(self):
        msg, severity = _classify_network_error(ValueError('something else'))
        assert msg == ''
        assert severity == ''

    def test_non_network_does_not_swallow(self):
        msg, _ = _classify_network_error(RuntimeError('boom'))
        assert msg == ''


class TestOpenStepsEditor:
    def _run(self, monkeypatch, editor='vim') -> list[str]:
        monkeypatch.setenv('EDITOR', editor)
        fake_app = MagicMock()
        fake_app.suspend.return_value.__enter__ = MagicMock(return_value=None)
        fake_app.suspend.return_value.__exit__ = MagicMock(return_value=False)

        captured = {}

        def fake_run(args, check=False) -> None:  # noqa: ARG001, FBT002
            captured['args'] = args

        with patch('gtd.gtd_tui.subprocess.run', side_effect=fake_run):
            asyncio.run(_open_steps_editor(fake_app))
        return captured['args']

    def test_opens_editor_at_last_line(self, monkeypatch):
        args = self._run(monkeypatch)
        assert args[0] == 'vim'
        assert args[1] == '+'
        assert args[2].endswith('.md')
