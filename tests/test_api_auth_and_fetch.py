"""API key checking and the raw page fetch every per-entry route relies on."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import httpx
import pytest

from gtd import api

get_page_by_id = api._get_page_by_id  # noqa: SLF001

if TYPE_CHECKING:
    from collections.abc import Iterator

    from flask.testing import FlaskClient


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[FlaskClient]:
    monkeypatch.setenv('GTD_API_KEY', 'test-key')
    api.app.config['TESTING'] = True
    with api.app.test_client() as c:
        yield c


@pytest.mark.parametrize(
    'authorization',
    [
        pytest.param('Bearer test-ke', id='prefix-of-key'),
        pytest.param('Bearer test-key2', id='key-plus-suffix'),
        pytest.param('Bearer ', id='empty-token'),
        pytest.param('bearer test-key', id='lowercase-scheme'),
        pytest.param('test-key', id='no-scheme'),
        pytest.param('Bearer tëst-key', id='non-ascii'),
    ],
)
def test_near_miss_keys_are_rejected(
    client: FlaskClient, authorization: str
) -> None:
    response = client.get('/version', headers={'Authorization': authorization})
    assert response.status_code == 401


def test_exact_key_is_accepted(client: FlaskClient) -> None:
    response = client.get(
        '/version', headers={'Authorization': 'Bearer test-key'}
    )
    assert response.status_code == 200


def test_missing_server_key_fails_closed(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv('GTD_API_KEY')
    response = client.get('/version', headers={'Authorization': 'Bearer '})
    assert response.status_code == 500


@pytest.mark.parametrize(
    'error',
    [
        httpx.ConnectError('down'),
        httpx.ReadTimeout('slow'),
    ],
    ids=['connect', 'timeout'],
)
def test_get_page_by_id_returns_none_when_notion_unreachable(
    monkeypatch: pytest.MonkeyPatch, error: httpx.HTTPError
) -> None:
    monkeypatch.setattr(httpx, 'get', MagicMock(side_effect=error))
    assert get_page_by_id('page-1') is None


def test_done_is_404_not_500_when_notion_unreachable(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        httpx, 'get', MagicMock(side_effect=httpx.ConnectError('down'))
    )
    response = client.post(
        '/done/page-1', headers={'Authorization': 'Bearer test-key'}
    )
    assert response.status_code == 404


def test_get_page_by_id_uses_the_configured_notion_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`gtd init` stores the token in config, not the environment."""
    monkeypatch.delenv('NOTION_NOTES_TOKEN')
    monkeypatch.setattr(
        'gtd.notion.client.get_config_value',
        lambda key: 'config-token' if key == 'token' else None,
    )
    get = MagicMock()
    get.return_value.status_code = 200
    get.return_value.is_success = True
    get.return_value.json.return_value = {'id': 'page-1'}
    monkeypatch.setattr(httpx, 'get', get)

    assert get_page_by_id('page-1') == {'id': 'page-1'}
    headers = get.call_args.kwargs['headers']
    assert headers['Authorization'] == 'Bearer config-token'


def test_get_page_by_id_returns_none_on_notion_error_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = MagicMock(status_code=404, is_success=False, text='')
    monkeypatch.setattr(httpx, 'get', MagicMock(return_value=response))
    assert get_page_by_id('page-1') is None
