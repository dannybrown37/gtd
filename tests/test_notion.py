"""Tests for Notion integration modules."""

from datetime import datetime, timedelta
from http import HTTPStatus
from unittest.mock import MagicMock, patch

import pytest

from gtd.notion.client import (
    NotionAPIError,
    _extract_block_text,
    _handle_response,
    build_property_update,
)
from gtd.notion.entries import _entry_preview_text, _parse_date_input
from gtd.notion.log import (
    _infer_cadence,
    _infer_reschedule_days,
    _is_recurring,
)
from gtd.notion.models import ProjectEntry
from gtd.notion.views import (
    inbox_entries,
    inbox_filter,
    next_steps_entries,
)


# --- _handle_response: maps HTTP codes to actionable errors ---


class TestHandleResponse:
    def _resp(self, code: int, **kwargs) -> MagicMock:
        resp = MagicMock()
        resp.status_code = code
        resp.is_success = 200 <= code < 300
        resp.text = kwargs.get('text', '')
        resp.headers = kwargs.get('headers', {})
        return resp

    def test_2xx_passes_silently(self):
        _handle_response(self._resp(200))
        _handle_response(self._resp(204))

    @pytest.mark.parametrize(
        ('code', 'expected_substr'),
        [
            (HTTPStatus.UNAUTHORIZED, 'gtd init'),
            (HTTPStatus.FORBIDDEN, 'permission'),
            (HTTPStatus.NOT_FOUND, 'NOTION_PROJECTS_DB_ID'),
            (HTTPStatus.CONFLICT, 'try again'),
            (HTTPStatus.TOO_MANY_REQUESTS, 'rate limit'),
        ],
    )
    def test_known_errors_give_actionable_advice(
        self, code: int, expected_substr: str
    ):
        resp = self._resp(code, headers={'Retry-After': '30'})
        with pytest.raises(NotionAPIError) as exc_info:
            _handle_response(resp)
        assert expected_substr.lower() in str(exc_info.value).lower()
        assert exc_info.value.status_code == code

    def test_5xx_treated_as_server_error(self):
        for code in (500, 502, 503):
            with pytest.raises(NotionAPIError, match='server error'):
                _handle_response(self._resp(code))

    def test_unknown_4xx_includes_response_body(self):
        resp = self._resp(422, text='{"message": "validation failed"}')
        with pytest.raises(NotionAPIError) as exc_info:
            _handle_response(resp)
        assert 'validation failed' in str(exc_info.value)


# --- _extract_block_text: the parser that drives get_page_body ---


class TestExtractBlockText:
    def test_paragraph_joins_rich_text_segments(self):
        block = {
            'type': 'paragraph',
            'paragraph': {
                'rich_text': [
                    {'plain_text': 'Hello'},
                    {'plain_text': 'world'},
                ],
            },
        }
        assert _extract_block_text(block) == 'Hello world'

    def test_bulleted_list_gets_prefix(self):
        block = {
            'type': 'bulleted_list_item',
            'bulleted_list_item': {
                'rich_text': [{'plain_text': 'Item one'}],
            },
        }
        assert _extract_block_text(block) == '• Item one'

    def test_unsupported_block_type_returns_none(self):
        block = {'type': 'image', 'image': {}}
        assert _extract_block_text(block) is None

    def test_whitespace_only_returns_none(self):
        block = {
            'type': 'paragraph',
            'paragraph': {'rich_text': [{'plain_text': '   '}]},
        }
        assert _extract_block_text(block) is None


# --- next_steps_entries: client-side filtering after Notion query ---


def _make_page(
    *,
    header: str = 'Test',
    context: str = 'Work',
    next_step: str = 'Do it',
    success_condition: str = 'Done',
    status: str = 'Current Project',
) -> dict:
    return {
        'id': 'page-1',
        'created_time': '2026-06-01T00:00:00Z',
        'properties': {
            'Header': {'title': [{'plain_text': header}]},
            'Status': {'select': {'name': status}},
            'Context': {
                'select': {'name': context} if context else None,
            },
            'Next Actionable Step': {
                'rich_text': [{'plain_text': next_step}] if next_step else [],
            },
            'Success Condition': {
                'rich_text': (
                    [{'plain_text': success_condition}]
                    if success_condition
                    else []
                ),
            },
            'Due Date': {'date': None},
            'Follow-Up Date': {'date': None},
        },
    }


class TestGetTodayEntries:
    @patch('gtd.notion.views.query_database')
    def test_excludes_incomplete_entries(self, mock_db):
        """Items missing context or next_step are filtered out client-side."""
        mock_db.return_value = [
            _make_page(header='Complete', context='Work', next_step='Go'),
            _make_page(header='No context', context='', next_step='Go'),
            _make_page(header='No step', context='Work', next_step=''),
        ]
        results = next_steps_entries()
        assert len(results) == 1
        assert results[0].header == 'Complete'

    @patch('gtd.notion.views.query_database')
    def test_recurring_items_shown_without_context_or_step(self, mock_db):
        """Recurring items surface even without context/next_step set.

        Unlike Current Project entries, a recurring item's header alone
        describes the task -- it shouldn't need a Next Actionable Step
        or Context to show up in Today.
        """
        mock_db.return_value = [
            _make_page(
                header='Daily: Take out trash',
                context='',
                next_step='',
                status='Recurring',
            ),
            _make_page(header='No context', context='', next_step='Go'),
        ]
        results = next_steps_entries()
        assert len(results) == 1
        assert results[0].header == 'Daily: Take out trash'


# --- Triage catches items that would be invisible in Today ---


def _make_triage_page(
    *,
    header: str = 'Uncategorized',
    status: str = 'Triage',
    context: str = '',
    next_step: str = '',
    success_condition: str = '',
) -> dict:
    return {
        'id': 'page-triage-1',
        'created_time': '2026-06-01T00:00:00Z',
        'properties': {
            'Header': {'title': [{'plain_text': header}]},
            'Status': {
                'select': {'name': status} if status else None,
            },
            'Context': {
                'select': {'name': context} if context else None,
            },
            'Next Actionable Step': {
                'rich_text': [{'plain_text': next_step}] if next_step else [],
            },
            'Success Condition': {
                'rich_text': (
                    [{'plain_text': success_condition}]
                    if success_condition
                    else []
                ),
            },
            'Due Date': {'date': None},
            'Follow-Up Date': {'date': None},
        },
    }


class TestTriageCatchesInvisibleItems:
    """Items that would be invisible in Today MUST appear in triage.

    The design contract: if an item has no context or no next_step,
    it won't show in Today. But it should never reach that state
    silently -- either it's in Triage (awaiting processing) or it
    has no status (just captured). Both cases are caught by
    inbox_entries.
    """

    @patch('gtd.notion.views.query_database')
    def test_items_with_triage_status_appear(self, mock_db):
        mock_db.return_value = [
            _make_triage_page(header='Needs processing', status='Triage'),
        ]
        results = inbox_entries()
        assert len(results) == 1
        assert results[0].header == 'Needs processing'

    @patch('gtd.notion.views.query_database')
    def test_items_with_no_status_appear(self, mock_db):
        mock_db.return_value = [
            _make_triage_page(header='Just captured', status=''),
        ]
        results = inbox_entries()
        assert len(results) == 1
        assert results[0].header == 'Just captured'

    @patch('gtd.notion.views.query_database')
    def test_triage_items_never_appear_in_today(self, mock_db):
        """Items in Triage are invisible to Today -- by design.

        Today only shows Current Projects with context + next_step.
        Triage items lack these, so they correctly don't show up.
        This test proves the safety net: you can't accidentally
        lose track of items because they MUST go through triage
        before becoming Current Projects.
        """
        mock_db.return_value = [
            _make_triage_page(
                header='In triage',
                status='Triage',
                context='',
                next_step='',
            ),
            _make_page(
                header='Properly triaged',
                context='Work',
                next_step='Do thing',
            ),
        ]
        results = next_steps_entries()
        assert len(results) == 1
        assert results[0].header == 'Properly triaged'


# --- _parse_date_input: relative dates and error handling ---


class TestParseDateInput:
    def test_relative_days_bypass_dateutil(self):
        result = _parse_date_input('tomorrow')
        expected = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
        assert result == expected

    def test_garbage_returns_none_gracefully(self, capsys):
        assert _parse_date_input('asdfghjkl') is None
        assert 'Could not parse' in capsys.readouterr().out


# --- Cadence inference: drives auto-rescheduling logic ---


class TestCadenceInference:
    @pytest.mark.parametrize(
        ('header', 'expected_days'),
        [
            ('Daily: Meditate', 1),
            ('daily: journal', 1),
            ('Weekly: Review goals', 7),
            ('2x/week: Exercise', 3),
            ('3x/week: Practice guitar', 2),
        ],
    )
    def test_recurring_headers_infer_days(
        self, header: str, expected_days: int
    ):
        assert _infer_reschedule_days(header) == expected_days

    @pytest.mark.parametrize(
        'header',
        ['Buy groceries', 'Call dentist', '', 'Dailyish: nope'],
    )
    def test_non_recurring_returns_none(self, header: str):
        assert _infer_reschedule_days(header) is None

    def test_infer_cadence_defaults_to_weekly(self):
        assert _infer_cadence('No prefix') == 'weekly'

    def test_is_recurring_delegates_to_infer(self):
        entry = ProjectEntry(
            page_id='x',
            header='Daily: Meditate',
            status='Current Project',
            context='Home',
            next_step='Sit',
            success_condition='Feel centered',
            due_date=None,
            follow_up_date=None,
            created_date='2026-06-01',
            updated_date='',
        )
        assert _is_recurring(entry) is True
        entry.header = 'Call Bob'
        assert _is_recurring(entry) is False


# --- build_property_update: empty-string-clears semantics ---


class TestBuildPropertyUpdate:
    def test_empty_string_clears_date_field(self):
        """Empty string means 'clear this field', distinct from None."""
        props = build_property_update(due_date='', follow_up_date='')
        assert props['Due Date'] == {'date': None}
        assert props['Follow-Up Date'] == {'date': None}

    def test_none_omits_field_entirely(self):
        props = build_property_update(status='Waiting For')
        assert 'Status' in props
        assert 'Due Date' not in props
        assert 'Follow-Up Date' not in props
        assert 'Header' not in props
        assert 'Success Condition' not in props

    def test_success_condition_included_when_set(self):
        props = build_property_update(success_condition='Ship the feature')
        assert props['Success Condition'] == {
            'rich_text': [{'text': {'content': 'Ship the feature'}}]
        }

    def test_success_condition_none_omits_field(self):
        props = build_property_update(next_step='Do it')
        assert 'Success Condition' not in props


# --- ProjectEntry.from_page: parses success_condition ---


class TestProjectEntryFromPage:
    def test_parses_success_condition(self):
        page = _make_page(success_condition='Inbox zero maintained')
        entry = ProjectEntry.from_page(page)
        assert entry.success_condition == 'Inbox zero maintained'

    def test_empty_success_condition_defaults_to_empty_string(self):
        page = _make_page(success_condition='')
        entry = ProjectEntry.from_page(page)
        assert entry.success_condition == ''

    def test_missing_iso_property_defaults_to_empty_string(self):
        """Pages created before the ISO field was added parse gracefully."""
        page = _make_page()
        del page['properties']['Success Condition']
        entry = ProjectEntry.from_page(page)
        assert entry.success_condition == ''


# --- inbox_entries: items missing ISO appear for triage ---


class TestTriageIncludesItemsMissingISO:
    @patch('gtd.notion.views.query_database')
    def test_items_missing_iso_appear_in_triage(self, mock_db):
        """Projects without an ISO must surface for triage."""
        mock_db.return_value = [
            _make_triage_page(
                header='No outcome set',
                status='Current Project',
                context='Work',
                next_step='Do something',
                success_condition='',
            ),
        ]
        results = inbox_entries()
        assert len(results) == 1
        assert results[0].header == 'No outcome set'

    @patch('gtd.notion.views.query_database')
    def test_filter_includes_iso_condition(self, mock_db):
        """The query sent to Notion must include an ISO-empty condition."""
        mock_db.return_value = []
        inbox_entries()
        filter_obj = mock_db.call_args.kwargs.get('filter_obj', {})
        assert matches_notion_filter(
            filter_obj,
            {
                'Status': 'Current Project',
                'Context': 'Work',
                'Next Actionable Step': 'Do something',
                'Success Condition': '',
            },
        )


# --- inbox_filter: one definition shared by every inbox surface ---


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
                'categorized list item has no next step or ISO',
                _props(
                    status='List',
                    context='',
                    next_step='',
                    success_condition='',
                    list_category='Books',
                ),
                False,
            ),
            (
                'uncategorized list item',
                _props(status='List', list_category=''),
                True,
            ),
        ],
    )
    def test_membership(
        self, description: str, props: dict[str, str], expected: bool
    ):
        assert matches_notion_filter(inbox_filter(), props) is expected, (
            description
        )

    def test_inbox_tab_uses_the_shared_definition(self):
        """Weekly review triage list must equal the Inbox tab.

        The tab can't hold its own copy of the filter any more -- it fetches
        through `views.inbox_entries`, so both halves (the Notion filter and
        the agenda exemption) come along automatically.
        """
        from gtd.gtd_tui import InboxContent

        entries = [object()]
        with patch(
            'gtd.notion.views.inbox_entries',
            return_value=entries,
        ) as fetch:
            assert InboxContent._fetch(None) is entries  # noqa: SLF001
        fetch.assert_called_once_with()


# --- _entry_preview_text: outcome shown in fzf preview ---


class TestEntryPreviewText:
    @pytest.mark.parametrize(
        ('outcome', 'expected'),
        [
            ('Project shipped', 'Project shipped'),
            ('', '(none)'),
        ],
    )
    def test_outcome_shown_in_preview(self, outcome: str, expected: str):
        page = _make_page(success_condition=outcome)
        entry = ProjectEntry.from_page(page)
        result = _entry_preview_text(entry)
        assert expected in result


# --- @Person agenda items: no next step / ISO required ---


class TestAgendaContexts:
    """Agenda items (`@Person` contexts) are things to raise with a person.

    They are complete once they have a context and a follow-up/due date --
    demanding a Next Actionable Step and a Success Condition for "mention
    the budget to Sam" is busywork, and leaving them blank used to park the
    item in the inbox forever.
    """

    @pytest.mark.parametrize(
        ('context', 'expected'),
        [
            ('@Sam', True),
            ('@', True),
            ('Work', False),
            ('', False),
            (None, False),
        ],
    )
    def test_is_agenda_context(self, context: str | None, expected: bool):
        from gtd.notion.schema import is_agenda_context

        assert is_agenda_context(context) is expected

    @patch('gtd.notion.views.query_database')
    def test_triaged_agenda_item_is_not_inbox(self, mock_db):
        mock_db.return_value = [
            _make_triage_page(
                header='Discuss budget',
                status='Current Project',
                context='@Sam',
                next_step='',
                success_condition='',
            ),
        ]
        assert inbox_entries() == []

    @patch('gtd.notion.views.query_database')
    def test_untriaged_agenda_item_is_still_inbox(self, mock_db):
        """A `@Person` item still in Triage has not been processed yet."""
        mock_db.return_value = [
            _make_triage_page(
                header='Discuss budget',
                status='Triage',
                context='@Sam',
            ),
        ]
        assert len(inbox_entries()) == 1

    @patch('gtd.notion.views.query_database')
    def test_statusless_agenda_item_is_still_inbox(self, mock_db):
        mock_db.return_value = [
            _make_triage_page(
                header='Discuss budget',
                status='',
                context='@Sam',
            ),
        ]
        assert len(inbox_entries()) == 1

    @patch('gtd.notion.views.query_database')
    def test_non_agenda_item_missing_fields_is_still_inbox(self, mock_db):
        mock_db.return_value = [
            _make_triage_page(
                header='Vague thing',
                status='Current Project',
                context='Work',
                next_step='',
                success_condition='',
            ),
        ]
        assert len(inbox_entries()) == 1

    @patch('gtd.notion.views.query_database')
    def test_inbox_tab_applies_the_same_exemption(self, mock_db):
        """The TUI Inbox tab must agree with the CLI on what is inbox.

        A triaged agenda item is complete with just a context, so it must
        not bounce back into the Inbox on either surface.
        """
        from gtd.gtd_tui import InboxContent

        mock_db.return_value = [
            _make_triage_page(
                header='Discuss budget',
                status='Current Project',
                context='@Sam',
            ),
        ]

        assert InboxContent._fetch(None) == []  # noqa: SLF001


# --- @Person agenda items: the person lives in the header ---


class TestAgendaPersonFromHeader:
    """`@Sam: raise the thing` declares its own context.

    Agenda items are captured by typing the person into the header. The
    Context select lags behind -- a brand-new person has no option yet, so
    triage used to ask you to pick a context that didn't exist, for an item
    that had already named it. The header prefix *is* the context.
    """

    @pytest.mark.parametrize(
        ('header', 'expected'),
        [
            ('@Sam: raise the budget question', '@Sam'),
            ('@Sam raise the budget question', '@Sam'),
            ('@Sam', '@Sam'),
            ('  @Sam: leading whitespace', '@Sam'),
            ('Ask @Sam about the thing', None),
            ('@: nothing after the sigil', None),
            ('@', None),
            ('Normal item', None),
            ('', None),
        ],
    )
    def test_parsing(self, header: str, expected: str | None):
        from gtd.notion.schema import agenda_person_from_header

        assert agenda_person_from_header(header) == expected

    @patch('gtd.notion.views.query_database')
    def test_header_agenda_item_is_not_inbox_once_triaged(self, mock_db):
        """Context still empty, but the header names the person."""
        mock_db.return_value = [
            _make_triage_page(
                header='@Sam: raise the budget',
                status='Current Project',
                context='',
                next_step='',
                success_condition='',
            ),
        ]
        assert inbox_entries() == []

    @patch('gtd.notion.views.query_database')
    def test_header_agenda_item_in_triage_still_appears(self, mock_db):
        mock_db.return_value = [
            _make_triage_page(
                header='@Sam: raise the budget',
                status='Triage',
                context='',
            ),
        ]
        assert len(inbox_entries()) == 1

    def test_is_agenda_entry_accepts_either_signal(self):
        from gtd.notion.schema import is_agenda_entry

        by_header = ProjectEntry.from_page(
            _make_triage_page(header='@Sam: talk', context='')
        )
        by_context = ProjectEntry.from_page(
            _make_triage_page(header='Talk to Sam', context='@Sam')
        )
        neither = ProjectEntry.from_page(
            _make_triage_page(header='Talk to Sam', context='Work')
        )
        assert is_agenda_entry(by_header) is True
        assert is_agenda_entry(by_context) is True
        assert is_agenda_entry(neither) is False


class TestAgendaStatus:
    """Agenda items are always Current Projects.

    There is no case where "@Sam: raise the budget" is Someday/Maybe or a
    List, so triage doesn't ask -- the Status prompt was one more keystroke
    with only one real answer. Dropping an agenda item is still possible via
    the Inbox tab's `D` binding, which is why removing the prompt (and with
    it the inline Delete option) doesn't strand anything.
    """

    def test_agenda_status_is_current_project(self):
        from gtd.notion.schema import AGENDA_STATUS, STATUSES

        assert AGENDA_STATUS == 'Current Project'
        assert AGENDA_STATUS in STATUSES

    @patch('gtd.notion.views.query_database')
    def test_agenda_item_left_in_triage_still_needs_processing(self, mock_db):
        """Auto-status only applies during triage, not retroactively."""
        mock_db.return_value = [
            _make_triage_page(header='@Sam: talk', status='Triage'),
        ]
        assert len(inbox_entries()) == 1


class TestAgendaItemsAppearInNextSteps:
    """Agenda items must reach the Next Steps tab without a next_step.

    `next_steps_entries` gates on `context and next_step`, which is exactly
    the field agenda items are exempt from providing. Exempting them in
    triage without exempting them here left "@Sam: raise the budget" visible
    on Projects but invisible on Next Steps -- actionable, and unfindable.
    """

    @patch('gtd.notion.views.query_database')
    def test_agenda_item_shown_without_next_step(self, mock_db):
        mock_db.return_value = [
            _make_page(
                header='@Sam: raise the budget',
                context='@Sam',
                next_step='',
                status='Current Project',
            ),
        ]
        results = next_steps_entries()
        assert len(results) == 1
        assert results[0].header == '@Sam: raise the budget'

    @patch('gtd.notion.views.query_database')
    def test_agenda_item_shown_from_header_alone(self, mock_db):
        """Context may still be empty if the item was never triaged."""
        mock_db.return_value = [
            _make_page(
                header='@Sam: raise the budget',
                context='',
                next_step='',
                status='Current Project',
            ),
        ]
        assert len(next_steps_entries()) == 1

    @patch('gtd.notion.views.query_database')
    def test_non_agenda_item_still_needs_a_next_step(self, mock_db):
        mock_db.return_value = [
            _make_page(header='Vague', context='Work', next_step=''),
        ]
        assert next_steps_entries() == []
