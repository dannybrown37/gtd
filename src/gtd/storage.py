import json
from datetime import datetime, timedelta
from pathlib import Path


__all__ = [
    'OUTPUT_PATH',
    'get_weekly_habit_date',
    'load_areas',
    'save_areas',
    'set_weekly_habit_date',
]

OUTPUT_PATH = Path.home() / '.local' / 'share' / 'gtd'
HABITS_PATH = OUTPUT_PATH / 'weekly_habits.json'
AREAS_PATH = OUTPUT_PATH / 'areas.json'


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


def _current_week_start() -> str:
    today = datetime.now().date()
    return (today - timedelta(days=today.weekday())).isoformat()


def load_review_state(num_steps: int) -> list[bool]:
    """Return saved step completion list for this week, or all-False."""
    if not HABITS_PATH.exists():
        return [False] * num_steps
    data = json.loads(HABITS_PATH.read_text())
    state = data.get('review_state', {})
    if state.get('week_start') != _current_week_start():
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
        'week_start': _current_week_start(),
        'steps_done': steps_done,
    }
    HABITS_PATH.write_text(json.dumps(data, indent=2) + '\n')


def reset_review_state() -> None:
    """Clear the saved weekly review state and completion marker."""
    if not HABITS_PATH.exists():
        return
    data = json.loads(HABITS_PATH.read_text())
    data.pop('review_state', None)
    data.pop('weekly_review', None)
    HABITS_PATH.write_text(json.dumps(data, indent=2) + '\n')


def load_areas() -> list[dict]:
    """Return list of area dicts: {name: str, notes: str}."""
    if not AREAS_PATH.exists():
        return []
    return json.loads(AREAS_PATH.read_text())


def save_areas(areas: list[dict]) -> None:
    AREAS_PATH.write_text(json.dumps(areas, indent=2) + '\n')
