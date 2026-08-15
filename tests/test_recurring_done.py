"""A Recurring item may never be archived by an unqualified "Done".

The TUI asks *Reschedule vs Permanently complete* before `action_mark_done`
archives anything. The webapp asked the same question -- but only when it
knew the entry's status, and `GET /next-steps` strips `status` out of its
payload (`EXCLUDE_THESE`), which is exactly the view a recurring item shows
up on. So on the home view the sheet offered a plain "Done" and the item was
deleted outright.

The guard therefore lives at the write chokepoint (`POST /done/<id>`), not in
the client that happens to know the status: a bare API call can't silently
destroy a recurring item either. `reschedule` or an explicit
`confirm_recurring` are the two ways through.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from gtd import api

if TYPE_CHECKING:
    from collections.abc import Iterator

    from flask.testing import FlaskClient

APP_JS = Path(__file__).parent.parent / 'src' / 'gtd' / 'webapp' / 'app.js'


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[FlaskClient]:
    monkeypatch.setenv('GTD_API_KEY', 'test-key')
    api.app.config['TESTING'] = True
    with api.app.test_client() as c:
        yield c


@pytest.fixture
def auth_header() -> dict[str, str]:
    return {'Authorization': 'Bearer test-key'}


def make_page(
    header: str = 'Water the plants', status: str = 'Recurring'
) -> dict:
    return {
        'id': 'page-1',
        'created_time': '2026-08-01',
        'last_edited_time': '2026-08-01',
        'properties': {
            'Header': {'title': [{'plain_text': header}]},
            'Status': {'select': {'name': status}},
        },
    }


def patch_page(monkeypatch: pytest.MonkeyPatch, page: dict) -> MagicMock:
    monkeypatch.setattr(api, '_get_page_by_id', MagicMock(return_value=page))
    archive = MagicMock(return_value={})
    monkeypatch.setattr(api, 'archive_page', archive)
    return archive


@pytest.mark.parametrize(
    ('header', 'status'),
    [
        ('Water the plants', 'Recurring'),
        # The TUI's `_is_recurring` also catches a cadence prefix on an item
        # whose status never got set, so the guard must too.
        ('Daily: floss', 'Current Project'),
        ('2x/week: gym', ''),
    ],
)
def test_done_refuses_to_archive_a_recurring_item(
    client: FlaskClient,
    auth_header: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    header: str,
    status: str,
) -> None:
    archive = patch_page(monkeypatch, make_page(header, status))
    response = client.post('/done/page-1', headers=auth_header)
    assert response.status_code == 409
    body = response.get_json()
    assert body['recurring'] is True
    assert body['header'] == header
    archive.assert_not_called()


def test_done_archives_recurring_when_explicitly_confirmed(
    client: FlaskClient,
    auth_header: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = patch_page(monkeypatch, make_page())
    response = client.post(
        '/done/page-1',
        headers=auth_header,
        json={'confirm_recurring': True},
    )
    assert response.status_code == 200
    assert response.get_json() == {'deleted': True}
    archive.assert_called_once_with('page-1')


def test_reschedule_still_needs_no_confirmation(
    client: FlaskClient,
    auth_header: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = patch_page(monkeypatch, make_page())
    monkeypatch.setattr(api, 'update_page', MagicMock(return_value={}))
    response = client.post(
        '/done/page-1',
        headers=auth_header,
        json={'reschedule': '2026-08-22'},
    )
    assert response.status_code == 200
    assert response.get_json() == {'rescheduled': '2026-08-22'}
    archive.assert_not_called()


def test_non_recurring_done_is_unaffected(
    client: FlaskClient,
    auth_header: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = patch_page(
        monkeypatch, make_page('Ship the thing', 'Current Project')
    )
    response = client.post('/done/page-1', headers=auth_header)
    assert response.status_code == 200
    archive.assert_called_once_with('page-1')


def test_next_steps_payload_carries_status(
    client: FlaskClient,
    auth_header: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without this the action sheet can't label Done "(reschedule)"."""
    from gtd.notion.models import ProjectEntry

    entry = ProjectEntry(
        page_id='page-1',
        header='Water the plants',
        status='Recurring',
        context='Home',
        next_step='Water them',
        success_condition='',
        due_date=None,
        follow_up_date='2026-08-15',
        created_date='2026-08-01',
        list_category='',
        updated_date='2026-08-01',
        area='',
    )
    monkeypatch.setattr(
        api, 'next_steps_entries', MagicMock(return_value=[entry])
    )
    response = client.get('/next-steps', headers=auth_header)
    assert response.status_code == 200
    assert response.get_json()[0]['status'] == 'Recurring'


def test_webapp_handles_the_recurring_refusal() -> None:
    """The 409 must reopen the reschedule choice, not surface as an error."""
    js = APP_JS.read_text()
    match = re.search(
        r'^async function markDone\(.*?^\}', js, re.DOTALL | re.MULTILINE
    )
    assert match, 'markDone() not found in app.js'
    source = match.group(0)
    assert 'recurring' in source, (
        'markDone must branch on the 409 recurring refusal'
    )
    assert 'openRescheduleModal' in source
    assert 'confirm_recurring' in js, (
        'the permanent-completion path must send confirm_recurring'
    )
