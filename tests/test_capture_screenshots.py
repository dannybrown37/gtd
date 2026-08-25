"""The docs screenshot capture script must not fall behind the TUI.

`_TABS` used to be a hardcoded list, and it silently missed the Calendar
tab for a whole release: nothing failed, the README just showed an app
that no longer existed. The tab list is now derived from the running app,
and these tests are what keep it that way — plus a guard that every
screenshot the README links to is still one the script produces.
"""

from __future__ import annotations

import asyncio
import importlib.util
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from gtd.gtd_tui import GTDApp
from textual.widgets import TabPane, TabbedContent


if TYPE_CHECKING:
    from types import ModuleType

_ROOT = Path(__file__).resolve().parent.parent
_README = _ROOT / 'README.md'


def _load_capture() -> ModuleType:
    """Import scripts/capture_screenshots.py, which is outside the package."""
    path = _ROOT / 'scripts' / 'capture_screenshots.py'
    spec = importlib.util.spec_from_file_location('capture_screenshots', path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


capture = _load_capture()


async def _app_tab_ids() -> list[str]:
    app = GTDApp()
    async with app.run_test(size=(130, 45)):
        tabs = app.query_one('#tabs', TabbedContent)
        return [pane.id for pane in tabs.query(TabPane) if pane.id]


@pytest.fixture(scope='module')
def tab_ids() -> list[str]:
    return asyncio.run(_app_tab_ids())


@pytest.fixture(scope='module')
def discovered(tab_ids: list[str]) -> list[tuple[str, str]]:
    return capture.discover_tabs(tab_ids)


def test_every_tab_in_the_app_is_discovered(
    tab_ids: list[str],
    discovered: list[tuple[str, str]],
) -> None:
    """The whole point: a new TabPane needs no edit to the script."""
    assert [tab_id for tab_id, _ in discovered] == tab_ids


def test_calendar_is_discovered(discovered: list[tuple[str, str]]) -> None:
    """The tab the old hardcoded list missed."""
    assert 'tab-calendar' in {tab_id for tab_id, _ in discovered}


def test_slugs_are_unique(discovered: list[tuple[str, str]]) -> None:
    """Two tabs sharing a slug would silently overwrite one screenshot."""
    slugs = [slug for _, slug in discovered]
    assert len(slugs) == len(set(slugs))


def test_no_stale_slug_override(tab_ids: list[str]) -> None:
    """An override for a tab that no longer exists is dead weight."""
    assert set(capture.SLUG_OVERRIDES) <= set(tab_ids)


@pytest.mark.parametrize(
    'tab_id',
    ['tab-next-steps', 'tab-waiting', 'tab-snoozed', 'tab-lists'],
)
def test_readme_slugs_still_produced(
    tab_id: str,
    discovered: list[tuple[str, str]],
) -> None:
    """README <img> paths must keep resolving after the refactor."""
    slugs = dict(discovered)
    referenced = set(
        re.findall(r'docs/screenshots/([a-z0-9-]+)\.svg', _README.read_text()),
    )
    assert slugs[tab_id] in referenced


def test_private_tabs_skipped_by_default(
    discovered: list[tuple[str, str]],
) -> None:
    """Calendar renders the user's real meetings; opt in, never default."""
    default = capture.select_tabs(discovered, with_private=False)
    assert 'tab-calendar' not in {tab_id for tab_id, _ in default}

    opted_in = capture.select_tabs(discovered, with_private=True)
    assert 'tab-calendar' in {tab_id for tab_id, _ in opted_in}
