from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from gtd import api
from gtd.notion.models import ProjectEntry

if TYPE_CHECKING:
    from flask.testing import FlaskClient
    from collections.abc import Iterator


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[FlaskClient]:
    monkeypatch.setenv('GTD_API_KEY', 'test-key')
    api.app.config['TESTING'] = True
    with api.app.test_client() as c:
        yield c


@pytest.fixture
def auth_header() -> dict[str, str]:
    return {'Authorization': 'Bearer test-key'}


ROUTES = [
    ('post', '/capture'),
    ('get', '/list-categories'),
    ('get', '/list/test'),
]


@pytest.mark.parametrize(('method', 'route'), ROUTES)
def test_missing_auth_header_returns_401(
    client: FlaskClient,
    method: str,
    route: str,
) -> None:
    response = getattr(client, method)(route)
    assert response.status_code == 401


@pytest.mark.parametrize(('method', 'route'), ROUTES)
def test_wrong_api_key_returns_401(
    client: FlaskClient,
    method: str,
    route: str,
) -> None:
    headers = {'Authorization': 'Bearer wrong-key'}
    response = getattr(client, method)(route, headers=headers)
    assert response.status_code == 401


def test_capture_creates_page(
    client: FlaskClient,
    auth_header: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        api, '_create_page', MagicMock(return_value={'id': 'page-1'})
    )
    response = client.post(
        '/capture', json={'header': ' buy milk '}, headers=auth_header
    )
    assert response.status_code == 201
    assert response.get_json() == {'page_id': 'page-1', 'header': 'buy milk'}


def test_capture_rejects_empty_header(
    client: FlaskClient,
    auth_header: dict[str, str],
) -> None:
    response = client.post(
        '/capture', json={'header': '   '}, headers=auth_header
    )
    assert response.status_code == 400


def test_list_categories_returns_sorted_categories(
    client: FlaskClient,
    auth_header: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        api,
        'get_list_categories',
        MagicMock(return_value=['Books to Read', 'Movies', 'Articles']),
    )
    response = client.get('/list-categories', headers=auth_header)
    assert response.status_code == 200
    data = response.get_json()
    assert data['list_categories'] == ['Articles', 'Books to Read', 'Movies']


def test_list_categories_handles_notion_error(
    client: FlaskClient,
    auth_header: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        api,
        'get_list_categories',
        MagicMock(side_effect=Exception('Notion error')),
    )
    response = client.get('/list-categories', headers=auth_header)
    assert response.status_code == 500
    assert 'error' in response.get_json()


def test_list_by_category_returns_entries(
    client: FlaskClient,
    auth_header: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_entry = {
        'id': 'entry-1',
        'header': 'Read Python Book',
        'list_category': 'Books to Read',
        'due_date': '2026-08-01',
    }
    mock_project_entry = ProjectEntry(
        page_id='entry-1',
        header='Read Python Book',
        status='List',
        context=None,
        list_category='Books to Read',
        next_step='',
        success_condition='',
        due_date='2026-08-01',
        follow_up_date=None,
        created_date='2026-07-24',
        updated_date='2026-07-24',
    )
    monkeypatch.setattr(
        api,
        'get_list_categories',
        MagicMock(return_value=['Books to Read', 'Movies']),
    )
    monkeypatch.setattr(
        api,
        'query_database',
        MagicMock(return_value=[mock_entry]),
    )
    monkeypatch.setattr(
        api.ProjectEntry,
        'from_page',
        MagicMock(return_value=mock_project_entry),
    )
    response = client.get('/list/Books%20to%20Read', headers=auth_header)
    assert response.status_code == 200
    data = response.get_json()
    assert len(data) == 1
    assert data[0]['header'] == 'Read Python Book'


def test_list_by_category_invalid_returns_404(
    client: FlaskClient,
    auth_header: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        api,
        'get_list_categories',
        MagicMock(return_value=['Books to Read', 'Movies']),
    )
    response = client.get('/list/InvalidCategory', headers=auth_header)
    assert response.status_code == 404
    assert 'error' in response.get_json()


def test_list_by_category_case_insensitive(
    client: FlaskClient,
    auth_header: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        api,
        'get_list_categories',
        MagicMock(return_value=['Books to Read']),
    )
    monkeypatch.setattr(
        api,
        'query_database',
        MagicMock(return_value=[]),
    )
    response = client.get('/list/books%20to%20read', headers=auth_header)
    assert response.status_code == 200
    assert response.get_json() == []


def test_list_by_category_handles_notion_error(
    client: FlaskClient,
    auth_header: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        api,
        'get_list_categories',
        MagicMock(side_effect=Exception('Notion error')),
    )
    response = client.get('/list/test', headers=auth_header)
    assert response.status_code == 500
    assert 'error' in response.get_json()
