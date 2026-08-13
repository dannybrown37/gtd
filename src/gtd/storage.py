"""Local JSON I/O for weekly review state and habit dates."""

import json
from datetime import datetime, timedelta
from pathlib import Path


__all__ = [
    'OUTPUT_PATH',
    'REVIEW_STEPS',
    'WEEKLY_REVIEW_HABIT',
    'get_weekly_habit_date',
    'habit_done_this_week',
    'set_review_step',
    'set_weekly_habit_date',
]

OUTPUT_PATH = Path.home() / '.local' / 'share' / 'gtd'
HABITS_PATH = OUTPUT_PATH / 'weekly_habits.json'

WEEKLY_REVIEW_HABIT = 'weekly_review'

# The steps of the weekly review, in order — the definition every surface
# reads. It lives here, next to the state that records which of them are
# done, rather than in `gtd_tui.py`: the HTTP API serves the same list to the
# webapp and must not import Textual to get it, and a second copy hand-written
# in `app.js` is exactly the kind of drift `notion/views.py` exists to prevent.
REVIEW_STEPS: list[tuple[str, str]] = [
    ('Process Inbox', 'triage'),
    ('Review Projects', 'projects'),
    ('Review Waiting For', 'waiting'),
    ('Review Someday/Maybe', 'someday'),
    ('Review Horizons of Focus', 'areas'),
    ('Review Calendar (Past & Upcoming)', 'manual'),
    ("Plan Next Week's Priorities", 'manual'),
]


def get_weekly_habit_date(key: str) -> str | None:
    """Return the ISO date this habit was last marked done, or None."""
    if not HABITS_PATH.exists():
        return None
    return json.loads(HABITS_PATH.read_text()).get(key)


def set_weekly_habit_date(key: str) -> None:
    """Mark a habit done today."""
    data: dict = {}
    if HABITS_PATH.exists():
        data = json.loads(HABITS_PATH.read_text())
    data[key] = datetime.now().date().isoformat()
    HABITS_PATH.write_text(json.dumps(data, indent=2) + '\n')


def current_week_start() -> str:
    today = datetime.now().date()
    return (today - timedelta(days=today.weekday())).isoformat()


def habit_done_this_week(key: str) -> bool:
    """True if this habit was last marked done in the current week."""
    last = get_weekly_habit_date(key)
    return bool(last) and last >= current_week_start()


def load_review_state(num_steps: int) -> list[bool]:
    """Return saved step completion list for this week, or all-False."""
    if not HABITS_PATH.exists():
        return [False] * num_steps
    data = json.loads(HABITS_PATH.read_text())
    state = data.get('review_state', {})
    if state.get('week_start') != current_week_start():
        return [False] * num_steps
    saved = state.get('steps_done', [])
    if len(saved) != num_steps:
        return [False] * num_steps
    return list(saved)


def save_review_state(steps_done: list[bool]) -> None:
    """Persist step completion for this week."""
    data: dict = {}
    if HABITS_PATH.exists():
        data = json.loads(HABITS_PATH.read_text())
    data['review_state'] = {
        'week_start': current_week_start(),
        'steps_done': steps_done,
    }
    HABITS_PATH.write_text(json.dumps(data, indent=2) + '\n')


def set_review_step(index: int, *, done: bool) -> list[bool]:
    """Check or uncheck one step, and return the whole week's state.

    Read-modify-write against the same file the TUI uses, so progress made on
    the phone shows up in the terminal and vice versa.
    """
    steps = load_review_state(len(REVIEW_STEPS))
    if not 0 <= index < len(steps):
        msg = f'step index {index} out of range 0..{len(steps) - 1}'
        raise IndexError(msg)
    steps[index] = done
    save_review_state(steps)
    return steps


def reset_review_state() -> None:
    """Clear the saved weekly review state and completion marker."""
    if not HABITS_PATH.exists():
        return
    data = json.loads(HABITS_PATH.read_text())
    data.pop('review_state', None)
    data.pop('weekly_review', None)
    HABITS_PATH.write_text(json.dumps(data, indent=2) + '\n')
