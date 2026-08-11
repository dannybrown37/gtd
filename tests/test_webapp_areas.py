"""Guards on the webapp's Areas of Focus affordances.

The TUI manages Areas from the Someday/Maybe tab (`(`/`+`/`-`/`)`); the
webapp could neither assign nor create nor delete them. These are text
assertions over the shipped JS/CSS — the webapp has no JS test harness.
"""

from __future__ import annotations

from pathlib import Path

import pytest

WEBAPP = Path(__file__).parent.parent / 'src' / 'gtd' / 'webapp'


@pytest.fixture(scope='module')
def app_js() -> str:
    return (WEBAPP / 'app.js').read_text()


@pytest.fixture(scope='module')
def styles() -> str:
    return (WEBAPP / 'styles.css').read_text()


def test_someday_has_its_own_loader(app_js: str) -> None:
    """The generic /entries loader can't group or offer Area CRUD."""
    assert "kind: 'someday'" in app_js
    assert 'async function loadSomeday()' in app_js
    assert "if (view.kind === 'someday') return loadSomeday();" in app_js


def test_someday_renders_area_sections(app_js: str) -> None:
    assert 'function renderAreaSections(' in app_js
    assert 'renderAreaSections(state.entries, state.areas' in app_js


def test_someday_offers_area_filter_chips(app_js: str) -> None:
    """Chips filter; sections group. The user asked for both."""
    assert 'renderChips(' in app_js.split('async function loadSomeday()')[1]
    assert 'NO_AREA' in app_js


@pytest.mark.parametrize(
    'capability',
    ['set_area', 'add_area', 'remove_area', 'rename_area'],
)
def test_area_actions_are_declared_capabilities(
    app_js: str,
    capability: str,
) -> None:
    assert f"'{capability}'," in app_js.split('];', maxsplit=1)[0]


@pytest.mark.parametrize(
    ('name', 'method', 'path'),
    [
        ('openNewAreaModal', "method: 'POST'", "'/areas'"),
        ('openRenameAreaModal', "method: 'PATCH'", '/areas/${encodeURI'),
        ('confirmRemoveArea', "method: 'DELETE'", '/areas/${encodeURI'),
    ],
)
def test_each_crud_modal_calls_its_endpoint(
    app_js: str,
    name: str,
    method: str,
    path: str,
) -> None:
    body = app_js.split(f'function {name}(')[1].split('\n}\n', maxsplit=1)[0]
    assert method in body
    assert path in body


def test_assigning_an_area_is_reachable_from_the_action_sheet(
    app_js: str,
) -> None:
    assert 'data-act="area">Assign Area' in app_js
    assert "if (act === 'area') openAreaPicker(entry);" in app_js


def test_clearing_an_area_sends_an_empty_string(app_js: str) -> None:
    """`(no area)` is a display label, not a value to write into Notion."""
    picker = app_js.split('async function openAreaPicker(')[1]
    assert "value === '(no area)' ? '' : value" in picker


def test_area_controls_are_touch_targets(styles: str) -> None:
    for selector in ('.group-btn', '.group-add-btn'):
        block = styles.split(selector)[1].split('}', maxsplit=1)[0]
        assert 'min-height: 44px' in block


def test_app_js_is_plain_text(app_js: str) -> None:
    """A stray control byte turns the served asset binary and breaks diffs."""
    assert '\x00' not in app_js
