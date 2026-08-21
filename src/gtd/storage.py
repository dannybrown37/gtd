"""Storage for weekly review state and habit dates.

The weekly review has exactly one source of truth. When an API server is
configured (`GTD_API_URL` + `GTD_API_KEY`, or `api_url`/`api_key` in the
config file) the review's state lives on that server — the same state the
webapp reads and writes — and every surface here proxies to it. Without a
configured server, or when it can't be reached, state falls back to the local
JSON file. Routing the decision here rather than at each call site is what
keeps the TUI and the webapp from drifting apart.
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path


__all__ = [
    'OUTPUT_PATH',
    'REVIEW_STEPS',
    'WEEKLY_REVIEW_HABIT',
    'get_weekly_habit_date',
    'habit_done_this_week',
    'local_load_review_state',
    'local_reset_review_state',
    'local_save_review_state',
    'local_set_review_step',
    'set_review_step',
    'set_weekly_habit_date',
]

OUTPUT_PATH = Path.home() / '.local' / 'share' / 'gtd'
HABITS_PATH = OUTPUT_PATH / 'weekly_habits.json'

WEEKLY_REVIEW_HABIT = 'weekly_review'

REMOTE_TIMEOUT = 5.0


def _config_value(key: str) -> str | None:
    from gtd.notion.config import get_config_value

    try:
        return get_config_value(key)
    except (OSError, ValueError):
        return None


def _remote_base() -> tuple[str, str] | None:
    """The configured API base URL and key, or None to stay local."""
    url = os.environ.get('GTD_API_URL') or _config_value('api_url')
    key = os.environ.get('GTD_API_KEY') or _config_value('api_key')
    if not url or not key:
        return None
    return url.rstrip('/'), key


def _remote_request(
    method: str, path: str, json_body: dict | None = None
) -> dict:
    """Call the GTD API server. Raises OSError-family errors on failure."""
    import httpx

    base = _remote_base()
    if base is None:
        msg = 'no API server configured'
        raise OSError(msg)
    url, key = base
    response = httpx.request(
        method,
        f'{url}{path}',
        headers={'Authorization': f'Bearer {key}'},
        json=json_body,
        timeout=REMOTE_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def _remote_review(
    method: str = 'GET', path: str = '/review', json_body: dict | None = None
) -> dict | None:
    """Review payload from the server, or None if it isn't usable."""
    if _remote_base() is None:
        return None
    try:
        payload = _remote_request(method, path, json_body)
    except Exception:
        return None
    if not isinstance(payload, dict) or 'steps' not in payload:
        return None
    return payload


def _steps_done(payload: dict) -> list[bool]:
    return [bool(step.get('done')) for step in payload['steps']]


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


def _write_habits(data: dict) -> None:
    """Persist the habits file, creating its directory if it isn't there.

    Nothing else creates `~/.local/share/gtd` — the config dir has its own
    `mkdir`, this one had none. Every reader here falls back to a default when
    the file is missing, so a host that has never run the TUI (an API-only
    box) serves the whole weekly review read-only and then fails on the first
    tick. Doing it here rather than at each call site keeps the next writer
    from reintroducing it.
    """
    OUTPUT_PATH.mkdir(parents=True, exist_ok=True)
    HABITS_PATH.write_text(json.dumps(data, indent=2) + '\n')


def local_get_weekly_habit_date(key: str) -> str | None:
    """Read the habit's last-done date from the local file."""
    if not HABITS_PATH.exists():
        return None
    return json.loads(HABITS_PATH.read_text()).get(key)


def local_set_weekly_habit_date(key: str) -> None:
    """Mark a habit done today in the local file."""
    data: dict = {}
    if HABITS_PATH.exists():
        data = json.loads(HABITS_PATH.read_text())
    data[key] = datetime.now().date().isoformat()
    _write_habits(data)


def local_load_review_state(num_steps: int) -> list[bool]:
    """Read this week's step completion from the local file."""
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


def local_save_review_state(steps_done: list[bool]) -> None:
    """Write this week's step completion to the local file."""
    data: dict = {}
    if HABITS_PATH.exists():
        data = json.loads(HABITS_PATH.read_text())
    data['review_state'] = {
        'week_start': current_week_start(),
        'steps_done': steps_done,
    }
    _write_habits(data)


def local_set_review_step(index: int, *, done: bool) -> list[bool]:
    """Check or uncheck one step in the local file."""
    steps = local_load_review_state(len(REVIEW_STEPS))
    if not 0 <= index < len(steps):
        msg = f'step index {index} out of range 0..{len(steps) - 1}'
        raise IndexError(msg)
    steps[index] = done
    local_save_review_state(steps)
    return steps


def local_reset_review_state() -> None:
    """Clear this week's review state and marker in the local file."""
    if not HABITS_PATH.exists():
        return
    data = json.loads(HABITS_PATH.read_text())
    data.pop('review_state', None)
    data.pop('weekly_review', None)
    _write_habits(data)


def current_week_start() -> str:
    today = datetime.now().date()
    return (today - timedelta(days=today.weekday())).isoformat()


def get_weekly_habit_date(key: str) -> str | None:
    """Return the ISO date this habit was last marked done, or None."""
    if key == WEEKLY_REVIEW_HABIT:
        payload = _remote_review()
        if payload is not None:
            return payload.get('last_done')
    return local_get_weekly_habit_date(key)


def set_weekly_habit_date(key: str) -> None:
    """Mark a habit done today."""
    if key == WEEKLY_REVIEW_HABIT and (
        _remote_review('POST', '/review/complete') is not None
    ):
        return
    local_set_weekly_habit_date(key)


def habit_done_this_week(key: str) -> bool:
    """True if this habit was last marked done in the current week."""
    last = get_weekly_habit_date(key)
    if not last:
        return False
    return last >= current_week_start()


def load_review_state(num_steps: int) -> list[bool]:
    """Return saved step completion list for this week, or all-False."""
    payload = _remote_review()
    if payload is not None:
        remote = _steps_done(payload)
        if len(remote) == num_steps:
            return remote
    return local_load_review_state(num_steps)


def save_review_state(steps_done: list[bool]) -> None:
    """Persist step completion for this week.

    The server has no whole-list endpoint, so a remote save sends only the
    steps whose value actually changed.
    """
    payload = _remote_review()
    if payload is not None:
        current = _steps_done(payload)
        if len(current) == len(steps_done):
            for i, want in enumerate(steps_done):
                if current[i] != want:
                    _remote_review('POST', f'/review/step/{i}', {'done': want})
            return
    local_save_review_state(steps_done)


def set_review_step(index: int, *, done: bool) -> list[bool]:
    """Check or uncheck one step, and return the whole week's state.

    Progress made on the phone shows up in the terminal and vice versa —
    either through the shared API server, or, unconfigured, the shared file.
    """
    if not 0 <= index < len(REVIEW_STEPS):
        msg = f'step index {index} out of range 0..{len(REVIEW_STEPS) - 1}'
        raise IndexError(msg)
    payload = _remote_review('POST', f'/review/step/{index}', {'done': done})
    if payload is not None:
        return _steps_done(payload)
    return local_set_review_step(index, done=done)


def reset_review_state() -> None:
    """Clear the saved weekly review state and completion marker."""
    if _remote_review('POST', '/review/reset') is not None:
        return
    local_reset_review_state()
