import json
from datetime import datetime, timedelta
from pathlib import Path
import re


__all__ = [
    'ARCHIVE_PATH',
    'CONFIG_PATH',
    'OUTPUT_PATH',
    'ensure_dirs',
    'get_weekly_habit_date',
    'load_areas',
    'load_config',
    'save_areas',
    'save_config',
    'set_weekly_habit_date',
]

OUTPUT_PATH = Path.home() / '.local' / 'share' / 'gtd'
ARCHIVE_PATH = OUTPUT_PATH / 'archive'
CONFIG_PATH = OUTPUT_PATH / 'config.json'
HABITS_PATH = OUTPUT_PATH / 'weekly_habits.json'
AREAS_PATH = OUTPUT_PATH / 'areas.json'


def ensure_dirs() -> None:
    """Create storage directories if they don't exist."""
    OUTPUT_PATH.mkdir(parents=True, exist_ok=True)
    ARCHIVE_PATH.mkdir(parents=True, exist_ok=True)


def _safe_filename(name: str) -> str:
    """Sanitize a name for use as a filename."""
    safe = re.sub(r'[/<>:"\\|?*\x00-\x1f]', '-', name)
    safe = re.sub(r'-{2,}', '-', safe)
    return safe.strip('-. ') or 'unnamed'


def load_config() -> dict:
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open() as f:
            return json.load(f)
    return {}


def save_config(config: dict) -> None:
    with CONFIG_PATH.open('w') as f:
        json.dump(config, f, indent=2)


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


LIST_CATEGORIES_PATH = OUTPUT_PATH / 'list_categories.json'


def load_list_categories(defaults: list[str]) -> list[str]:
    """Return persisted list categories, seeded with defaults if missing."""
    if LIST_CATEGORIES_PATH.exists():
        return json.loads(LIST_CATEGORIES_PATH.read_text())
    return list(defaults)


def save_list_categories(categories: list[str]) -> None:
    LIST_CATEGORIES_PATH.write_text(json.dumps(categories, indent=2) + '\n')
