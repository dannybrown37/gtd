"""Tests for the Delete branch of the CLI triage flow.

`notion/triage.py` does **not** route through `_confirm_delete` — it has
its own inline `(y/N)` prompt, so nothing in `test_confirm_delete.py`
covers it. It is also the *only* archive site reachable straight from an
inbox item, which makes a mis-typed answer here the cheapest possible way
to lose a page.

The assertions are about what the prompt refuses. Note in particular that
the guard compares against `'y'` exactly, so a full `'yes'` is a refusal —
pinned below so a later "helpful" loosening is a deliberate change.
"""

import pytest

from gtd.notion import triage
from gtd.notion.models import ProjectEntry
from gtd.notion.triage import _process_single_entry


def _entry(header: str = 'Buy a kayak') -> ProjectEntry:
    return ProjectEntry(
        page_id='page-1',
        header=header,
        status='Triage',
        context='',
        next_step='',
        success_condition='',
        due_date=None,
        follow_up_date=None,
        created_date='2026-08-01',
    )


@pytest.fixture
def delete_flow(monkeypatch: pytest.MonkeyPatch):
    """Drive triage to the Delete branch and record the archive calls."""

    def _install(reply: str | None) -> list[str]:
        archived: list[str] = []

        monkeypatch.setattr(triage, 'get_page_body', lambda _pid: '')
        monkeypatch.setattr(
            triage, 'fzf_on_a_list', lambda *_a, **_kw: 'Delete'
        )
        monkeypatch.setattr(triage, 'prompt_input', lambda _label: reply)
        monkeypatch.setattr(triage, 'archive_page', archived.append)
        return archived

    return _install


@pytest.mark.parametrize('reply', ['y', 'Y'])
def test_deletes_on_yes(delete_flow, reply: str) -> None:
    archived = delete_flow(reply)
    assert _process_single_entry(_entry()) is True
    assert archived == ['page-1']


@pytest.mark.parametrize(
    'reply',
    ['n', 'N', '', None, 'yes', 'YES', 'no', ' y', 'y ', 'delete', '1'],
)
def test_refuses_on_anything_else(delete_flow, reply: str | None) -> None:
    archived = delete_flow(reply)
    assert _process_single_entry(_entry()) is False
    assert archived == []


def test_prompt_names_the_entry(delete_flow, monkeypatch) -> None:
    labels: list[str] = []
    delete_flow('n')
    monkeypatch.setattr(
        triage,
        'prompt_input',
        lambda label: labels.append(label) or 'n',
    )
    _process_single_entry(_entry('  Cancel the gym membership  '))
    assert 'Cancel the gym membership' in labels[0]
    assert '(y/N)' in labels[0]


def test_cancelling_the_status_picker_archives_nothing(
    delete_flow, monkeypatch
) -> None:
    archived = delete_flow('y')
    monkeypatch.setattr(triage, 'fzf_on_a_list', lambda *_a, **_kw: None)
    assert _process_single_entry(_entry()) is False
    assert archived == []


def test_agenda_items_never_reach_the_delete_branch(
    delete_flow, monkeypatch
) -> None:
    """An `@Person` header skips the Status picker entirely."""
    from gtd.notion import client

    archived = delete_flow('y')
    monkeypatch.setattr(triage, 'update_page', lambda *_a, **_kw: None)
    monkeypatch.setattr(client, 'add_context', lambda _name: None)
    monkeypatch.setattr(triage, 'prompt_input', lambda _label: None)
    _process_single_entry(_entry('@Sam: raise the budget'))
    assert archived == []
