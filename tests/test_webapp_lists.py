"""Guards on the webapp's Lists tab affordances.

The TUI adds list items and manages list categories from the Lists tab
(`A`/`+`/`-`/`)`); the webapp could only browse them. These are text
assertions over the shipped JS — the webapp has no JS test harness.
"""

from __future__ import annotations

from pathlib import Path

import pytest

WEBAPP = Path(__file__).parent.parent / 'src' / 'gtd' / 'webapp'


@pytest.fixture(scope='module')
def app_js() -> str:
    return (WEBAPP / 'app.js').read_text()


def test_lists_render_a_section_with_category_controls(app_js: str) -> None:
    assert 'function renderListSection(' in app_js
    assert 'renderListSection(state.currentCategory' in app_js
    section = app_js.split('function renderListSection(')[1]
    assert 'openRenameCategoryModal(category)' in section
    assert 'confirmRemoveCategory(category, entries.length)' in section
    assert 'openAddListItemModal(category)' in section
    assert 'openNewCategoryModal' in section


@pytest.mark.parametrize(
    'capability',
    ['add_item', 'add_category', 'remove_category', 'rename_category'],
)
def test_list_actions_are_declared_capabilities(
    app_js: str,
    capability: str,
) -> None:
    assert f"'{capability}'," in app_js.split('];', maxsplit=1)[0]


@pytest.mark.parametrize(
    ('name', 'method', 'path'),
    [
        ('openNewCategoryModal', "method: 'POST'", "'/list-categories'"),
        (
            'openRenameCategoryModal',
            "method: 'PATCH'",
            '/list-categories/${encodeURI',
        ),
        (
            'confirmRemoveCategory',
            "method: 'DELETE'",
            '/list-categories/${encodeURI',
        ),
        ('openAddListItemModal', "method: 'POST'", '/list/${encodeURI'),
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


@pytest.mark.parametrize(
    ('func', 'noun'),
    [('confirmRemoveCategory', 'category'), ('confirmRemoveArea', 'area')],
)
def test_a_non_empty_group_cannot_be_removed(
    app_js: str,
    func: str,
    noun: str,
) -> None:
    """Deleting the select option would orphan everything still on it."""
    body = app_js.split(f'function {func}(')[1].split('\n}\n', maxsplit=1)[0]
    guard = body.split('openModal(')[0]
    assert 'if (count)' in guard
    assert 'move or drop them first' in guard
    assert 'return;' in guard
    assert noun in body


def test_an_empty_category_list_still_offers_a_way_out(app_js: str) -> None:
    assert 'function renderNewCategoryOnly(' in app_js
    assert 'renderNewCategoryOnly();' in app_js
