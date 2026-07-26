"""Log, reschedule, and recurring-item utilities."""

import os
import subprocess
import tempfile


__all__ = ['log_and_reschedule']
from datetime import datetime, timedelta
from pathlib import Path

from gtd.notion.client import (
    build_property_update,
    get_page_body,
    replace_page_body,
    update_page,
)
from gtd.notion.entries import (
    _get_today_entries,
    _parse_date_input,
    select_entry,
)
from gtd.notion.models import ProjectEntry
from gtd.ui import prompt_input


_CADENCE_DAYS = {
    'daily': 1,
    'weekly': 7,
    '2x/week': 3,
    '3x/week': 2,
}


def _infer_reschedule_days(header: str) -> int | None:
    """Infer reschedule interval from header prefix like 'Daily:' etc."""
    lowered = header.lower().strip()
    for cadence, days in _CADENCE_DAYS.items():
        if lowered.startswith(f'{cadence}:'):
            return days
    return None


def _infer_cadence(header: str) -> str:
    """Infer cadence string from header prefix, default 'weekly'."""
    lowered = header.lower().strip()
    for cadence in _CADENCE_DAYS:
        if lowered.startswith(f'{cadence}:'):
            return cadence
    return 'weekly'


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


def _log_and_reschedule_entry(entry: ProjectEntry) -> None:
    """Log a note and reschedule a specific entry."""
    body = get_page_body(entry.page_id)
    editor = os.environ.get('EDITOR', 'nvim')
    fd, tmp_path = tempfile.mkstemp(suffix='.md')
    with open(fd, 'w') as f:  # noqa: PTH123
        f.write(body)
    subprocess.run(  # noqa: S603
        [editor, tmp_path],
        check=False,
    )
    new_body = Path(tmp_path).read_text()
    Path(tmp_path).unlink(missing_ok=True)
    if new_body != body:
        replace_page_body(entry.page_id, new_body)
        print(f'  ✓ Notes updated for "{entry.header.strip()}"')

    inferred_days = _infer_reschedule_days(entry.header)
    if inferred_days:
        next_date = (datetime.now() + timedelta(days=inferred_days)).strftime(
            '%Y-%m-%d'
        )
        suffix = 's' if inferred_days != 1 else ''
        print(
            f'  Rescheduling in {inferred_days} day{suffix} ({next_date})',
        )
    else:
        date_input = prompt_input(
            'Reschedule to (e.g. tomorrow, Monday, Jul 15): ',
        )
        if not date_input:
            return
        next_date = _parse_date_input(date_input)
        if not next_date:
            return

    props = build_property_update(follow_up_date=next_date)
    update_page(entry.page_id, props)
    print(f'  ✓ "{entry.header.strip()}" → {next_date}')


def log_and_reschedule() -> None:
    """Log a note and reschedule recurring items."""
    actionable = _get_today_entries()

    if not actionable:
        print('Nothing actionable today.')
        return

    entry = select_entry(actionable, prompt='Log & Reschedule')
    if not entry:
        return

    _log_and_reschedule_entry(entry)
