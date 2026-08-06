"""Keeps the webapp's feature set in step with the TUI's.

The TUI and the webapp are two front ends over the same Notion data and are
meant to stay feature-equivalent — a capability added to one belongs in the
other. Nothing enforced that, so the webapp drifted to roughly a third of the
TUI's actions before anyone noticed.

The check is deliberately asymmetric, because a symmetric one is worthless:

* The **TUI side is derived** — it walks the real `BINDINGS` on the content
  widgets, so adding a binding is picked up with no bookkeeping.
* The **webapp side is declared** — a `CAPABILITIES` array in `app.js`.

So the failure mode that actually happens (add a TUI key, forget the webapp)
is caught automatically. Two hand-maintained lists compared against each other
would drift together and always pass.

When you add a TUI binding, do one of:
  1. implement it in the webapp and add its action name to `CAPABILITIES`, or
  2. add it to `TUI_ONLY` below **with a reason** — it genuinely can't cross.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from gtd import gtd_tui

APP_JS = Path(__file__).parent.parent / 'src' / 'gtd' / 'webapp' / 'app.js'

# Widgets whose bindings are user-facing actions: the app itself (global keys
# like capture/refresh) plus the tab content widgets (per-entry actions). The
# modal browse screens used during the Weekly Review are excluded — that whole
# flow is tracked as one capability (see TUI_ONLY).
TUI_WIDGETS = [
    gtd_tui.GTDApp,
    gtd_tui.BaseEntryContent,
    gtd_tui.NextStepsContent,
    gtd_tui.InboxContent,
    gtd_tui.ProjectsContent,
    gtd_tui.RecurringContent,
    gtd_tui.WaitingForContent,
    gtd_tui.SomedayContent,
    gtd_tui.SnoozedContent,
    gtd_tui.ListsContent,
]

# Actions with no webapp counterpart, each with the reason it can't have one.
# Adding an entry here is a deliberate decision, not a way to silence the test.
TUI_ONLY = {
    # Terminal-only navigation; the webapp navigates by tapping.
    'cursor_down': 'keyboard navigation',
    'cursor_up': 'keyboard navigation',
    'focus_list': 'keyboard navigation',
    'focus_list_up': 'keyboard navigation',
    'focus_list_bottom': 'keyboard navigation',
    'tab_left': 'keyboard navigation',
    'tab_right': 'keyboard navigation',
    'command_palette': 'keyboard navigation',
    'quit': 'no equivalent in a browser tab',
    'dismiss_cel': 'celebration animation is TUI chrome',
    # Deferred by scope: the Weekly Review and the select-option CRUD are a
    # follow-up pass, not part of the core-actions parity work.
    'complete_habit': 'Weekly Review not yet ported',
    'complete_step': 'Weekly Review not yet ported',
    'finish_step': 'Weekly Review not yet ported',
    'change_status': 'Weekly Review not yet ported',
    'toggle': 'Weekly Review not yet ported',
    'reset': 'Weekly Review not yet ported',
    'someday': 'Weekly Review browse screen; webapp uses move_someday',
    'drop': 'Weekly Review browse screen; webapp uses drop_entry',
    'set_area': 'Area CRUD not yet ported',
    'add_area': 'Area CRUD not yet ported',
    'remove_area': 'Area CRUD not yet ported',
    'rename_area': 'Area CRUD not yet ported',
    'add_category': 'List category CRUD not yet ported',
    'remove_category': 'List category CRUD not yet ported',
    'rename_category': 'List category CRUD not yet ported',
    'move_item': 'List category CRUD not yet ported',
    'add_item': 'List category CRUD not yet ported',
    'update_item': 'List category CRUD not yet ported',
    'save': 'modal-internal binding',
    'cancel': 'modal-internal binding',
}


def declared_capabilities() -> set[str]:
    """Parse the `CAPABILITIES` array out of app.js."""
    source = APP_JS.read_text()
    match = re.search(r'const CAPABILITIES = \[(.*?)\];', source, re.DOTALL)
    assert match, 'app.js must declare a CAPABILITIES array'
    return set(re.findall(r"'([^']+)'", match.group(1)))


def tui_actions() -> set[str]:
    """Collect action names from the content widgets' real BINDINGS."""
    actions: set[str] = set()
    for widget in TUI_WIDGETS:
        for binding in widget.__dict__.get('BINDINGS', []):
            action = getattr(binding, 'action', None)
            if action:
                actions.add(action)
    return actions


def test_tui_bindings_are_discoverable() -> None:
    """Guard the guard: a rename emptying this set must not pass quietly."""
    found = tui_actions()
    assert len(found) > 10, f'only found {found} — is BINDINGS still a list?'
    assert 'mark_done' in found


def test_every_tui_action_has_a_webapp_counterpart() -> None:
    missing = tui_actions() - declared_capabilities() - set(TUI_ONLY)
    assert not missing, (
        f'TUI actions with no webapp capability and no TUI_ONLY reason: '
        f'{sorted(missing)}. Either implement them in app.js and add them to '
        f'CAPABILITIES, or add them to TUI_ONLY with a reason.'
    )


def test_declared_capabilities_are_real_tui_actions() -> None:
    """Catches typos and capabilities left behind by a TUI rename."""
    known = tui_actions() | set(TUI_ONLY)
    unknown = declared_capabilities() - known
    assert not unknown, (
        f'CAPABILITIES names no TUI action: {sorted(unknown)}. '
        f'Was the TUI action renamed or removed?'
    )


def test_tui_only_entries_carry_a_reason() -> None:
    for action, reason in TUI_ONLY.items():
        assert reason.strip(), f'{action} needs a reason, not an empty string'


@pytest.mark.parametrize(
    'capability',
    ['update_entry', 'edit_notes', 'wait_tomorrow', 'mark_done'],
)
def test_core_actions_are_declared(capability: str) -> None:
    """The actions the parity work existed to deliver, pinned explicitly."""
    assert capability in declared_capabilities()
