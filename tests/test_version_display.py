"""Version must be visible in every surface a human looks at.

Shipping a release is only useful if you can tell, from the thing in front of
you, which version you're running. `gtd --version` covered the CLI; the TUI and
the webapp had no way to say. All three read the same installed package
metadata via `gtd.version.get_version()`.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from gtd import api
from gtd.gtd_tui import GTDApp
from gtd.version import get_version

if TYPE_CHECKING:
    from collections.abc import Iterator

    from flask.testing import FlaskClient

WEBAPP = Path(api.__file__).parent / 'webapp'


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[FlaskClient]:
    monkeypatch.setenv('GTD_API_KEY', 'test-key')
    api.app.config['TESTING'] = True
    with api.app.test_client() as c:
        yield c


class TestGetVersion:
    def test_returns_a_non_empty_string(self) -> None:
        assert get_version()

    def test_falls_back_when_package_is_not_installed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from importlib.metadata import PackageNotFoundError

        import gtd.version as version_mod

        def boom(_name: str) -> str:
            raise PackageNotFoundError

        monkeypatch.setattr(version_mod, 'metadata_version', boom)
        assert version_mod.get_version() == 'dev'


class TestTuiShowsVersion:
    def test_sub_title_is_the_version(self) -> None:
        assert f'v{get_version()}' == GTDApp.SUB_TITLE


class TestVersionEndpoint:
    def test_returns_the_version(self, client: FlaskClient) -> None:
        resp = client.get(
            '/version', headers={'Authorization': 'Bearer test-key'}
        )
        assert resp.status_code == 200
        assert resp.get_json() == {'version': get_version()}

    def test_requires_auth(self, client: FlaskClient) -> None:
        assert client.get('/version').status_code == 401


class TestWebappShowsVersion:
    def test_nav_menu_has_a_version_slot(self) -> None:
        assert 'nav-version' in (WEBAPP / 'index.html').read_text()

    def test_app_js_loads_and_renders_it(self) -> None:
        js = (WEBAPP / 'app.js').read_text()
        assert "'/version'" in js
        assert 'nav-version' in js

    def test_styled_to_be_unobtrusive(self) -> None:
        assert '.nav-version' in (WEBAPP / 'styles.css').read_text()
