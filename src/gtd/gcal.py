"""Google Calendar, read-only, borrowed from `gfunk`.

GTD owns no Google credentials and builds no Google client. It shells out
to `gfunk grind --json`, which already holds the OAuth token and an opt-in
`calendar.readonly` scope. That keeps the token in one place, keeps GTD
installable on Python 3.12, and means a machine without `gfunk` simply has
no calendar rather than a broken one.

Named `gcal` rather than `calendar` on purpose: the latter shadows a
stdlib module.

The grouping below is a port of `gfunk`'s `grind_days`, not an import —
importing it would drag in gfunk's entire 2000-line CLI module, Textual
and all. The part gfunk does not have, and the part GTD actually wants,
is `free_spans`: where the holes in the day are.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from gtd.notion.config import get_config_value


__all__ = [
    'BAR_BUSY',
    'BAR_FREE',
    'CalDay',
    'CalEvent',
    'CalendarUnavailableError',
    'busy_bar',
    'fetch_events',
    'free_hours',
    'group_days',
    'largest_gap',
    'load_label',
    'merged_events',
    'usable_gaps',
    'working_hours',
]

DEFAULT_DAY_START = time(8, 0)
DEFAULT_DAY_END = time(20, 0)

_LOAD_LIGHT_HOURS = 2.0
_LOAD_HEAVY_HOURS = 5.0

BAR_BUSY = '█'
BAR_FREE = '░'
_BAR_MINUTES = 30

# Below this, a hole between meetings is context-switching, not work time.
USABLE_GAP_MINUTES = 45

_TIMEOUT_SECONDS = 30

_OPT_IN_HINT = 'Calendar not connected — run: gfunk mount-up --with-calendar'
_MISSING_HINT = (
    'Calendar needs gfunk — install it, or set GTD_GFUNK_BIN to its path'
)


class CalendarUnavailableError(Exception):
    """No calendar to show: gfunk missing, signed out, or not opted in.

    Carries a `hint` the caller can render verbatim. Every surface treats
    this as an empty state, never as an error — a machine with no gfunk
    token is the normal case, not the edge case.
    """

    def __init__(self, hint: str) -> None:
        super().__init__(hint)
        self.hint = hint


def gfunk_bin() -> str:
    """Where the gfunk binary lives — env first, then config, then PATH."""
    return (
        os.environ.get('GTD_GFUNK_BIN')
        or get_config_value('gfunk_bin')
        or 'gfunk'
    )


def working_hours() -> tuple[time, time]:
    """The window free time is measured inside."""
    return (
        _parse_time(get_config_value('calendar_day_start'), DEFAULT_DAY_START),
        _parse_time(get_config_value('calendar_day_end'), DEFAULT_DAY_END),
    )


def _parse_time(raw: str | None, fallback: time) -> time:
    if not raw:
        return fallback
    try:
        return time.fromisoformat(raw)
    except ValueError:
        return fallback


@dataclass
class CalEvent:
    """One timed event, already in local time."""

    summary: str
    start: datetime
    end: datetime
    location: str = ''
    # Which calendar it came from — '' for the primary Google one,
    # `ics.SOURCE_LABEL` for a subscribed feed.
    source: str = ''

    @property
    def minutes(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)


@dataclass
class CalDay:
    """One day's worth of calendar, plus the holes in it."""

    date: date
    events: list[CalEvent] = field(default_factory=list)
    all_day: list[str] = field(default_factory=list)
    total_hours: float = 0.0
    conflicts: int = 0
    free_spans: list[tuple[time, time]] = field(default_factory=list)

    @property
    def load(self) -> str:
        return load_label(self.total_hours)


def _to_local(raw: str) -> datetime:
    """Google sends offsets; the day you live in is the local one."""
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone().replace(tzinfo=None)


def _parse_event(event: dict) -> CalEvent | None:
    start_raw = event.get('start') or {}
    if 'dateTime' not in start_raw:
        return None
    try:
        start = _to_local(start_raw['dateTime'])
        end_raw = (event.get('end') or {}).get('dateTime')
        end = _to_local(end_raw) if end_raw else start
    except (TypeError, ValueError):
        return None
    return CalEvent(
        summary=event.get('summary') or '(no title)',
        start=start,
        end=max(start, end),
        location=event.get('location') or '',
        source=event.get('_source') or '',
    )


def _all_day_date(event: dict) -> date | None:
    start_raw = event.get('start') or {}
    if 'dateTime' in start_raw or 'date' not in start_raw:
        return None
    try:
        return date.fromisoformat(start_raw['date'])
    except (TypeError, ValueError):
        return None


def _merge(spans: list[tuple[time, time]]) -> list[tuple[time, time]]:
    """Collapse overlapping busy blocks so gaps can't come out negative."""
    merged: list[list[time]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(s, e) for s, e in merged]


def _free_spans(
    events: list[CalEvent], day_start: time, day_end: time
) -> list[tuple[time, time]]:
    busy = _merge(
        [
            (max(e.start.time(), day_start), min(e.end.time(), day_end))
            for e in events
            if e.start.time() < day_end and e.end.time() > day_start
        ]
    )
    spans: list[tuple[time, time]] = []
    cursor = day_start
    for start, end in busy:
        if start > cursor:
            spans.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < day_end:
        spans.append((cursor, day_end))
    return spans


def group_days(
    events: list[dict],
    *,
    start: date,
    num_days: int,
    day_start: time | None = None,
    day_end: time | None = None,
) -> list[CalDay]:
    """Bucket raw Google events into one `CalDay` per day in the window.

    Days with nothing on them are still returned — an empty Thursday is
    the most useful row in the table.
    """
    if day_start is None or day_end is None:
        configured = working_hours()
        day_start = day_start or configured[0]
        day_end = day_end or configured[1]

    wanted = [start + timedelta(days=i) for i in range(num_days)]
    buckets: dict[date, CalDay] = {d: CalDay(date=d) for d in wanted}
    timed: dict[date, list[CalEvent]] = defaultdict(list)

    for event in events:
        whole_day = _all_day_date(event)
        if whole_day is not None:
            if whole_day in buckets:
                buckets[whole_day].all_day.append(
                    event.get('summary') or '(no title)'
                )
            continue
        parsed = _parse_event(event)
        if parsed is None or parsed.start.date() not in buckets:
            continue
        timed[parsed.start.date()].append(parsed)

    for day, found in timed.items():
        bucket = buckets[day]
        bucket.events = sorted(found, key=lambda e: (e.start, e.summary))
        bucket.total_hours = sum(e.minutes for e in bucket.events) / 60
        bucket.conflicts = sum(
            1
            for a, b in zip(bucket.events, bucket.events[1:], strict=False)
            if a.end > b.start
        )

    for bucket in buckets.values():
        bucket.free_spans = _free_spans(bucket.events, day_start, day_end)

    return [buckets[d] for d in wanted]


def load_label(hours: float) -> str:
    """How full a day is, in a word."""
    if hours < _LOAD_LIGHT_HOURS:
        return 'light'
    if hours < _LOAD_HEAVY_HOURS:
        return 'moderate'
    return 'heavy'


def _minutes(moment: time) -> int:
    return moment.hour * 60 + moment.minute


def busy_bar(
    day: CalDay,
    *,
    day_start: time | None = None,
    day_end: time | None = None,
) -> str:
    """The day drawn as half-hour blocks, so a week reads at a glance."""
    if day_start is None or day_end is None:
        configured = working_hours()
        day_start = day_start or configured[0]
        day_end = day_end or configured[1]

    opens, closes = _minutes(day_start), _minutes(day_end)
    slots = max(0, (closes - opens) // _BAR_MINUTES)
    cells = [BAR_FREE] * slots
    for event in day.events:
        first = (_minutes(event.start.time()) - opens) // _BAR_MINUTES
        last = -(-(_minutes(event.end.time()) - opens) // _BAR_MINUTES)
        for i in range(max(0, first), min(slots, max(last, first + 1))):
            cells[i] = BAR_BUSY
    return ''.join(cells)


def usable_gaps(day: CalDay) -> list[tuple[time, time]]:
    """Free spans long enough to actually put a next step into."""
    return [
        span
        for span in day.free_spans
        if _span_minutes(span) >= USABLE_GAP_MINUTES
    ]


def _span_minutes(span: tuple[time, time]) -> int:
    start, end = span
    return (end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)


def free_hours(day: CalDay) -> float:
    """Unbooked hours inside working hours."""
    return sum(_span_minutes(s) for s in day.free_spans) / 60


def largest_gap(day: CalDay) -> tuple[time, time] | None:
    """The longest uninterrupted hole in the day, if there is one."""
    if not day.free_spans:
        return None
    return max(day.free_spans, key=_span_minutes)


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        argv,
        capture_output=True,
        text=True,
        timeout=_TIMEOUT_SECONDS,
        check=False,
    )


def fetch_events(
    *,
    days: int = 7,
    since_days: int = 0,
    runner: object = None,
) -> list[dict]:
    """Raw Google event dicts from `gfunk grind --json`, earliest first.

    `runner` exists so tests can drive the subprocess boundary without
    one; the suite blocks real network and there is no gfunk in CI.
    """
    binary = gfunk_bin()
    if runner is None and shutil.which(binary) is None:
        raise CalendarUnavailableError(_MISSING_HINT)

    argv = [
        binary,
        'grind',
        '--json',
        '--days',
        str(days),
        '--since',
        str(since_days),
    ]
    call = _run if runner is None else runner
    try:
        result = call(argv)  # type: ignore[operator]
    except FileNotFoundError as exc:
        raise CalendarUnavailableError(_MISSING_HINT) from exc
    except subprocess.TimeoutExpired as exc:
        msg = 'Calendar timed out — is gfunk waiting on a sign-in?'
        raise CalendarUnavailableError(msg) from exc
    except OSError as exc:
        raise CalendarUnavailableError(_MISSING_HINT) from exc

    if result.returncode != 0:
        raise CalendarUnavailableError(_hint_for(result.stderr))

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        msg = f'Calendar returned something unreadable from `{binary}`'
        raise CalendarUnavailableError(msg) from exc

    if not isinstance(payload, list):
        msg = f'Calendar returned something unreadable from `{binary}`'
        raise CalendarUnavailableError(msg)
    return payload


def merged_events(
    *, days: int = 7, since_days: int = 0
) -> tuple[list[dict], str | None]:
    """Google plus every subscribed feed, in one list.

    Returns `(events, hint)`. The hint is set when Google specifically
    could not be read but a subscribed feed could — a machine with no
    `gfunk` should still show the work calendar rather than nothing, and
    the reason wants saying somewhere.

    Raises `CalendarUnavailableError` only when there is nothing at all
    to show.
    """
    from gtd import ics

    subscribed = ics.fetch_events(days=days, since_days=since_days)

    try:
        primary = fetch_events(days=days, since_days=since_days)
    except CalendarUnavailableError as exc:
        if not subscribed:
            raise
        return subscribed, exc.hint

    return [*primary, *subscribed], None


def _hint_for(stderr: str) -> str:
    """Pass gfunk's own advice through when it gave any."""
    if '--with-calendar' in (stderr or ''):
        return _OPT_IN_HINT
    first = next(
        (ln.strip() for ln in (stderr or '').splitlines() if ln.strip()),
        '',
    )
    return f'Calendar unavailable: {first}' if first else _OPT_IN_HINT
