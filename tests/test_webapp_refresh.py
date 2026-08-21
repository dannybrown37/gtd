"""Guards on the webapp's refresh affordance.

The TUI can reload the current tab from anywhere (`r`); the webapp had no
equivalent, so the only way to re-fetch was to navigate to another view and
back. These are text assertions over the shipped HTML/JS — the webapp has no
JS test infrastructure.
"""

from __future__ import annotations

from pathlib import Path

import pytest

WEBAPP = Path(__file__).parent.parent / 'src' / 'gtd' / 'webapp'


@pytest.fixture(scope='module')
def index_html() -> str:
    return (WEBAPP / 'index.html').read_text()


@pytest.fixture(scope='module')
def app_js() -> str:
    return (WEBAPP / 'app.js').read_text()


@pytest.fixture(scope='module')
def styles() -> str:
    return (WEBAPP / 'styles.css').read_text()


def test_topbar_has_a_refresh_button(index_html: str) -> None:
    assert 'id="refresh-btn"' in index_html
    assert 'aria-label="Refresh"' in index_html


def test_refresh_button_is_a_touch_target(index_html: str) -> None:
    """It must carry `icon-btn`, which is what sets the 44px minimum."""
    button = next(
        line for line in index_html.splitlines() if 'id="refresh-btn"' in line
    )
    assert 'icon-btn' in button


def test_refresh_button_reloads_the_active_view(app_js: str) -> None:
    assert "$('#refresh-btn').addEventListener" in app_js
    assert 'refreshActiveView' in app_js


def test_refresh_is_declared_as_a_capability(app_js: str) -> None:
    """Parity's webapp side is declared, so `refresh` must stay listed."""
    assert "'refresh'," in app_js


def test_refresh_shows_it_is_working(styles: str) -> None:
    """A tap with no feedback reads as a dead button on a slow connection."""
    assert '.spinning' in styles


def test_refresh_drops_the_cached_schema(app_js: str) -> None:
    """A category added elsewhere never appeared until a full page reload."""
    body = app_js.split('async function refreshActiveView')[1][:400]
    assert 'invalidateSchema()' in body


def test_mutations_drop_the_cached_schema(app_js: str) -> None:
    """Adding/renaming a category or area edits the schema it caches."""
    body = app_js.split('async function mutateAndReload')[1][:400]
    assert 'invalidateSchema()' in body


def test_invalidate_schema_clears_the_cache(app_js: str) -> None:
    assert 'function invalidateSchema() {' in app_js
    assert 'state.schema = null;' in app_js
