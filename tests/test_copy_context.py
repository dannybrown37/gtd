"""Copying an entry's full context as plain text, for pasting into an AI."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from gtd import api
from gtd.notion.models import ProjectEntry, entry_context_text

if TYPE_CHECKING:
    from flask.testing import FlaskClient
    from collections.abc import Iterator


def _entry(**kwargs: object) -> ProjectEntry:
    defaults = {
        'page_id': 'page-1',
        'header': 'Ship the thing',
        'status': 'Current Project',
        'context': '@Computer',
        'next_step': '1. Draft spec\n2. Review it',
        'success_condition': 'Thing is shipped',
        'due_date': '2026-09-01',
        'follow_up_date': None,
        'created_date': '2026-08-01T00:00:00.000Z',
        'area': 'Work',
    }
    defaults.update(kwargs)
    return ProjectEntry(**defaults)  # type: ignore[arg-type]


def test_context_text_is_title_and_notes_only() -> None:
    """Everything else is noise in an AI prompt — deliberately excluded."""
    text = entry_context_text(_entry(), notes='Some notes')
    assert text == 'Ship the thing\n\nSome notes\n'


@pytest.mark.parametrize(
    'excluded',
    ['Current Project', '@Computer', 'Draft spec', '2026-09-01', 'Work'],
)
def test_context_text_omits_metadata(excluded: str) -> None:
    assert excluded not in entry_context_text(_entry(), notes='Some notes')


def test_context_text_without_notes_is_just_the_title() -> None:
    assert entry_context_text(_entry(), notes='') == 'Ship the thing\n'


def test_context_text_has_no_markup_tags() -> None:
    text = entry_context_text(_entry(), notes='Some notes')
    assert '[/' not in text
    assert '[bold' not in text


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[FlaskClient]:
    monkeypatch.setenv('GTD_API_KEY', 'test-key')
    api.app.config['TESTING'] = True
    with api.app.test_client() as c:
        yield c


def test_context_endpoint_returns_text(
    client: FlaskClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = _entry()
    monkeypatch.setattr(
        api, '_get_page_by_id', MagicMock(return_value={'id': 'page-1'})
    )
    monkeypatch.setattr(
        api.ProjectEntry, 'from_page', MagicMock(return_value=entry)
    )
    monkeypatch.setattr(
        api, 'get_page_body', MagicMock(return_value='Notes here')
    )
    response = client.get(
        '/entry/page-1/context',
        headers={'Authorization': 'Bearer test-key'},
    )
    assert response.status_code == 200
    assert 'Ship the thing' in response.get_json()['text']
    assert 'Notes here' in response.get_json()['text']


def test_context_endpoint_requires_auth(client: FlaskClient) -> None:
    assert client.get('/entry/page-1/context').status_code == 401


COPY_TABS = [
    'NextStepsContent',
    'InboxContent',
    'ProjectsContent',
    'WaitingForContent',
    'SomedayContent',
    'RecurringContent',
    'SnoozedContent',
    'ListsContent',
]


@pytest.mark.parametrize('tab_name', COPY_TABS)
def test_copy_binding_is_not_greyed_out(tab_name: str) -> None:
    """`check_action` returning None greys the key out in the footer."""
    from gtd import gtd_tui

    tab = gtd_tui.__dict__[tab_name]
    check = tab.__dict__.get('check_action')
    if check is None:
        return
    widget = tab.__new__(tab)
    widget._current_habit_item = lambda: None  # noqa: SLF001
    widget._current_entry = _entry  # noqa: SLF001
    assert check(widget, 'copy_context', ()) is True


def test_copy_key_is_uppercase() -> None:
    from gtd import gtd_tui

    keys = [
        b.key
        for b in gtd_tui.BaseEntryContent.BINDINGS
        if b.action == 'copy_context'
    ]
    assert keys == ['Y']
