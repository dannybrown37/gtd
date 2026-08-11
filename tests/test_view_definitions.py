"""No surface may define a view for itself.

`tests/test_notion_views.py` pins *what* each view contains.
`tests/test_webapp_parity.py` pins which *actions* each front end offers.
Neither notices a surface quietly growing its own copy of a view — which is
how `/inbox` ended up meaning `Status == "Triage"` while the TUI meant
something six clauses wider, and how `/next-steps` ended up without the
next-step gate. Both shipped, both passed CI, and the phone reported inbox
zero for months while the TUI showed a backlog.

So this file tests the *shape of the code*, not its behaviour: the string
`'property': 'Status'` may appear in exactly one module. Anything that
needs entries asks `gtd.notion.views` for them.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from gtd import api, gtd_tui
from gtd.notion import views


SRC = Path(views.__file__).parent.parent

# Every module that fetches entries. `views` is the one allowed to say what
# a view is; everything else consumes it.
CONSUMERS = [
    'api.py',
    'gtd_tui.py',
    'notion/triage.py',
    'notion/today.py',
    'notion/log.py',
    'notion/review.py',
]

# `entries.py` still builds its own status query for the `gtd update` picker
# ("everything except Triage"), which is a picker scope rather than a view
# any front end renders. If a tab or an endpoint ever wants that list, move
# it into `views.py` first.
NOT_A_VIEW = {'notion/entries.py'}


def _source(relative: str) -> str:
    return (SRC / relative).read_text()


@pytest.mark.parametrize('module', CONSUMERS)
def test_no_status_filters_outside_views(module: str) -> None:
    """A Notion Status filter is a view definition by another name."""
    assert "'property': 'Status'" not in _source(module), (
        f'{module} builds its own Status filter. Define the view in '
        f'gtd/notion/views.py and call it from here instead.'
    )


@pytest.mark.parametrize('module', CONSUMERS)
def test_consumers_do_not_parse_notion_result_sets(module: str) -> None:
    """Parsing a *result set* means a raw query happened just above it.

    Parsing a single page is fine and stays — `/entry/<id>` fetches one
    known page by id, which is a lookup, not a view.
    """
    assert 'ProjectEntry.from_page(p) for p in' not in _source(module), (
        f'{module} parses a page result set, so it is querying directly '
        f'too. Fetch through gtd.notion.views.'
    )


def test_every_status_query_helper_lives_in_views() -> None:
    """The union of the above, stated once as a whole-package sweep."""
    offenders = [
        str(path.relative_to(SRC))
        for path in SRC.rglob('*.py')
        if "'property': 'Status'" in path.read_text()
        and str(path.relative_to(SRC)) not in {'notion/views.py', *NOT_A_VIEW}
    ]

    assert offenders == [], (
        f'Status filters outside gtd/notion/views.py: {offenders}'
    )


# --- The TUI tabs declare their view rather than building it ---


def _tab_widgets() -> list[type]:
    return [
        obj
        for _, obj in inspect.getmembers(gtd_tui, inspect.isclass)
        if issubclass(obj, gtd_tui.BaseEntryContent)
        and obj is not gtd_tui.BaseEntryContent
    ]


def test_every_tab_declares_or_overrides_its_view() -> None:
    """A tab with neither would silently query the empty status."""
    undeclared = [
        cls.__name__
        for cls in _tab_widgets()
        if not cls.VIEW_STATUS and '_fetch' not in vars(cls)
    ]

    assert undeclared == [], (
        f'Tabs with no VIEW_STATUS and no _fetch override: {undeclared}'
    )


def test_tab_view_statuses_are_real_statuses() -> None:
    from gtd.notion.schema import STATUSES

    for cls in _tab_widgets():
        declared = cls.VIEW_STATUS
        statuses = [declared] if isinstance(declared, str) else declared
        for status in statuses:
            assert not status or status in STATUSES, (
                f'{cls.__name__}.VIEW_STATUS has unknown status {status!r}'
            )


def test_no_tab_reintroduces_build_filter() -> None:
    """The hook that used to let each tab define its own view.

    It was replaced by `VIEW_STATUS`/`_fetch` so that a tab can only name a
    view, never describe one. Re-adding it re-opens the drift.
    """
    for cls in [gtd_tui.BaseEntryContent, *_tab_widgets()]:
        assert not hasattr(cls, '_build_filter'), (
            f'{cls.__name__} defines _build_filter again'
        )


# --- The API endpoints are wrappers, not definitions ---


VIEW_ENDPOINTS = {
    'inbox': 'inbox_entries',
    'entries': 'entries_for_status',
    'next_steps': 'next_steps_entries',
    'contexts': 'next_steps_entries',
    'get_list': 'entries_for_status',
}


@pytest.mark.parametrize(('endpoint', 'view'), VIEW_ENDPOINTS.items())
def test_endpoint_delegates_to_a_named_view(endpoint: str, view: str) -> None:
    source = inspect.getsource(getattr(api, endpoint))
    called = {
        node.func.id
        for node in ast.walk(ast.parse(source.lstrip()))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert view in called, f'/{endpoint} should fetch via views.{view}'
