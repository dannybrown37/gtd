"""Tests for the CLI delete confirmation guard.

`_confirm_delete` is the only thing standing between a keystroke and
`archive_page`, and until now nothing tested it.

Four CLI paths call it (`commands.mark_done`, both sites in
`notion/review.py`, `notion/today.py`) and all four archive on a `True`.
An inverted boolean here would ship green, so the assertions below are
about what the guard *refuses*, not only what it accepts.
"""

import pytest

from gtd.notion import log
from gtd.notion.log import _confirm_delete, _is_recurring
from gtd.notion.models import ProjectEntry
from gtd.ui import CancelAction


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
    """Feed one canned reply to the prompt and capture the label shown."""

    def _install(reply: str | None) -> list[str]:
        labels: list[str] = []

        def _prompt(label: str) -> str | None:
            labels.append(label)
            return reply

        monkeypatch.setattr(log, 'prompt_input', _prompt)
        return labels

    return _install


# --- ordinary entries: y/N ---


@pytest.mark.parametrize('reply', ['y', 'Y'])
def test_plain_entry_deletes_on_yes(answer, reply: str) -> None:
    answer(reply)
    assert _confirm_delete(_entry()) is True


@pytest.mark.parametrize(
    'reply',
    [
        pytest.param('n', id='no'),
        pytest.param('N', id='no-caps'),
        pytest.param('', id='bare-enter'),
        pytest.param(None, id='no-input'),
        pytest.param('yes', id='yes-spelled-out'),
        pytest.param('YES', id='shouted-yes'),
        pytest.param(' ', id='whitespace'),
    ],
)
def test_plain_entry_refuses_anything_else(answer, reply: str | None) -> None:
    """Only a bare y/Y counts — `_confirm_delete` compares the whole reply."""
    answer(reply)
    assert _confirm_delete(_entry()) is False


def test_plain_entry_prompt_defaults_to_no(answer) -> None:
    labels = answer('n')
    _confirm_delete(_entry())
    assert '(y/N)' in labels[0]


# --- recurring entries: the strict path ---


@pytest.mark.parametrize(
    'entry',
    [
        pytest.param(_entry(status='Recurring'), id='by-status'),
        pytest.param(_entry(header='Daily: stretch'), id='by-daily-header'),
        pytest.param(_entry(header='Weekly: review'), id='by-weekly-header'),
        pytest.param(_entry(header='2x/week: gym'), id='by-2x-header'),
        pytest.param(_entry(header='3x/week: run'), id='by-3x-header'),
    ],
)
def test_recurring_requires_literal_yes(answer, entry: ProjectEntry) -> None:
    answer('YES')
    assert _confirm_delete(entry) is True


@pytest.mark.parametrize(
    'reply',
    [
        pytest.param('y', id='y-is-not-enough'),
        pytest.param('yes', id='lowercase-is-not-enough'),
        pytest.param('Yes', id='titlecase-is-not-enough'),
        pytest.param('', id='bare-enter'),
        pytest.param(None, id='no-input'),
        pytest.param('n', id='no'),
    ],
)
def test_recurring_refuses_a_casual_yes(answer, reply: str | None) -> None:
    """The whole point of the strict path: `y` must not delete a habit."""
    answer(reply)
    assert _confirm_delete(_entry(status='Recurring')) is False


def test_recurring_prompt_demands_yes_in_caps(answer) -> None:
    labels = answer('YES')
    _confirm_delete(_entry(status='Recurring'))
    assert 'YES' in labels[0]


def test_recurring_warns_before_prompting(
    answer, capsys: pytest.CaptureFixture[str]
) -> None:
    answer('YES')
    _confirm_delete(_entry(header='Daily: stretch', status='Recurring'))
    assert '⚠' in capsys.readouterr().out


# --- Ctrl+C must abort, not fall through to a delete ---


def test_cancel_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    def _cancel(_label: str) -> str:
        raise CancelAction

    monkeypatch.setattr(log, 'prompt_input', _cancel)
    with pytest.raises(CancelAction):
        _confirm_delete(_entry())


# --- _is_recurring, which selects between the two paths ---


@pytest.mark.parametrize(
    ('entry', 'expected'),
    [
        pytest.param(_entry(status='Recurring'), True, id='status'),
        pytest.param(_entry(header='daily: stretch'), True, id='lowercase'),
        pytest.param(_entry(header='  Daily: stretch'), True, id='leading-ws'),
        pytest.param(_entry(header='Daily stretch'), False, id='no-colon'),
        pytest.param(
            _entry(header='My daily: stretch'), False, id='not-leading'
        ),
        pytest.param(_entry(), False, id='ordinary'),
    ],
)
def test_is_recurring(entry: ProjectEntry, expected: bool) -> None:
    assert _is_recurring(entry) is expected
