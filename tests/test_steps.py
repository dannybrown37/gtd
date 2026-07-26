"""Tests for the step queue helpers in ProjectEntry."""

import pytest

from gtd.notion.models import (
    ProjectEntry,
    advance_steps,
    format_steps,
    parse_steps,
)


# ── parse_steps ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ('text', 'expected'),
    [
        ('', []),
        ('Call the vendor', ['Call the vendor']),
        ('1. First step', ['First step']),
        (
            '1. First step\n2. Second step\n3. Third step',
            ['First step', 'Second step', 'Third step'],
        ),
        ('1) First\n2) Second', ['First', 'Second']),
        ('  First\n  Second  ', ['First', 'Second']),
        ('1. Step\n\n2. Another\n', ['Step', 'Another']),
    ],
)
def test_parse_steps(text: str, expected: list[str]) -> None:
    assert parse_steps(text) == expected


# ── format_steps ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ('steps', 'expected'),
    [
        ([], ''),
        (['Only step'], '1. Only step'),
        (['First', 'Second', 'Third'], '1. First\n2. Second\n3. Third'),
    ],
)
def test_format_steps(steps: list[str], expected: str) -> None:
    assert format_steps(steps) == expected


# ── advance_steps ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ('text', 'expected'),
    [
        ('', ''),
        ('1. Only step', ''),
        ('Single plain step', ''),
        ('1. First\n2. Second\n3. Third', '1. Second\n2. Third'),
        ('1. First\n2. Second', '1. Second'),
    ],
)
def test_advance_steps(text: str, expected: str) -> None:
    assert advance_steps(text) == expected


# ── ProjectEntry properties ──────────────────────────────────────────────────


def _make_entry(next_step: str) -> ProjectEntry:
    return ProjectEntry(
        page_id='test-id',
        header='Test Project',
        status='Current Project',
        context='Work',
        next_step=next_step,
        success_condition='Done',
        due_date=None,
        follow_up_date=None,
        created_date='2026-01-01T00:00:00Z',
        updated_date='',
    )


def test_steps_single() -> None:
    entry = _make_entry('Call the vendor')
    assert entry.steps == ['Call the vendor']
    assert entry.current_step == 'Call the vendor'
    assert entry.step_count == 1


def test_steps_queue() -> None:
    entry = _make_entry('1. Call vendor\n2. Send contract\n3. Follow up')
    assert entry.steps == ['Call vendor', 'Send contract', 'Follow up']
    assert entry.current_step == 'Call vendor'
    assert entry.step_count == 3


def test_steps_empty() -> None:
    entry = _make_entry('')
    assert entry.steps == []
    assert entry.current_step == ''
    assert entry.step_count == 0


def test_advance_mutates_next_step() -> None:
    entry = _make_entry('1. First\n2. Second\n3. Third')
    entry.next_step = advance_steps(entry.next_step)
    assert entry.current_step == 'Second'
    assert entry.step_count == 2


def test_advance_to_empty() -> None:
    entry = _make_entry('1. Only step')
    entry.next_step = advance_steps(entry.next_step)
    assert entry.next_step == ''
    assert entry.step_count == 0
