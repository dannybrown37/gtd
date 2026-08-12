"""The CLI reschedules the way the TUI does — one decision, one question.

`_log_and_reschedule_entry` opened `$EDITOR` on the notes body before it
would let you reach a date, then applied an inferred date silently. The TUI
replaced that with `_shared_reschedule_only`: infer from the cadence prefix,
confirm it, fall through to a manual prompt if declined. The CLI paths that
still drove the old flow (`gtd log`, its menu entry, and the `gtd today`
action) are gone; `reschedule_only` is what remains.
"""

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from gtd.notion import log as log_module
from gtd.notion.log import reschedule_only
from gtd.notion.models import ProjectEntry


def _entry(header: str = 'Daily: Meditate') -> ProjectEntry:
    return ProjectEntry(
        page_id='page-1',
        header=header,
        status='Recurring',
        context='Home',
        next_step='Sit',
        success_condition='Feel centered',
        due_date=None,
        follow_up_date=None,
        created_date='2026-06-01',
        updated_date='',
    )


def _in_days(days: int) -> str:
    return (datetime.now() + timedelta(days=days)).strftime('%Y-%m-%d')


# --- The old flow is gone, not merely unused ---


class TestLogFlowRemoved:
    @pytest.mark.parametrize(
        'name',
        ['_log_and_reschedule_entry', 'log_and_reschedule', '_infer_cadence'],
    )
    def test_removed_from_log_module(self, name: str):
        assert not hasattr(log_module, name)

    def test_no_gtd_log_command(self):
        from gtd.cli import cli

        assert 'log' not in cli.commands

    def test_menu_has_no_log_and_reschedule_entry(self):
        import inspect

        from gtd.cli import _interactive_menu

        source = inspect.getsource(_interactive_menu)
        assert 'Log & Reschedule' not in source

    def test_today_offers_reschedule_not_log(self):
        import inspect

        from gtd.notion.today import list_today

        source = inspect.getsource(list_today)
        assert 'Log & Reschedule' not in source
        assert 'Reschedule' in source

    def test_rescheduling_never_touches_the_page_body(self):
        """No editor, no body rewrite — that was the whole complaint."""
        import inspect

        source = inspect.getsource(reschedule_only)
        for forbidden in ('EDITOR', 'subprocess', 'replace_page_body'):
            assert forbidden not in source


# --- reschedule_only: infer, confirm, fall through ---


class TestRescheduleOnly:
    @pytest.mark.parametrize('answer', ['', 'y', 'Y', 'yes'])
    def test_accepting_inferred_date_asks_nothing_else(self, answer: str):
        entry = _entry('Daily: Meditate')
        with (
            patch.object(log_module, 'prompt_input', return_value=answer) as p,
            patch.object(log_module, 'update_page') as update,
        ):
            assert reschedule_only(entry) == _in_days(1)

        assert p.call_count == 1
        assert _in_days(1) in p.call_args[0][0]
        page_id, props = update.call_args[0]
        assert page_id == 'page-1'
        assert props['Follow-Up Date']['date']['start'] == _in_days(1)

    def test_declining_inferred_date_falls_through_to_manual(self):
        entry = _entry('Weekly: Review')
        with (
            patch.object(
                log_module, 'prompt_input', side_effect=['n', 'tomorrow']
            ),
            patch.object(log_module, 'update_page') as update,
        ):
            assert reschedule_only(entry) == _in_days(1)

        assert update.call_count == 1

    def test_no_cadence_prefix_prompts_directly(self):
        entry = _entry('Call the dentist')
        with (
            patch.object(
                log_module, 'prompt_input', return_value='tomorrow'
            ) as p,
            patch.object(log_module, 'update_page'),
        ):
            assert reschedule_only(entry) == _in_days(1)

        assert p.call_count == 1
        assert 'Reschedule to' in p.call_args[0][0]

    @pytest.mark.parametrize('reply', ['', None])
    def test_empty_manual_date_cancels_without_writing(
        self, reply: str | None
    ):
        entry = _entry('Call the dentist')
        with (
            patch.object(log_module, 'prompt_input', return_value=reply),
            patch.object(log_module, 'update_page') as update,
        ):
            assert reschedule_only(entry) is None

        update.assert_not_called()

    def test_unparseable_manual_date_cancels_without_writing(self):
        entry = _entry('Call the dentist')
        with (
            patch.object(
                log_module, 'prompt_input', return_value='not a date at all'
            ),
            patch.object(log_module, 'update_page') as update,
        ):
            assert reschedule_only(entry) is None

        update.assert_not_called()

    def test_declining_then_cancelling_writes_nothing(self):
        entry = _entry('Daily: Meditate')
        with (
            patch.object(log_module, 'prompt_input', side_effect=['n', '']),
            patch.object(log_module, 'update_page') as update,
        ):
            assert reschedule_only(entry) is None

        update.assert_not_called()
