"""Tests for the generic entry endpoints that back the webapp's tabs.

The webapp needs the same views the TUI has (Projects, Waiting For,
Incubation, Recurring, Someday) plus the per-entry actions bound to `U`, `N`,
`T` and `D`. Rather than a route per tab, `/entries` takes a status filter and
`/entry/<id>` carries the actions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from gtd import api
from gtd.notion import views
from gtd.notion.models import ProjectEntry

if TYPE_CHECKING:
    from collections.abc import Iterator

    from flask.testing import FlaskClient


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[FlaskClient]:
    monkeypatch.setenv('GTD_API_KEY', 'test-key')
    api.app.config['TESTING'] = True
    with api.app.test_client() as c:
        yield c


@pytest.fixture
def auth_header() -> dict[str, str]:
    return {'Authorization': 'Bearer test-key'}


def make_entry(**overrides: object) -> ProjectEntry:
    defaults: dict = {
        'page_id': 'entry-1',
        'header': 'Ship the thing',
        'status': 'Current Project',
        'context': 'Computer',
        'next_step': 'Draft the PR',
        'success_condition': 'Merged',
        'due_date': None,
        'follow_up_date': None,
        'created_date': '2026-08-01',
        'list_category': '',
        'updated_date': '2026-08-01',
        'area': 'Work',
    }
    defaults.update(overrides)
    return ProjectEntry(**defaults)  # type: ignore[arg-type]


def patch_query(
    monkeypatch: pytest.MonkeyPatch,
    entries: list[ProjectEntry],
) -> MagicMock:
    """Stub `query_database` and return entries, bypassing page parsing.

    Both `api` and `gtd.notion.views` are stubbed: the view endpoints go
    through `views`, the rest still query directly.
    """
    query = MagicMock(return_value=[{'id': e.page_id} for e in entries])
    monkeypatch.setattr(api, 'query_database', query)
    monkeypatch.setattr(views, 'query_database', query)
    monkeypatch.setattr(
        api.ProjectEntry,
        'from_page',
        classmethod(
            lambda _cls, page: next(
                e for e in entries if e.page_id == page['id']
            )
        ),
    )
    return query


# region /entries


NEW_ROUTES = [
    ('get', '/entries'),
    ('patch', '/entry/page-1'),
    ('get', '/entry/page-1/notes'),
    ('put', '/entry/page-1/notes'),
    ('post', '/entry/page-1/snooze'),
]


@pytest.mark.parametrize(('method', 'route'), NEW_ROUTES)
def test_new_routes_require_auth(
    client: FlaskClient,
    method: str,
    route: str,
) -> None:
    assert getattr(client, method)(route).status_code == 401


def test_entries_requires_a_status(
    client: FlaskClient,
    auth_header: dict[str, str],
) -> None:
    """An unfiltered dump of the whole database is never what a tab wants."""
    response = client.get('/entries', headers=auth_header)
    assert response.status_code == 400
    assert 'status' in response.get_json()['error']


def test_entries_rejects_unknown_status(
    client: FlaskClient,
    auth_header: dict[str, str],
) -> None:
    response = client.get('/entries?status=Nonsense', headers=auth_header)
    assert response.status_code == 400
    assert 'Nonsense' in response.get_json()['error']


def test_entries_filters_by_status(
    client: FlaskClient,
    auth_header: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = make_entry(status='Waiting For')
    query = patch_query(monkeypatch, [entry])
    response = client.get('/entries?status=Waiting+For', headers=auth_header)
    assert response.status_code == 200
    assert query.call_args.kwargs['filter_obj'] == {
        'property': 'Status',
        'select': {'equals': 'Waiting For'},
    }
    assert [e['page_id'] for e in response.get_json()] == ['entry-1']


def test_entries_incubation_keeps_only_future_follow_ups(
    client: FlaskClient,
    auth_header: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`follow_up=future` is the Incubation tab: deferred, not yet due."""
    monkeypatch.setattr(api, '_today_iso', lambda: '2026-08-06')
    entries = [
        make_entry(page_id='past', follow_up_date='2026-08-01'),
        make_entry(page_id='today', follow_up_date='2026-08-06'),
        make_entry(page_id='future', follow_up_date='2026-09-01'),
        make_entry(page_id='none', follow_up_date=None),
    ]
    patch_query(monkeypatch, entries)
    response = client.get(
        '/entries?status=Current+Project&follow_up=future',
        headers=auth_header,
    )
    assert [e['page_id'] for e in response.get_json()] == ['future']


def test_entries_due_excludes_future_follow_ups(
    client: FlaskClient,
    auth_header: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`follow_up=due` is the complement — what is actionable now."""
    monkeypatch.setattr(api, '_today_iso', lambda: '2026-08-06')
    entries = [
        make_entry(page_id='past', follow_up_date='2026-08-01'),
        make_entry(page_id='future', follow_up_date='2026-09-01'),
        make_entry(page_id='none', follow_up_date=None),
    ]
    patch_query(monkeypatch, entries)
    response = client.get(
        '/entries?status=Current+Project&follow_up=due',
        headers=auth_header,
    )
    assert sorted(e['page_id'] for e in response.get_json()) == [
        'none',
        'past',
    ]


def test_entries_filters_by_context(
    client: FlaskClient,
    auth_header: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entries = [
        make_entry(page_id='a', context='Computer'),
        make_entry(page_id='b', context='Phone'),
    ]
    patch_query(monkeypatch, entries)
    response = client.get(
        '/entries?status=Current+Project&context=Phone',
        headers=auth_header,
    )
    assert [e['page_id'] for e in response.get_json()] == ['b']


# endregion

# region PATCH /entry/<id>


def test_patch_entry_updates_only_supplied_fields(
    client: FlaskClient,
    auth_header: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    update = MagicMock()
    monkeypatch.setattr(api, 'update_page', update)
    monkeypatch.setattr(
        api,
        '_entry_response',
        lambda page_id: ({'page_id': page_id}, 200),
    )
    response = client.patch(
        '/entry/page-1',
        json={'context': 'Errands', 'due_date': '2026-09-01'},
        headers=auth_header,
    )
    assert response.status_code == 200
    props = update.call_args[0][1]
    assert props['Context'] == {'select': {'name': 'Errands'}}
    assert props['Due Date'] == {'date': {'start': '2026-09-01'}}
    # Nothing else should be touched — a PATCH is not a replace.
    assert set(props) == {'Context', 'Due Date'}


def test_patch_entry_rejects_unknown_field(
    client: FlaskClient,
    auth_header: dict[str, str],
) -> None:
    """Unknown keys are a client bug; silently ignoring them hides it."""
    response = client.patch(
        '/entry/page-1',
        json={'colour': 'blue'},
        headers=auth_header,
    )
    assert response.status_code == 400
    assert 'colour' in response.get_json()['error']


def test_patch_entry_rejects_empty_body(
    client: FlaskClient,
    auth_header: dict[str, str],
) -> None:
    response = client.patch('/entry/page-1', json={}, headers=auth_header)
    assert response.status_code == 400


def test_patch_entry_clears_a_field_with_empty_string(
    client: FlaskClient,
    auth_header: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`''` must clear rather than be dropped — that's how Area is unset."""
    update = MagicMock()
    monkeypatch.setattr(api, 'update_page', update)
    monkeypatch.setattr(
        api,
        '_entry_response',
        lambda page_id: ({'page_id': page_id}, 200),
    )
    client.patch('/entry/page-1', json={'area': ''}, headers=auth_header)
    assert update.call_args[0][1]['Area'] == {'select': None}


# endregion

# region notes


def test_get_notes_returns_page_body(
    client: FlaskClient,
    auth_header: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        api, 'get_page_body', MagicMock(return_value='some notes')
    )
    response = client.get('/entry/page-1/notes', headers=auth_header)
    assert response.status_code == 200
    assert response.get_json() == {'notes': 'some notes'}


def test_put_notes_replaces_page_body(
    client: FlaskClient,
    auth_header: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replace = MagicMock()
    monkeypatch.setattr(api, 'replace_page_body', replace)
    response = client.put(
        '/entry/page-1/notes',
        json={'notes': 'rewritten'},
        headers=auth_header,
    )
    assert response.status_code == 200
    replace.assert_called_once_with('page-1', 'rewritten')


def test_put_notes_requires_the_notes_key(
    client: FlaskClient,
    auth_header: dict[str, str],
) -> None:
    """Absent key is an error; an empty string is a legitimate erase."""
    response = client.put('/entry/page-1/notes', json={}, headers=auth_header)
    assert response.status_code == 400


# endregion

# region snooze


def test_snooze_defaults_to_tomorrow(
    client: FlaskClient,
    auth_header: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mirrors the TUI's `T` (wait_tomorrow) binding."""
    monkeypatch.setattr(api, '_today_iso', lambda: '2026-08-06')
    update = MagicMock()
    monkeypatch.setattr(api, 'update_page', update)
    monkeypatch.setattr(
        api,
        '_entry_response',
        lambda page_id: ({'page_id': page_id}, 200),
    )
    response = client.post('/entry/page-1/snooze', headers=auth_header)
    assert response.status_code == 200
    props = update.call_args[0][1]
    assert props['Follow-Up Date'] == {'date': {'start': '2026-08-07'}}


def test_snooze_accepts_explicit_days(
    client: FlaskClient,
    auth_header: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(api, '_today_iso', lambda: '2026-08-06')
    update = MagicMock()
    monkeypatch.setattr(api, 'update_page', update)
    monkeypatch.setattr(
        api,
        '_entry_response',
        lambda page_id: ({'page_id': page_id}, 200),
    )
    client.post('/entry/page-1/snooze', json={'days': 7}, headers=auth_header)
    props = update.call_args[0][1]
    assert props['Follow-Up Date'] == {'date': {'start': '2026-08-13'}}


def test_snooze_accepts_explicit_date(
    client: FlaskClient,
    auth_header: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    update = MagicMock()
    monkeypatch.setattr(api, 'update_page', update)
    monkeypatch.setattr(
        api,
        '_entry_response',
        lambda page_id: ({'page_id': page_id}, 200),
    )
    client.post(
        '/entry/page-1/snooze',
        json={'date': '2026-12-25'},
        headers=auth_header,
    )
    props = update.call_args[0][1]
    assert props['Follow-Up Date'] == {'date': {'start': '2026-12-25'}}


def test_snooze_rejects_bad_date(
    client: FlaskClient,
    auth_header: dict[str, str],
) -> None:
    response = client.post(
        '/entry/page-1/snooze',
        json={'date': 'next tuesday-ish'},
        headers=auth_header,
    )
    assert response.status_code == 400


# endregion

# region done / reschedule


def test_done_reschedules_instead_of_archiving_when_asked(
    client: FlaskClient,
    auth_header: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recurring items are rescheduled, not archived — the TUI's `D` choice."""
    archive = MagicMock()
    update = MagicMock()
    monkeypatch.setattr(api, 'archive_page', archive)
    monkeypatch.setattr(api, 'update_page', update)
    monkeypatch.setattr(
        api, '_get_page_by_id', MagicMock(return_value={'id': 'page-1'})
    )
    response = client.post(
        '/done/page-1',
        json={'reschedule': '2026-08-20'},
        headers=auth_header,
    )
    assert response.status_code == 200
    archive.assert_not_called()
    props = update.call_args[0][1]
    assert props['Follow-Up Date'] == {'date': {'start': '2026-08-20'}}


def test_done_still_archives_without_reschedule(
    client: FlaskClient,
    auth_header: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = MagicMock()
    monkeypatch.setattr(api, 'archive_page', archive)
    monkeypatch.setattr(
        api, '_get_page_by_id', MagicMock(return_value={'id': 'page-1'})
    )
    response = client.post('/done/page-1', headers=auth_header)
    assert response.status_code == 200
    archive.assert_called_once_with('page-1')


# endregion


# region complete-step


class TestCompleteStep:
    """`POST /entry/<id>/complete-step` — the TUI's `X` on an entry.

    The renumbering itself stays in `notion/models.advance_steps`, the same
    function the TUI calls. Reimplementing it in `app.js` would be a second
    definition of what a step list is, which is the drift this repo keeps
    getting bitten by.
    """

    def test_drops_the_first_step_and_renumbers(
        self,
        client: FlaskClient,
        auth_header: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        entry = make_entry(next_step='1. Draft it\n2. Send it\n3. Chase it')
        patch_query(monkeypatch, [entry])
        update = MagicMock()
        monkeypatch.setattr(api, 'update_page', update)
        monkeypatch.setattr(
            api,
            '_get_page_by_id',
            MagicMock(return_value={'id': 'entry-1'}),
        )

        response = client.post(
            '/entry/entry-1/complete-step', headers=auth_header
        )

        assert response.status_code == 200
        props = update.call_args[0][1]
        written = props['Next Actionable Step']['rich_text'][0]['text'][
            'content'
        ]
        assert written == '1. Send it\n2. Chase it'

    def test_last_step_leaves_the_field_empty(
        self,
        client: FlaskClient,
        auth_header: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        entry = make_entry(next_step='1. The only thing')
        patch_query(monkeypatch, [entry])
        update = MagicMock()
        monkeypatch.setattr(api, 'update_page', update)
        monkeypatch.setattr(
            api,
            '_get_page_by_id',
            MagicMock(return_value={'id': 'entry-1'}),
        )

        response = client.post(
            '/entry/entry-1/complete-step', headers=auth_header
        )

        assert response.status_code == 200
        props = update.call_args[0][1]
        written = props['Next Actionable Step']['rich_text'][0]['text'][
            'content'
        ]
        assert written == ''

    def test_nothing_to_complete_is_a_400_not_a_silent_no_op(
        self,
        client: FlaskClient,
        auth_header: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        entry = make_entry(next_step='')
        patch_query(monkeypatch, [entry])
        update = MagicMock()
        monkeypatch.setattr(api, 'update_page', update)
        monkeypatch.setattr(
            api,
            '_get_page_by_id',
            MagicMock(return_value={'id': 'entry-1'}),
        )

        response = client.post(
            '/entry/entry-1/complete-step', headers=auth_header
        )

        assert response.status_code == 400
        update.assert_not_called()

    def test_missing_entry_is_a_404(
        self,
        client: FlaskClient,
        auth_header: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            api, '_get_page_by_id', MagicMock(return_value=None)
        )

        response = client.post(
            '/entry/nope/complete-step', headers=auth_header
        )

        assert response.status_code == 404

    def test_requires_auth(self, client: FlaskClient) -> None:
        response = client.post('/entry/entry-1/complete-step')

        assert response.status_code == 401


# endregion


# region /next-steps — the due-date escape hatch


TODAY = '2026-08-11'


@pytest.fixture
def fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the server's idea of today so date tests don't drift."""
    monkeypatch.setattr(api, '_today_iso', MagicMock(return_value=TODAY))


def next_steps_headers(
    client: FlaskClient,
    auth_header: dict[str, str],
) -> list[str]:
    response = client.get('/next-steps', headers=auth_header)
    assert response.status_code == 200
    return [e['header'] for e in response.get_json()]


@pytest.mark.usefixtures('fixed_today')
def test_snoozed_item_stays_hidden(
    client: FlaskClient,
    auth_header: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No due date, deferred to next week — snoozing must still work."""
    patch_query(
        monkeypatch,
        [make_entry(header='Later', follow_up_date='2026-08-20')],
    )

    assert next_steps_headers(client, auth_header) == []


@pytest.mark.parametrize('due', ['2026-08-11', '2026-08-04'])
@pytest.mark.usefixtures('fixed_today')
def test_a_due_date_beats_a_future_snooze(
    client: FlaskClient,
    auth_header: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    due: str,
) -> None:
    """Due today or overdue surfaces however far ahead the snooze reaches.

    The webapp reimplements the TUI's Today gate client-side, so this is the
    same bug in a second place: an item due Wednesday and snoozed to Friday
    disappeared on Wednesday.
    """
    patch_query(
        monkeypatch,
        [make_entry(header='Owed', due_date=due, follow_up_date='2026-08-20')],
    )

    assert next_steps_headers(client, auth_header) == ['Owed']


@pytest.mark.usefixtures('fixed_today')
def test_a_future_due_date_does_not_defeat_a_snooze(
    client: FlaskClient,
    auth_header: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only a deadline that has arrived overrides the deferral."""
    patch_query(
        monkeypatch,
        [
            make_entry(
                header='Owed later',
                due_date='2026-09-01',
                follow_up_date='2026-08-20',
            )
        ],
    )

    assert next_steps_headers(client, auth_header) == []


# endregion


# region /inbox, /contexts — views the API used to define for itself


@pytest.mark.usefixtures('fixed_today')
def test_inbox_includes_a_project_with_no_next_step(
    client: FlaskClient,
    auth_header: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`/inbox` was `Status == "Triage"` only.

    A Current Project with no next action is the single most important
    thing a GTD inbox surfaces, and the TUI has always shown it. On the
    phone it was invisible, so the webapp reported inbox zero while the
    TUI showed a backlog.
    """
    patch_query(
        monkeypatch,
        [make_entry(header='Stalled', next_step='')],
    )

    response = client.get('/inbox', headers=auth_header)

    assert [e['header'] for e in response.get_json()] == ['Stalled']


@pytest.mark.usefixtures('fixed_today')
def test_inbox_drops_triaged_agenda_items(
    client: FlaskClient,
    auth_header: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The client-side half of the inbox definition reaches the API too."""
    patch_query(
        monkeypatch,
        [make_entry(header='@Sam: budget', next_step='', context='@Sam')],
    )

    response = client.get('/inbox', headers=auth_header)

    assert response.get_json() == []


@pytest.mark.usefixtures('fixed_today')
def test_next_steps_excludes_untriaged_entries(
    client: FlaskClient,
    auth_header: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`/next-steps` skipped the `context and next_step` gate entirely.

    An item with no next action belongs in the inbox, not on a list of
    things to do.
    """
    patch_query(
        monkeypatch,
        [
            make_entry(header='Ready', page_id='a'),
            make_entry(header='Stalled', page_id='b', next_step=''),
        ],
    )

    assert next_steps_headers(client, auth_header) == ['Ready']


@pytest.mark.usefixtures('fixed_today')
def test_next_steps_keeps_recurring_and_agenda_entries(
    client: FlaskClient,
    auth_header: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both are exempt from the gate — their header is the whole action."""
    patch_query(
        monkeypatch,
        [
            make_entry(
                page_id='a',
                header='Daily: trash',
                status='Recurring',
                context='',
                next_step='',
            ),
            make_entry(page_id='b', header='@Sam: budget', next_step=''),
        ],
    )

    assert sorted(next_steps_headers(client, auth_header)) == [
        '@Sam: budget',
        'Daily: trash',
    ]


@pytest.mark.usefixtures('fixed_today')
def test_contexts_offers_only_contexts_next_steps_can_return(
    client: FlaskClient,
    auth_header: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Otherwise an item is unreachable through the context picker.

    `/contexts` derived the view itself and missed the due-date escape
    hatch, so an overdue-but-snoozed item was returned by
    `/next-steps?context=X` under a context `/contexts` never listed.
    """
    patch_query(
        monkeypatch,
        [
            make_entry(
                page_id='a',
                context='Errands',
                due_date='2026-08-04',
                follow_up_date='2026-08-20',
            ),
            make_entry(page_id='b', context='Nowhere', next_step=''),
        ],
    )

    response = client.get('/contexts', headers=auth_header)

    assert response.get_json()['contexts'] == ['Errands']


# endregion


# region "Today" agrees across surfaces


class TestTodayIsTheMachinesToday:
    """One idea of today, taken from the clock the user is looking at.

    `api.py` pinned a hardcoded Eastern zone while the TUI used a naive
    `datetime.now()`, so date-gated views could disagree by a day.
    Each user runs their own instance, so system-local is both surfaces'
    answer — and it follows the user if they travel.
    """

    def test_matches_the_system_date(self) -> None:
        from datetime import datetime

        assert api._today_iso() == datetime.now().date().isoformat()  # noqa: SLF001

    def test_api_pins_no_timezone(self) -> None:
        from pathlib import Path

        source = Path(api.__file__).read_text()
        assert 'ZoneInfo(' not in source

    def test_webapp_does_not_use_utc(self) -> None:
        """`new Date().toISOString()` is UTC — a day early every US evening."""
        from pathlib import Path

        app_js = Path(api.__file__).parent / 'webapp' / 'app.js'
        assert '.toISOString().slice' not in app_js.read_text()


# endregion
