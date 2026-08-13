"""Tests for the weekly review state endpoints backing the webapp.

The review's *work* is done through the entry endpoints that already exist
(`/inbox`, `/entries`, `PATCH /entry`, `/done`, `/capture`, `/areas`); these
four endpoints carry only the checklist. That is deliberate — they read and
write the same `weekly_habits.json` the TUI does, so progress ticked off on
the phone shows up in the terminal, and no Notion call can take the checklist
down.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import pytest

from gtd import api, storage

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from flask.testing import FlaskClient


@pytest.fixture(autouse=True)
def _isolated_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(storage, 'OUTPUT_PATH', tmp_path)
    monkeypatch.setattr(
        storage, 'HABITS_PATH', tmp_path / 'weekly_habits.json'
    )


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[FlaskClient]:
    monkeypatch.setenv('GTD_API_KEY', 'test-key')
    api.app.config['TESTING'] = True
    with api.app.test_client() as c:
        yield c


@pytest.fixture
def auth_header() -> dict[str, str]:
    return {'Authorization': 'Bearer test-key'}


@pytest.mark.parametrize(
    ('method', 'path'),
    [
        ('get', '/review'),
        ('post', '/review/step/0'),
        ('post', '/review/reset'),
        ('post', '/review/complete'),
    ],
)
def test_review_endpoints_require_auth(
    client: FlaskClient,
    method: str,
    path: str,
) -> None:
    assert getattr(client, method)(path).status_code == 401


class TestGetReview:
    def test_serves_every_step_from_the_shared_definition(
        self,
        client: FlaskClient,
        auth_header: dict[str, str],
    ) -> None:
        """The webapp must not hand-copy the step list — it reads this."""
        payload = client.get('/review', headers=auth_header).get_json()

        assert [(s['label'], s['action']) for s in payload['steps']] == list(
            storage.REVIEW_STEPS
        )
        assert [s['index'] for s in payload['steps']] == list(
            range(len(storage.REVIEW_STEPS))
        )

    def test_starts_with_nothing_done(
        self,
        client: FlaskClient,
        auth_header: dict[str, str],
    ) -> None:
        payload = client.get('/review', headers=auth_header).get_json()

        assert all(not s['done'] for s in payload['steps'])
        assert payload['done_this_week'] is False
        assert payload['last_done'] is None
        assert payload['week_start'] == storage.current_week_start()

    def test_never_touches_notion(
        self,
        client: FlaskClient,
        auth_header: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A Notion outage must not take the checklist down with it."""

        def _explode(*_args: object, **_kwargs: object) -> None:
            msg = 'the review checklist queried Notion'
            raise AssertionError(msg)

        monkeypatch.setattr(api, 'query_database', _explode)
        monkeypatch.setattr(api, 'inbox_entries', _explode)

        assert client.get('/review', headers=auth_header).status_code == 200


class TestReviewStep:
    def test_checking_a_step_persists(
        self,
        client: FlaskClient,
        auth_header: dict[str, str],
    ) -> None:
        response = client.post(
            '/review/step/1', json={'done': True}, headers=auth_header
        )

        assert response.status_code == 200
        assert response.get_json()['steps'][1]['done'] is True
        after = client.get('/review', headers=auth_header).get_json()
        assert after['steps'][1]['done'] is True

    def test_unchecking_a_step_persists(
        self,
        client: FlaskClient,
        auth_header: dict[str, str],
    ) -> None:
        client.post('/review/step/1', json={'done': True}, headers=auth_header)
        client.post(
            '/review/step/1', json={'done': False}, headers=auth_header
        )

        after = client.get('/review', headers=auth_header).get_json()
        assert after['steps'][1]['done'] is False

    def test_leaves_other_steps_alone(
        self,
        client: FlaskClient,
        auth_header: dict[str, str],
    ) -> None:
        client.post('/review/step/0', json={'done': True}, headers=auth_header)
        client.post('/review/step/2', json={'done': True}, headers=auth_header)

        steps = client.get('/review', headers=auth_header).get_json()['steps']
        assert [s['index'] for s in steps if s['done']] == [0, 2]

    def test_the_tui_sees_what_the_phone_ticked(
        self,
        client: FlaskClient,
        auth_header: dict[str, str],
    ) -> None:
        """One file, two front ends — the whole point of local state."""
        client.post('/review/step/3', json={'done': True}, headers=auth_header)

        assert storage.load_review_state(len(storage.REVIEW_STEPS))[3] is True

    @pytest.mark.parametrize('index', [-1, 99])
    def test_rejects_an_index_outside_the_checklist(
        self,
        client: FlaskClient,
        auth_header: dict[str, str],
        index: int,
    ) -> None:
        response = client.post(
            f'/review/step/{index}', json={'done': True}, headers=auth_header
        )

        assert response.status_code in {404, 405}

    @pytest.mark.parametrize('body', [{}, {'done': 'yes'}, {'done': 1}])
    def test_rejects_a_body_without_a_boolean_done(
        self,
        client: FlaskClient,
        auth_header: dict[str, str],
        body: dict,
    ) -> None:
        response = client.post(
            '/review/step/0', json=body, headers=auth_header
        )

        assert response.status_code == 400
        assert 'done' in response.get_json()['error']


class TestReviewResetAndComplete:
    def test_reset_clears_every_step(
        self,
        client: FlaskClient,
        auth_header: dict[str, str],
    ) -> None:
        client.post('/review/step/0', json={'done': True}, headers=auth_header)

        payload = client.post('/review/reset', headers=auth_header).get_json()

        assert all(not s['done'] for s in payload['steps'])

    def test_complete_marks_the_habit_done_this_week(
        self,
        client: FlaskClient,
        auth_header: dict[str, str],
    ) -> None:
        payload = client.post(
            '/review/complete', headers=auth_header
        ).get_json()

        assert payload['done_this_week'] is True
        assert payload['last_done'] == datetime.now().date().isoformat()

    def test_complete_is_what_the_tui_habit_row_reads(
        self,
        client: FlaskClient,
        auth_header: dict[str, str],
    ) -> None:
        client.post('/review/complete', headers=auth_header)

        assert storage.habit_done_this_week(storage.WEEKLY_REVIEW_HABIT)

    def test_reset_clears_the_completion_marker_too(
        self,
        client: FlaskClient,
        auth_header: dict[str, str],
    ) -> None:
        """`X` in the TUI means "I want to do this week's review again"."""
        client.post('/review/complete', headers=auth_header)

        payload = client.post('/review/reset', headers=auth_header).get_json()

        assert payload['done_this_week'] is False
