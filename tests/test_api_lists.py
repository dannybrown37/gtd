"""Tests for the List Category endpoints backing the webapp's Lists tab.

The TUI manages list categories on the Lists tab (`+`/`-`/`)`) and adds items
with `A`; the webapp had no way to reach any of it. These endpoints wrap the
same `notion/client.py` select-option CRUD the TUI calls.
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
def categories(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    get = MagicMock(return_value=['Books to Read', 'Movies'])
    monkeypatch.setattr(api, 'get_list_categories', get)
    return get


@pytest.fixture
def empty_db(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    query = MagicMock(return_value=[])
    monkeypatch.setattr(api, 'query_database', query)
    return query


@pytest.mark.usefixtures('categories')
def test_post_category_creates_it(
    client: FlaskClient,
    auth_header: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add = MagicMock()
    monkeypatch.setattr(api, 'add_list_category', add)
    response = client.post(
        '/list-categories', headers=auth_header, json={'name': 'Podcasts'}
    )
    assert response.status_code == 201
    add.assert_called_once_with('Podcasts')


@pytest.mark.usefixtures('categories')
@pytest.mark.parametrize('name', ['Movies', 'movies', '  MOVIES '])
def test_post_category_rejects_a_duplicate(
    client: FlaskClient,
    auth_header: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> None:
    add = MagicMock()
    monkeypatch.setattr(api, 'add_list_category', add)
    response = client.post(
        '/list-categories', headers=auth_header, json={'name': name}
    )
    assert response.status_code == 409
    add.assert_not_called()


@pytest.mark.usefixtures('categories')
def test_post_category_requires_a_name(
    client: FlaskClient,
    auth_header: dict[str, str],
) -> None:
    response = client.post('/list-categories', headers=auth_header, json={})
    assert response.status_code == 400


def test_post_category_requires_auth(client: FlaskClient) -> None:
    assert client.post('/list-categories', json={'name': 'X'}).status_code == (
        401
    )


@pytest.mark.usefixtures('categories', 'empty_db')
def test_delete_category_removes_an_empty_one(
    client: FlaskClient,
    auth_header: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remove = MagicMock()
    monkeypatch.setattr(api, 'remove_list_category', remove)
    response = client.delete('/list-categories/movies', headers=auth_header)
    assert response.status_code == 200
    remove.assert_called_once_with('Movies')


@pytest.mark.usefixtures('categories')
def test_delete_category_refuses_while_it_still_has_items(
    client: FlaskClient,
    auth_header: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Removing the option would orphan them, so the delete is refused."""
    remove = MagicMock()
    monkeypatch.setattr(api, 'remove_list_category', remove)
    monkeypatch.setattr(
        api,
        'query_database',
        MagicMock(return_value=[{'id': 'p1'}, {'id': 'p2'}]),
    )
    response = client.delete('/list-categories/Movies', headers=auth_header)
    assert response.status_code == 409
    assert response.get_json()['count'] == 2
    remove.assert_not_called()


@pytest.mark.usefixtures('categories', 'empty_db')
def test_delete_category_404s_on_an_unknown_one(
    client: FlaskClient,
    auth_header: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remove = MagicMock()
    monkeypatch.setattr(api, 'remove_list_category', remove)
    response = client.delete('/list-categories/Nonsense', headers=auth_header)
    assert response.status_code == 404
    remove.assert_not_called()


@pytest.mark.usefixtures('categories')
def test_patch_category_renames_it_and_rewrites_its_entries(
    client: FlaskClient,
    auth_header: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rename = MagicMock()
    update = MagicMock()
    monkeypatch.setattr(api, 'rename_list_category', rename)
    monkeypatch.setattr(api, 'update_page', update)
    monkeypatch.setattr(
        api,
        'query_database',
        MagicMock(return_value=[{'id': 'p1'}, {'id': 'p2'}]),
    )
    response = client.patch(
        '/list-categories/Movies',
        headers=auth_header,
        json={'new_name': 'Films'},
    )
    assert response.status_code == 200
    rename.assert_called_once_with('Movies', 'Films')
    assert [c.args[0] for c in update.call_args_list] == ['p1', 'p2']


@pytest.mark.usefixtures('categories')
def test_patch_category_rejects_a_collision(
    client: FlaskClient,
    auth_header: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rename = MagicMock()
    monkeypatch.setattr(api, 'rename_list_category', rename)
    response = client.patch(
        '/list-categories/Movies',
        headers=auth_header,
        json={'new_name': 'books to read'},
    )
    assert response.status_code == 409
    rename.assert_not_called()


@pytest.mark.usefixtures('categories')
def test_post_list_item_creates_it_in_the_category(
    client: FlaskClient,
    auth_header: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create = MagicMock(return_value={'id': 'new-page'})
    update = MagicMock()
    monkeypatch.setattr(api, '_create_page', create)
    monkeypatch.setattr(api, 'update_page', update)
    response = client.post(
        '/list/movies',
        headers=auth_header,
        json={'header': 'Arrival', 'next_step': 'on Netflix'},
    )
    assert response.status_code == 201
    create.assert_called_once_with('Arrival')
    page_id, props = update.call_args.args
    assert page_id == 'new-page'
    assert props['Status'] == {'select': {'name': 'List'}}
    assert props['List Category'] == {'select': {'name': 'Movies'}}


@pytest.mark.usefixtures('categories')
def test_post_list_item_requires_a_header(
    client: FlaskClient,
    auth_header: dict[str, str],
) -> None:
    response = client.post('/list/Movies', headers=auth_header, json={})
    assert response.status_code == 400


@pytest.mark.usefixtures('categories')
def test_post_list_item_404s_on_an_unknown_category(
    client: FlaskClient,
    auth_header: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create = MagicMock()
    monkeypatch.setattr(api, '_create_page', create)
    response = client.post(
        '/list/Nonsense', headers=auth_header, json={'header': 'x'}
    )
    assert response.status_code == 404
    create.assert_not_called()
