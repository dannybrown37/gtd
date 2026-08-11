"""Tests for gtd.notion.views — the one definition of each GTD view.

These tests describe *membership*: given a database, which entries does a
view contain? That is the property that used to be defined twice (once in
`notion/`, once again in `api.py`) and drifted, so it is the property worth
pinning. `tests/test_view_definitions.py` covers the structural half —
that no surface re-implements any of this.
"""

from collections.abc import Callable
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from gtd.notion.models import ProjectEntry
from gtd.notion.views import (
    _follow_up_clause,
    _status_clause,
    _today_filter,
    drop_triaged_agenda_items,
    entries_for_status,
    inbox_entries,
    inbox_filter,
    is_actionable,
    is_due_today,
    next_steps_entries,
    status_filter,
)


TODAY = '2026-08-11'
YESTERDAY = '2026-08-10'
TOMORROW = '2026-08-12'


def _entry(**overrides) -> ProjectEntry:
    defaults = {
        'page_id': 'page-1',
        'header': 'Ship the thing',
        'status': 'Current Project',
        'context': 'Work',
        'next_step': 'Draft the plan',
        'success_condition': 'Shipped',
        'due_date': None,
        'follow_up_date': None,
        'created_date': '2026-06-01',
    }
    return ProjectEntry(**{**defaults, **overrides})


def _patch_query(entries: list[ProjectEntry]) -> tuple:
    """Stand in for Notion, returning `entries` whatever the filter."""
    pages = [object() for _ in entries]
    by_page = dict(zip(pages, entries, strict=True))
    return (
        patch('gtd.notion.views.query_database', return_value=pages),
        patch(
            'gtd.notion.views.ProjectEntry.from_page',
            side_effect=lambda p: by_page[p],
        ),
    )


def _run(fn: Callable, entries: list[ProjectEntry]) -> tuple:
    query, from_page = _patch_query(entries)
    with query as q, from_page:
        return fn(), q


# --- is_due_today: the tickler/deadline rule, in pure Python ---


class TestIsDueToday:
    @pytest.mark.parametrize(
        ('description', 'entry', 'expected'),
        [
            ('no dates at all', _entry(), True),
            ('follow-up today', _entry(follow_up_date=TODAY), True),
            ('follow-up passed', _entry(follow_up_date=YESTERDAY), True),
            ('snoozed to tomorrow', _entry(follow_up_date=TOMORROW), False),
            ('due today', _entry(due_date=TODAY), True),
            ('overdue', _entry(due_date=YESTERDAY), True),
            ('due later, not snoozed', _entry(due_date=TOMORROW), True),
            (
                'due today but snoozed past it',
                _entry(due_date=TODAY, follow_up_date=TOMORROW),
                True,
            ),
            (
                'snoozed, and not due until later',
                _entry(due_date=TOMORROW, follow_up_date=TOMORROW),
                False,
            ),
        ],
    )
    def test_membership(
        self, description: str, entry: ProjectEntry, expected: bool
    ):
        assert is_due_today(entry, TODAY) is expected, description

    def test_a_deadline_outranks_a_snooze(self):
        """The hard landscape beats the tickler.

        Snoozing is a request to be left alone; a Due Date is a promise to
        someone else. An item due Wednesday but snoozed to Friday must
        still surface on Wednesday.
        """
        snoozed_past_its_deadline = _entry(
            due_date=TODAY,
            follow_up_date=TOMORROW,
        )

        assert is_due_today(snoozed_past_its_deadline, TODAY)


# --- is_actionable: what triage has to have supplied ---


class TestIsActionable:
    @pytest.mark.parametrize(
        ('description', 'entry', 'expected'),
        [
            ('fully triaged', _entry(), True),
            ('no context', _entry(context=''), False),
            ('no next step', _entry(next_step=''), False),
            (
                'recurring needs neither',
                _entry(status='Recurring', context='', next_step=''),
                True,
            ),
            (
                'agenda item by header',
                _entry(header='@Sam: raise the budget', next_step=''),
                True,
            ),
            (
                'agenda item by context',
                _entry(context='@Sam', next_step=''),
                True,
            ),
        ],
    )
    def test_membership(
        self, description: str, entry: ProjectEntry, expected: bool
    ):
        assert is_actionable(entry) is expected, description


# --- _today_filter: what Next Steps asks Notion for ---


class TestTodayFilter:
    def test_matches_only_active_statuses(self):
        status_clause = _today_filter(TODAY)['and'][0]['or']
        statuses = [c['select']['equals'] for c in status_clause]

        assert statuses == ['Current Project', 'Recurring']

    @pytest.mark.parametrize(
        'excluded',
        ['Triage', 'Waiting For', 'Someday/Maybe', 'List'],
    )
    def test_inactive_statuses_are_not_requested(self, excluded: str):
        """Someday and Waiting For must never leak into Next Steps."""
        status_clause = _today_filter(TODAY)['and'][0]['or']
        statuses = [c['select']['equals'] for c in status_clause]

        assert excluded not in statuses

    def test_follow_up_clause_admits_due_and_unset_dates(self):
        date_clause = _today_filter(TODAY)['and'][1]['or']

        assert {
            'property': 'Follow-Up Date',
            'date': {'on_or_before': TODAY},
        } in date_clause
        assert {
            'property': 'Follow-Up Date',
            'date': {'is_empty': True},
        } in date_clause

    def test_future_follow_ups_are_excluded_by_on_or_before(self):
        """Snoozed items stay hidden — the whole point of snoozing."""
        date_clause = _today_filter(TODAY)['and'][1]['or']
        bounds = [
            c['date']['on_or_before']
            for c in date_clause
            if c['property'] == 'Follow-Up Date'
            and 'on_or_before' in c['date']
        ]

        assert bounds == [TODAY]

    def test_a_due_date_surfaces_an_item_a_snooze_would_hide(self):
        date_clause = _today_filter(TODAY)['and'][1]['or']

        assert {
            'property': 'Due Date',
            'date': {'on_or_before': TODAY},
        } in date_clause

    def test_undated_items_are_not_admitted_by_the_due_clause(self):
        """`is_empty` on Due Date would drag in every snoozed item."""
        date_clause = _today_filter(TODAY)['and'][1]['or']
        empties = [
            c['property'] for c in date_clause if c['date'].get('is_empty')
        ]

        assert empties == ['Follow-Up Date']

    def test_status_and_date_clauses_are_anded(self):
        result = _today_filter(TODAY)

        assert set(result) == {'and'}
        assert len(result['and']) == 2
        assert all('or' in clause for clause in result['and'])


# --- next_steps_entries ---


class TestNextStepsEntries:
    def test_untriaged_items_are_excluded(self):
        """The gate `/next-steps` used to be missing entirely."""
        entries = [_entry(page_id='ok'), _entry(page_id='bad', next_step='')]

        result, _ = _run(lambda: next_steps_entries(TODAY), entries)

        assert [e.page_id for e in result] == ['ok']

    def test_recurring_and_agenda_items_survive_the_gate(self):
        entries = [
            _entry(
                page_id='rec',
                status='Recurring',
                context='',
                next_step='',
            ),
            _entry(page_id='agenda', header='@Sam: budget', next_step=''),
        ]

        result, _ = _run(lambda: next_steps_entries(TODAY), entries)

        assert [e.page_id for e in result] == ['rec', 'agenda']

    def test_snoozed_items_are_excluded_client_side_too(self):
        entries = [_entry(page_id='snoozed', follow_up_date=TOMORROW)]

        result, _ = _run(lambda: next_steps_entries(TODAY), entries)

        assert result == []

    def test_today_defaults_to_now(self):
        today = datetime.now().strftime('%Y-%m-%d')

        _, query = _run(next_steps_entries, [])

        date_clause = query.call_args.kwargs['filter_obj']['and'][1]['or']
        assert date_clause[0]['date']['on_or_before'] == today

    def test_the_caller_may_pin_today(self):
        """The API supplies a timezone-aware date; the TUI does not."""
        _, query = _run(lambda: next_steps_entries('2030-01-01'), [])

        date_clause = query.call_args.kwargs['filter_obj']['and'][1]['or']
        assert date_clause[0]['date']['on_or_before'] == '2030-01-01'


# --- inbox_filter / inbox_entries ---


def matches_notion_filter(filter_obj: dict, props: dict[str, str]) -> bool:
    """Minimal local evaluator for the Notion filter shapes we emit."""
    if 'and' in filter_obj:
        return all(matches_notion_filter(f, props) for f in filter_obj['and'])
    if 'or' in filter_obj:
        return any(matches_notion_filter(f, props) for f in filter_obj['or'])
    value = props.get(filter_obj['property'], '')
    condition = filter_obj.get('select') or filter_obj['rich_text']
    if 'is_empty' in condition:
        return not value
    if 'equals' in condition:
        return value == condition['equals']
    return value != condition['does_not_equal']


def _props(
    *,
    status: str = 'Current Project',
    context: str = 'Work',
    next_step: str = 'Do it',
    success_condition: str = 'Done',
    list_category: str = '',
) -> dict[str, str]:
    return {
        'Status': status,
        'Context': context,
        'Next Actionable Step': next_step,
        'Success Condition': success_condition,
        'List Category': list_category,
    }


class TestInboxFilter:
    @pytest.mark.parametrize(
        ('description', 'props', 'expected'),
        [
            ('triage status', _props(status='Triage'), True),
            ('no status', _props(status=''), True),
            ('missing context', _props(context=''), True),
            ('missing next step', _props(next_step=''), True),
            ('missing ISO', _props(success_condition=''), True),
            ('fully processed', _props(), False),
            (
                'categorized list item has no context',
                _props(status='List', context='', list_category='Books'),
                False,
            ),
            (
                'uncategorized list item',
                _props(status='List', list_category=''),
                True,
            ),
            (
                'someday item has no context or next step',
                _props(status='Someday/Maybe', context='', next_step=''),
                False,
            ),
        ],
    )
    def test_membership(
        self, description: str, props: dict[str, str], expected: bool
    ):
        assert matches_notion_filter(inbox_filter(), props) is expected, (
            description
        )


class TestInboxEntries:
    def test_triaged_agenda_items_are_dropped(self):
        entries = [
            _entry(
                page_id='done',
                header='@Sam: budget',
                next_step='',
                success_condition='',
            ),
            _entry(
                page_id='new',
                header='@Sam: budget',
                status='Triage',
                next_step='',
                success_condition='',
            ),
        ]

        result, _ = _run(inbox_entries, entries)

        assert [e.page_id for e in result] == ['new']

    def test_a_current_project_with_no_next_step_is_inbox(self):
        """The canonical "projects with no next action" check.

        `/inbox` used to be `Status == "Triage"` only, so this item — the
        single most important thing a GTD inbox is for — was invisible on
        mobile.
        """
        assert matches_notion_filter(inbox_filter(), _props(next_step=''))

    def test_matches_drop_triaged_agenda_items(self):
        entries = [_entry(header='@Sam: budget', next_step='')]

        result, _ = _run(inbox_entries, entries)

        assert result == drop_triaged_agenda_items(entries)


# --- entries_for_status ---


class TestStatusFilter:
    def test_one_status_is_a_bare_clause(self):
        assert status_filter('Waiting For') == {
            'property': 'Status',
            'select': {'equals': 'Waiting For'},
        }

    def test_several_statuses_are_ored(self):
        result = status_filter(['Current Project', 'Waiting For'])

        assert result == _status_clause(['Current Project', 'Waiting For'])
        assert [c['select']['equals'] for c in result['or']] == [
            'Current Project',
            'Waiting For',
        ]

    def test_context_is_anded_on(self):
        result = status_filter('List', context='Work')

        assert {
            'property': 'Context',
            'select': {'equals': 'Work'},
        } in result['and']

    @pytest.mark.parametrize('follow_up', ['future', 'due'])
    def test_follow_up_is_anded_on(self, follow_up: str):
        result = status_filter(
            'Current Project',
            follow_up=follow_up,
            today=TODAY,
        )

        assert _follow_up_clause(follow_up, TODAY) in result['and']

    def test_list_category_is_anded_on(self):
        result = status_filter('List', list_category='Books')

        assert {
            'property': 'List Category',
            'select': {'equals': 'Books'},
        } in result['and']

    def test_a_list_category_still_requires_the_status(self):
        """The Lists tab is `Status == 'List'`, so `/list/<cat>` is too.

        Filtering on the category alone showed the phone entries the TUI
        never listed.
        """
        result = status_filter('List', list_category='Books')

        assert _status_clause('List') in result['and']

    def test_an_unknown_follow_up_is_ignored(self):
        assert status_filter('List', follow_up='') == status_filter('List')

    def test_due_admits_an_unset_follow_up_date(self):
        """Most entries never get one, and they are all actionable now."""
        clause = _follow_up_clause('due', TODAY)

        assert {
            'property': 'Follow-Up Date',
            'date': {'is_empty': True},
        } in clause['or']

    def test_future_does_not_admit_an_unset_follow_up_date(self):
        """Incubation means explicitly deferred, not merely undated."""
        clause = _follow_up_clause('future', TODAY)

        assert clause == {
            'property': 'Follow-Up Date',
            'date': {'after': TODAY},
        }


class TestEntriesForStatus:
    def test_a_category_on_the_wrong_status_is_excluded(self):
        entries = [
            _entry(page_id='listed', status='List', list_category='Books'),
            _entry(page_id='stray', status='Current Project'),
        ]
        entries[1].list_category = 'Books'

        result, _ = _run(
            lambda: entries_for_status('List', list_category='Books'),
            entries,
        )

        assert [e.page_id for e in result] == ['listed']

    def test_entries_are_returned_unsorted(self):
        """Ordering is presentation; each surface applies its own."""
        entries = [
            _entry(page_id='b', status='List'),
            _entry(page_id='a', status='List'),
        ]

        result, _ = _run(lambda: entries_for_status('List'), entries)

        assert [e.page_id for e in result] == ['b', 'a']

    def test_today_defaults_to_now_for_follow_up(self):
        today = datetime.now().strftime('%Y-%m-%d')

        _, query = _run(
            lambda: entries_for_status('Current Project', follow_up='future'),
            [],
        )

        assert query.call_args.kwargs['filter_obj']['and'][1] == {
            'property': 'Follow-Up Date',
            'date': {'after': today},
        }

    def test_a_future_follow_up_is_tomorrow_onwards(self):
        """`after` today, not `on_or_after` — today is not deferred."""
        tomorrow = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
        today = datetime.now().strftime('%Y-%m-%d')
        clause = _follow_up_clause('future', today)

        assert clause['date']['after'] < tomorrow
