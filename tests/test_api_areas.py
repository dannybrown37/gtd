"""Tests for the Areas of Focus endpoints backing the webapp's Someday tab.

The TUI manages Areas directly on the Someday/Maybe tab (`(`/`+`/`-`/`)`);
the webapp had no way to reach them at all. These endpoints wrap the same
`notion/client.py` select-option CRUD the TUI calls.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from gtd import api

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


@pytest.fixture
def areas(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    get = MagicMock(return_value=['Work', 'Health'])
    monkeypatch.setattr(api, 'get_areas', get)
    return get


@pytest.mark.usefixtures('areas')
def test_get_areas_returns_them_sorted(
    client: FlaskClient,
    auth_header: dict[str, str],
) -> None:
    response = client.get('/areas', headers=auth_header)
    assert response.status_code == 200
    assert response.get_json() == {'areas': ['Health', 'Work']}


@pytest.mark.usefixtures('areas')
def test_get_areas_requires_auth(
    client: FlaskClient,
) -> None:
    assert client.get('/areas').status_code == 401


def test_get_areas_surfaces_notion_failure(
    client: FlaskClient,
    auth_header: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        api, 'get_areas', MagicMock(side_effect=RuntimeError('boom'))
    )
    response = client.get('/areas', headers=auth_header)
    assert response.status_code == 500


@pytest.mark.usefixtures('areas')
def test_post_area_creates_it(
    client: FlaskClient,
    auth_header: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add = MagicMock()
    monkeypatch.setattr(api, 'add_area', add)
    response = client.post(
        '/areas', headers=auth_header, json={'name': '  Finances  '}
    )
    assert response.status_code == 201
    add.assert_called_once_with('Finances')


@pytest.mark.parametrize('name', ['', '   ', None])
@pytest.mark.usefixtures('areas')
def test_post_area_rejects_an_empty_name(
    client: FlaskClient,
    auth_header: dict[str, str],
    name: str | None,
) -> None:
    body = {} if name is None else {'name': name}
    response = client.post('/areas', headers=auth_header, json=body)
    assert response.status_code == 400


@pytest.mark.parametrize('name', ['Work', 'work', 'WORK'])
@pytest.mark.usefixtures('areas')
def test_post_area_rejects_a_duplicate_case_insensitively(
    client: FlaskClient,
    auth_header: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> None:
    add = MagicMock()
    monkeypatch.setattr(api, 'add_area', add)
    response = client.post('/areas', headers=auth_header, json={'name': name})
    assert response.status_code == 409
    add.assert_not_called()


@pytest.mark.usefixtures('areas')
def test_delete_area_removes_it(
    client: FlaskClient,
    auth_header: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remove = MagicMock()
    monkeypatch.setattr(api, 'remove_area', remove)
    monkeypatch.setattr(api, 'query_database', MagicMock(return_value=[]))
    response = client.delete('/areas/work', headers=auth_header)
    assert response.status_code == 200
    remove.assert_called_once_with('Work')


@pytest.mark.usefixtures('areas')
def test_delete_area_refuses_while_it_still_has_entries(
    client: FlaskClient,
    auth_header: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Removing the option would orphan them, so the delete is refused."""
    remove = MagicMock()
    monkeypatch.setattr(api, 'remove_area', remove)
    monkeypatch.setattr(
        api, 'query_database', MagicMock(return_value=[{'id': 'p1'}])
    )
    response = client.delete('/areas/work', headers=auth_header)
    assert response.status_code == 409
    assert response.get_json()['count'] == 1
    remove.assert_not_called()


@pytest.mark.usefixtures('areas')
def test_delete_area_404s_on_an_unknown_area(
    client: FlaskClient,
    auth_header: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remove = MagicMock()
    monkeypatch.setattr(api, 'remove_area', remove)
    response = client.delete('/areas/Nonsense', headers=auth_header)
    assert response.status_code == 404
    remove.assert_not_called()


@pytest.mark.usefixtures('areas')
def test_patch_area_renames_it_and_rewrites_its_entries(
    client: FlaskClient,
    auth_header: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A renamed select option leaves entries pointing at the old value."""
    rename = MagicMock()
    update = MagicMock()
    query = MagicMock(return_value=[{'id': 'p1'}, {'id': 'p2'}])
    monkeypatch.setattr(api, 'rename_area', rename)
    monkeypatch.setattr(api, 'update_page', update)
    monkeypatch.setattr(api, 'query_database', query)

    response = client.patch(
        '/areas/Work', headers=auth_header, json={'new_name': 'Career'}
    )

    assert response.status_code == 200
    rename.assert_called_once_with('Work', 'Career')
    assert [c.args[0] for c in update.call_args_list] == ['p1', 'p2']


@pytest.mark.usefixtures('areas')
def test_patch_area_404s_on_an_unknown_area(
    client: FlaskClient,
    auth_header: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rename = MagicMock()
    monkeypatch.setattr(api, 'rename_area', rename)
    response = client.patch(
        '/areas/Nonsense', headers=auth_header, json={'new_name': 'Career'}
    )
    assert response.status_code == 404
    rename.assert_not_called()


@pytest.mark.parametrize('new_name', ['', '   ', None])
@pytest.mark.usefixtures('areas')
def test_patch_area_rejects_an_empty_new_name(
    client: FlaskClient,
    auth_header: dict[str, str],
    new_name: str | None,
) -> None:
    body = {} if new_name is None else {'new_name': new_name}
    response = client.patch('/areas/Work', headers=auth_header, json=body)
    assert response.status_code == 400


@pytest.mark.usefixtures('areas')
def test_patch_area_rejects_a_collision_with_another_area(
    client: FlaskClient,
    auth_header: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rename = MagicMock()
    monkeypatch.setattr(api, 'rename_area', rename)
    response = client.patch(
        '/areas/Work', headers=auth_header, json={'new_name': 'health'}
    )
    assert response.status_code == 409
    rename.assert_not_called()
