"""Guards on context filtering in the webapp's status-backed views.

The TUI binds `F` (`action_filter_context`) on `BaseEntryContent`, so every
entry tab can narrow to one context — Projects most of all, since it is the
longest list. The webapp only ever offered it on Next Steps. These are text
assertions over the shipped JS; the webapp has no JS test harness.
"""

from __future__ import annotations

from pathlib import Path

import pytest

WEBAPP = Path(__file__).parent.parent / 'src' / 'gtd' / 'webapp'


@pytest.fixture(scope='module')
def app_js() -> str:
    return (WEBAPP / 'app.js').read_text()


@pytest.fixture(scope='module')
def entries_loader(app_js: str) -> str:
    body = app_js.split('async function loadEntries(view)')[1]
    return body.split('\nasync function ')[0]


def test_generic_entries_loader_renders_context_chips(
    entries_loader: str,
) -> None:
    assert 'renderChips(' in entries_loader
    assert 'state.currentContext' in entries_loader


def test_chips_come_from_the_entries_in_view(entries_loader: str) -> None:
    """The TUI's `F` lists only contexts present in the current view."""
    assert 'contextsInView(' in entries_loader


def test_context_filter_is_client_side(entries_loader: str) -> None:
    """Picking a chip must not re-fetch — the entries are already loaded."""
    assert 'context=' not in entries_loader


def test_filtering_keeps_the_unfiltered_entries_in_state(app_js: str) -> None:
    """The stored entries stay unfiltered.

    `removeEntryRow` prunes `state.entries`; a narrowed state would drop the
    rows the user returns to when clearing the chip.
    """
    assert 'function renderEntriesForContext(' in app_js


def test_no_context_bucket_exists(app_js: str) -> None:
    assert 'NO_CONTEXT' in app_js
