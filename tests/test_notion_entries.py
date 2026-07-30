"""Tests for gtd.notion.entries — the Today filter and field editing.

`_today_filter` decides what the Today view asks Notion for. A regression
here is silent: items simply stop appearing, with no error anywhere.
`_collect_field_updates` distinguishes "user skipped this field" from
"user cleared this field", which decides whether a Notion value survives.
"""

from datetime import datetime
from unittest.mock import patch

import pytest

from gtd.notion.entries import (
    _collect_field_updates,
    _entry_preview_text,
    _escape_for_shell,
    _today_filter,
)
from gtd.notion.models import ProjectEntry


def _entry(**overrides) -> ProjectEntry:
    defaults = {
        'page_id': 'page-1',
        'header': 'Ship the thing',
        'status': 'Current Project',
        'context': '@work',
        'next_step': 'Draft the plan',
        'success_condition': 'Shipped',
        'due_date': None,
        'follow_up_date': None,
        'created_date': '2026-06-01',
    }
    return ProjectEntry(**{**defaults, **overrides})


# --- _today_filter: defines what Today asks Notion for ---


class TestTodayFilter:
    def test_matches_only_active_statuses(self):
        status_clause = _today_filter()['and'][0]['or']
        statuses = [c['select']['equals'] for c in status_clause]

        assert statuses == ['Current Project', 'Recurring']

    @pytest.mark.parametrize(
        'excluded',
        ['Triage', 'Waiting For', 'Someday/Maybe', 'List'],
    )
    def test_inactive_statuses_are_not_requested(self, excluded: str):
        """Someday and Waiting For must never leak into Today."""
        status_clause = _today_filter()['and'][0]['or']
        statuses = [c['select']['equals'] for c in status_clause]

        assert excluded not in statuses

    def test_follow_up_clause_admits_due_and_unset_dates(self):
        date_clause = _today_filter()['and'][1]['or']
        today = datetime.now().strftime('%Y-%m-%d')

        assert {
            'property': 'Follow-Up Date',
            'date': {'on_or_before': today},
        } in date_clause
        assert {
            'property': 'Follow-Up Date',
            'date': {'is_empty': True},
        } in date_clause

    def test_future_follow_ups_are_excluded_by_on_or_before(self):
        """Snoozed items stay hidden — the whole point of snoozing."""
        date_clause = _today_filter()['and'][1]['or']
        bounds = [
            c['date']['on_or_before']
            for c in date_clause
            if 'on_or_before' in c['date']
        ]

        assert bounds == [datetime.now().strftime('%Y-%m-%d')]

    def test_status_and_date_clauses_are_anded(self):
        result = _today_filter()

        assert set(result) == {'and'}
        assert len(result['and']) == 2
        assert all('or' in clause for clause in result['and'])


# --- _escape_for_shell: preview text is interpolated into `echo '...'` ---


class TestEscapeForShell:
    @pytest.mark.parametrize(
        ('raw', 'expected'),
        [
            ('no quotes', 'no quotes'),
            ("it's", "it'\\''s"),
            ("'leading", "'\\''leading"),
            ("two's quote's", "two'\\''s quote'\\''s"),
            ('', ''),
        ],
    )
    def test_single_quotes_are_escaped(self, raw: str, expected: str):
        assert _escape_for_shell(raw) == expected

    def test_double_quotes_and_backslashes_pass_through(self):
        """Only single quotes matter inside a single-quoted shell string."""
        assert _escape_for_shell(r'say "hi" \n') == r'say "hi" \n'


# --- _entry_preview_text: the fzf preview pane ---


class TestEntryPreviewText:
    def test_all_fields_are_labelled(self):
        entry = _entry(due_date='2026-07-01', follow_up_date='2026-07-05')
        result = _entry_preview_text(entry)

        assert 'Status:    Current Project' in result
        assert 'Context:   @work' in result
        assert 'Next step: Draft the plan' in result
        assert 'Success condition: Shipped' in result
        assert 'Due:       2026-07-01' in result
        assert 'Follow-up: 2026-07-05' in result

    @pytest.mark.parametrize(
        'field',
        ['context', 'next_step', 'success_condition'],
    )
    def test_empty_fields_render_as_none(self, field: str):
        result = _entry_preview_text(_entry(**{field: ''}))

        assert '(none)' in result

    def test_body_is_appended_under_a_notes_heading(self):
        result = _entry_preview_text(_entry(), body='line one\nline two')

        assert '── Notes ──' in result
        assert '  line one' in result
        assert '  line two' in result

    def test_empty_body_omits_the_notes_heading(self):
        assert '── Notes ──' not in _entry_preview_text(_entry(), body='')

    def test_header_whitespace_is_stripped(self):
        result = _entry_preview_text(_entry(header='  Padded  '))

        assert '── Padded ──' in result


# --- _collect_field_updates: skip vs. clear must stay distinguishable ---


class TestCollectFieldUpdates:
    def test_no_fields_selected_returns_empty(self):
        assert _collect_field_updates(_entry(), []) == {}

    @pytest.mark.parametrize(
        ('field', 'key'),
        [
            ('Name', 'name'),
            ('Next actionable step', 'next_step'),
            ('Success condition', 'success_condition'),
        ],
    )
    def test_text_fields_capture_input(self, field: str, key: str):
        with patch(
            'gtd.notion.entries.prompt_input', return_value='new value'
        ):
            result = _collect_field_updates(_entry(), [field])

        assert result == {key: 'new value'}

    @pytest.mark.parametrize(
        ('field', 'key'),
        [
            ('Name', 'name'),
            ('Next actionable step', 'next_step'),
            ('Success condition', 'success_condition'),
        ],
    )
    def test_cancelled_prompt_omits_the_field(self, field: str, key: str):
        """None means the user aborted — the field must be left untouched."""
        with patch('gtd.notion.entries.prompt_input', return_value=None):
            result = _collect_field_updates(_entry(), [field])

        assert key not in result

    @pytest.mark.parametrize(
        ('field', 'key'),
        [
            ('Name', 'name'),
            ('Next actionable step', 'next_step'),
            ('Success condition', 'success_condition'),
        ],
    )
    def test_empty_string_is_kept_as_a_deliberate_clear(
        self, field: str, key: str
    ):
        with patch('gtd.notion.entries.prompt_input', return_value=''):
            result = _collect_field_updates(_entry(), [field])

        assert result == {key: ''}

    def test_context_is_chosen_from_notion_options(self):
        with (
            patch(
                'gtd.notion.entries.get_select_options',
                return_value=['@home', '@work'],
            ) as options,
            patch('gtd.notion.entries.fzf_on_a_list', return_value='@home'),
        ):
            result = _collect_field_updates(_entry(), ['Context'])

        options.assert_called_once_with('Context')
        assert result == {'context': '@home'}

    def test_cancelled_context_picker_omits_the_field(self):
        with (
            patch(
                'gtd.notion.entries.get_select_options', return_value=['@home']
            ),
            patch('gtd.notion.entries.fzf_on_a_list', return_value=None),
        ):
            result = _collect_field_updates(_entry(), ['Context'])

        assert result == {}

    def test_status_change_captures_selection(self):
        with patch(
            'gtd.notion.entries.fzf_on_a_list', return_value='Someday/Maybe'
        ):
            result = _collect_field_updates(_entry(), ['Status'])

        assert result == {'status': 'Someday/Maybe'}

    def test_waiting_for_status_also_prompts_for_who_and_when(self):
        with (
            patch(
                'gtd.notion.entries.fzf_on_a_list', return_value='Waiting For'
            ),
            patch(
                'gtd.notion.entries.prompt_input',
                side_effect=['Bob', 'tomorrow'],
            ),
            patch(
                'gtd.notion.entries._parse_date_input',
                return_value='2026-07-28',
            ),
        ):
            result = _collect_field_updates(_entry(), ['Status'])

        assert result == {
            'status': 'Waiting For',
            'next_step': 'Bob',
            'follow_up_date': '2026-07-28',
        }

    def test_waiting_for_without_follow_up_skips_the_date(self):
        with (
            patch(
                'gtd.notion.entries.fzf_on_a_list', return_value='Waiting For'
            ),
            patch('gtd.notion.entries.prompt_input', side_effect=['Bob', '']),
        ):
            result = _collect_field_updates(_entry(), ['Status'])

        assert 'follow_up_date' not in result

    def test_waiting_for_with_unparseable_date_skips_the_date(self):
        with (
            patch(
                'gtd.notion.entries.fzf_on_a_list', return_value='Waiting For'
            ),
            patch(
                'gtd.notion.entries.prompt_input',
                side_effect=['Bob', 'gibberish'],
            ),
            patch('gtd.notion.entries._parse_date_input', return_value=None),
        ):
            result = _collect_field_updates(_entry(), ['Status'])

        assert 'follow_up_date' not in result

    @pytest.mark.parametrize(
        ('field', 'key'),
        [
            ('Due date', 'due_date'),
            ('Follow-up date', 'follow_up_date'),
        ],
    )
    def test_blank_date_clears_the_value(self, field: str, key: str):
        """Empty string is the documented "blank to clear" path."""
        with patch('gtd.notion.entries.prompt_input', return_value=''):
            result = _collect_field_updates(_entry(), [field])

        assert result == {key: ''}

    @pytest.mark.parametrize(
        ('field', 'key'),
        [
            ('Due date', 'due_date'),
            ('Follow-up date', 'follow_up_date'),
        ],
    )
    def test_parsed_date_is_stored(self, field: str, key: str):
        with (
            patch('gtd.notion.entries.prompt_input', return_value='Jul 15'),
            patch(
                'gtd.notion.entries._parse_date_input',
                return_value='2026-07-15',
            ),
        ):
            result = _collect_field_updates(_entry(), [field])

        assert result == {key: '2026-07-15'}

    @pytest.mark.parametrize(
        ('field', 'key'),
        [
            ('Due date', 'due_date'),
            ('Follow-up date', 'follow_up_date'),
        ],
    )
    def test_unparseable_date_leaves_value_untouched(
        self, field: str, key: str
    ):
        """A typo must not silently clear an existing date."""
        with (
            patch('gtd.notion.entries.prompt_input', return_value='gibberish'),
            patch('gtd.notion.entries._parse_date_input', return_value=None),
        ):
            result = _collect_field_updates(_entry(), [field])

        assert key not in result

    def test_multiple_fields_collected_in_one_pass(self):
        with (
            patch(
                'gtd.notion.entries.prompt_input',
                side_effect=['New name', 'New step'],
            ),
            patch('gtd.notion.entries.fzf_on_a_list', return_value='@home'),
            patch(
                'gtd.notion.entries.get_select_options',
                return_value=['@home'],
            ),
        ):
            result = _collect_field_updates(
                _entry(),
                ['Name', 'Next actionable step', 'Context'],
            )

        assert result == {
            'name': 'New name',
            'next_step': 'New step',
            'context': '@home',
        }
