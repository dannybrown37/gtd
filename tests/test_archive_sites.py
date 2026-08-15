"""Tests for the four CLI paths that archive a page behind `_confirm_delete`.

`test_confirm_delete.py` proves the guard itself answers correctly.
This file proves each *caller* honours the answer — a separate failure,
and the one a refactor is likeliest to introduce, since dropping an
`if` is easier than breaking a boolean.

The guard is deliberately **not** mocked. Each test drives the real
`_confirm_delete` by feeding `log.prompt_input`, so a site that stopped
calling the guard, or started calling a mock-shaped stand-in, still fails
here. Every test asserts on `archive_page` — refusal means it was never
called, acceptance means it was called once with the page id.
"""

import pytest

from gtd.notion import commands, log, review, today
from gtd.notion.models import ProjectEntry
from gtd.notion.review import _review_get_current


def _entry(
    header: str = 'Ship the thing', status: str = 'Current Project'
) -> ProjectEntry:
    return ProjectEntry(
        page_id='page-1',
        header=header,
        status=status,
        context='@Computer',
        next_step='Do it',
        success_condition='Done',
        due_date=None,
        follow_up_date=None,
        created_date='2026-08-01',
    )


@pytest.fixture
def answer(monkeypatch: pytest.MonkeyPatch):
    """Feed one canned reply to the real guard's prompt."""

    def _install(reply: str | None) -> None:
        monkeypatch.setattr(log, 'prompt_input', lambda _label: reply)

    return _install


@pytest.fixture
def archived(monkeypatch: pytest.MonkeyPatch):
    """Record `archive_page` calls in whichever module is under test."""

    def _install(module: object) -> list[str]:
        calls: list[str] = []
        monkeypatch.setattr(module, 'archive_page', calls.append)
        return calls

    return _install


REFUSALS = ['n', 'N', '', None, 'yes', 'no', 'YES']
ACCEPTANCES = ['y', 'Y']


# --- gtd done → commands.mark_done ---


@pytest.fixture
def _mark_done_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = _entry()
    monkeypatch.setattr(
        commands, 'entries_for_status', lambda *_a, **_kw: [entry]
    )
    monkeypatch.setattr(commands, 'select_entry', lambda *_a, **_kw: entry)


@pytest.mark.parametrize('reply', REFUSALS)
def test_mark_done_refusal_archives_nothing(
    _mark_done_flow, answer, archived, reply: str | None
) -> None:
    answer(reply)
    calls = archived(commands)
    commands.mark_done()
    assert calls == []


@pytest.mark.parametrize('reply', ACCEPTANCES)
def test_mark_done_acceptance_archives(
    _mark_done_flow, answer, archived, reply: str
) -> None:
    answer(reply)
    calls = archived(commands)
    commands.mark_done()
    assert calls == ['page-1']


def test_mark_done_cancelling_the_picker_archives_nothing(
    monkeypatch, answer, archived
) -> None:
    monkeypatch.setattr(
        commands, 'entries_for_status', lambda *_a, **_kw: [_entry()]
    )
    monkeypatch.setattr(commands, 'select_entry', lambda *_a, **_kw: None)
    answer('y')
    calls = archived(commands)
    commands.mark_done()
    assert calls == []


# --- gtd review, Someday phase → review.review_someday ("Drop") ---


@pytest.fixture
def _someday_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        review,
        'entries_for_status',
        lambda *_a, **_kw: [_entry(status='Someday/Maybe')],
    )
    monkeypatch.setattr(review, 'get_page_body', lambda _pid: '')
    monkeypatch.setattr(
        review, 'fzf_on_a_list', lambda *_a, **_kw: 'Drop (archive)'
    )


@pytest.mark.parametrize('reply', REFUSALS)
def test_someday_drop_refusal_archives_nothing(
    _someday_flow, answer, archived, reply: str | None
) -> None:
    answer(reply)
    calls = archived(review)
    review.review_someday()
    assert calls == []


@pytest.mark.parametrize('reply', ACCEPTANCES)
def test_someday_drop_acceptance_archives(
    _someday_flow, answer, archived, reply: str
) -> None:
    answer(reply)
    calls = archived(review)
    review.review_someday()
    assert calls == ['page-1']


# --- gtd review, phase 2 → review._review_get_current ("Mark done") ---


@pytest.fixture
def _get_current_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    def _entries(
        status: str, *_a: object, **_kw: object
    ) -> list[ProjectEntry]:
        return [_entry()] if status == 'Current Project' else []

    monkeypatch.setattr(review, 'entries_for_status', _entries)
    monkeypatch.setattr(review, 'get_page_body', lambda _pid: '')
    monkeypatch.setattr(
        review, 'fzf_on_a_list', lambda *_a, **_kw: 'Mark done'
    )


@pytest.mark.parametrize('reply', REFUSALS)
def test_get_current_refusal_archives_nothing(
    _get_current_flow, answer, archived, reply: str | None
) -> None:
    answer(reply)
    calls = archived(review)
    _review_get_current()
    assert calls == []


@pytest.mark.parametrize('reply', ACCEPTANCES)
def test_get_current_acceptance_archives(
    _get_current_flow, answer, archived, reply: str
) -> None:
    answer(reply)
    calls = archived(review)
    _review_get_current()
    assert calls == ['page-1']


# --- gtd today → today.list_today ("Mark done (moves to trash)") ---


@pytest.fixture
def _today_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    """One pick, then the picker returns None so the while loop terminates.

    A refusal leaves the entry in `actionable`, so a picker that always
    returned the entry would spin forever rather than fail.
    """
    entry = _entry()
    picks = iter([entry, None])
    monkeypatch.setattr(
        today, 'next_steps_entries', lambda *_a, **_kw: [entry]
    )
    monkeypatch.setattr(
        today, 'select_entry', lambda *_a, **_kw: next(picks, None)
    )
    monkeypatch.setattr(today, 'get_page_body', lambda _pid: '')
    monkeypatch.setattr(
        today,
        'fzf_on_a_list',
        lambda *_a, **_kw: 'Mark done (moves to trash)',
    )


@pytest.mark.parametrize('reply', REFUSALS)
def test_today_refusal_archives_nothing(
    _today_flow, answer, archived, reply: str | None
) -> None:
    answer(reply)
    calls = archived(today)
    today.list_today()
    assert calls == []


@pytest.mark.parametrize('reply', ACCEPTANCES)
def test_today_acceptance_archives(
    _today_flow, answer, archived, reply: str
) -> None:
    answer(reply)
    calls = archived(today)
    today.list_today()
    assert calls == ['page-1']


# --- the recurring escalation reaches the call sites too ---


@pytest.mark.parametrize('reply', ['y', 'Y', 'yes', 'Yes', 'n', ''])
def test_recurring_entry_needs_literal_yes_at_the_call_site(
    monkeypatch, answer, archived, reply: str
) -> None:
    """`y` is not enough for a Recurring item — the guard demands YES."""
    entry = _entry(header='Weekly: water the plants', status='Recurring')
    monkeypatch.setattr(
        commands, 'entries_for_status', lambda *_a, **_kw: [entry]
    )
    monkeypatch.setattr(commands, 'select_entry', lambda *_a, **_kw: entry)
    answer(reply)
    calls = archived(commands)
    commands.mark_done()
    assert calls == []


def test_recurring_entry_archives_on_literal_yes(
    monkeypatch, answer, archived
) -> None:
    entry = _entry(header='Weekly: water the plants', status='Recurring')
    monkeypatch.setattr(
        commands, 'entries_for_status', lambda *_a, **_kw: [entry]
    )
    monkeypatch.setattr(commands, 'select_entry', lambda *_a, **_kw: entry)
    answer('YES')
    calls = archived(commands)
    commands.mark_done()
    assert calls == ['page-1']
