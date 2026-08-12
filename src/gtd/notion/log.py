"""Reschedule and recurring-item utilities."""

__all__ = ['reschedule_only']

from datetime import datetime, timedelta

from gtd.notion.client import (
    build_property_update,
    update_page,
)
from gtd.notion.entries import _parse_date_input
from gtd.notion.models import ProjectEntry
from gtd.ui import prompt_input


_CADENCE_DAYS = {
    'daily': 1,
    'weekly': 7,
    '2x/week': 3,
    '3x/week': 2,
}

_AFFIRMATIVE = {'', 'y', 'yes'}


def _infer_reschedule_days(header: str) -> int | None:
    """Infer reschedule interval from header prefix like 'Daily:' etc."""
    lowered = header.lower().strip()
    for cadence, days in _CADENCE_DAYS.items():
        if lowered.startswith(f'{cadence}:'):
            return days
    return None


def _is_recurring(entry: ProjectEntry) -> bool:
    """Check if an entry is a recurring item."""
    return (
        entry.status == 'Recurring'
        or _infer_reschedule_days(entry.header) is not None
    )


def _confirm_delete(entry: ProjectEntry) -> bool:
    """Prompt for delete confirmation. Stricter for recurring items."""
    name = entry.header.strip()
    if _is_recurring(entry):
        print(f'  ⚠ "{name}" is a recurring item!')
        confirm = prompt_input(
            '  Type YES to permanently delete: ',
        )
        return confirm == 'YES'
    confirm = prompt_input(f'  Delete "{name}"? (y/N): ')
    return bool(confirm and confirm.lower() == 'y')


def reschedule_only(entry: ProjectEntry) -> str | None:
    """Set a recurring entry's next follow-up date, and nothing else.

    The CLI counterpart of the TUI's `_shared_reschedule_only`. Rescheduling
    is one decision, so this asks one question: when the header carries a
    cadence ('Daily:', 'Weekly:', ...) the date is inferred and only
    confirmed, and declining it falls through to the manual prompt rather
    than cancelling.

    Returns the new follow-up date string, or None if cancelled.
    """
    name = entry.header.strip()
    next_date = None

    inferred = _infer_reschedule_days(entry.header)
    if inferred:
        candidate = (datetime.now() + timedelta(days=inferred)).strftime(
            '%Y-%m-%d'
        )
        answer = prompt_input(f'  Reschedule "{name}" to {candidate}? (Y/n): ')
        if answer is not None and answer.strip().lower() in _AFFIRMATIVE:
            next_date = candidate

    if next_date is None:
        date_input = prompt_input(
            '  Reschedule to (e.g. tomorrow, Monday, Jul 15): ',
        )
        if not date_input:
            return None
        next_date = _parse_date_input(date_input)
        if not next_date:
            print('  Could not parse that date.')
            return None

    update_page(entry.page_id, build_property_update(follow_up_date=next_date))
    print(f'  ✓ "{name}" → {next_date}')
    return next_date
