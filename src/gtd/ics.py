"""Subscribed iCalendar feeds — the work calendar Google can't see.

A published Outlook/Exchange feed will happily hand out an `.ics` URL that
Google Calendar refuses to subscribe to, which leaves a whole working week
invisible to `gcal.py`. This module reads those feeds directly and returns
events in the **same shape Google's API returns**, so `gcal.group_days`
and every surface above it stay untouched.

Two things worth knowing:

* **The feed URL is a bearer secret.** Anyone holding it can read the
  calendar indefinitely, with no sign-in. It lives in `GTD_ICS_URL` or
  the config file, never in the repo, and never in a test fixture.
* **Published feeds are usually privacy-stripped.** Exchange sends
  `Busy` / `Tentative` / `Free` / `Away` as the summary, with no real
  subject or location. That is enough for the question this tool asks —
  where are the holes in the day — and no more.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

import httpx

from gtd.notion.config import get_config_value


if TYPE_CHECKING:
    from collections.abc import Iterable


__all__ = [
    'FEED_URL_ENV',
    'SOURCE_LABEL',
    'FeedUnavailableError',
    'feed_urls',
    'fetch_events',
    'parse_events',
]

FEED_URL_ENV = 'GTD_ICS_URL'
FEED_CONFIG_KEY = 'ics_url'

SOURCE_LABEL = 'work'

_TIMEOUT_SECONDS = 20
_MAX_BYTES = 10 * 1024 * 1024

# Exchange's own busy flag. Only FREE means the block doesn't hold time —
# TENTATIVE and OOF both do.
_FREE_STATUSES = {'FREE'}


class FeedUnavailableError(Exception):
    """A feed could not be fetched or could not be parsed."""


def feed_urls() -> list[str]:
    """Configured feeds — env first, then config. Never a local path.

    The URL is attacker-controlled as far as this code knows (it comes
    from a config file), so anything that isn't http(s) is dropped rather
    than handed to a fetcher that would happily read `file:///`.
    """
    raw = os.environ.get(FEED_URL_ENV) or get_config_value(FEED_CONFIG_KEY)
    if not raw:
        return []
    found = []
    for part in raw.split(','):
        url = part.strip()
        if url.startswith('webcal://'):
            url = 'https://' + url[len('webcal://') :]
        if url.startswith(('http://', 'https://')):
            found.append(url)
    return found


def _get(url: str) -> str:
    """Fetch one feed's text, refusing anything implausibly large."""
    try:
        with (
            httpx.Client(
                timeout=_TIMEOUT_SECONDS, follow_redirects=True
            ) as client,
            client.stream('GET', url) as response,
        ):
            response.raise_for_status()
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > _MAX_BYTES:
                    msg = 'Calendar feed is implausibly large; refusing it'
                    raise FeedUnavailableError(msg)
                chunks.append(chunk)
    except httpx.HTTPError as exc:
        msg = f'Could not read calendar feed: {exc}'
        raise FeedUnavailableError(msg) from exc
    return b''.join(chunks).decode('utf-8', errors='replace')


def _is_busy(component: object) -> bool:
    """Whether this block actually holds time."""
    get = component.get  # type: ignore[attr-defined]
    if str(get('STATUS', '')).upper() == 'CANCELLED':
        return False
    if str(get('TRANSP', '')).upper() == 'TRANSPARENT':
        return False
    status = str(get('X-MICROSOFT-CDO-BUSYSTATUS', '')).upper()
    return status not in _FREE_STATUSES


def _as_google_event(component: object) -> dict | None:
    """Re-shape one occurrence into the dict Google's API would return.

    Matching Google's shape rather than inventing a third one is the
    whole trick: `gcal.group_days` and every renderer above it keep
    working with no idea a second source exists.
    """
    get = component.get  # type: ignore[attr-defined]
    start = get('DTSTART')
    end = get('DTEND')
    if start is None:
        return None

    begins = start.dt
    finishes = end.dt if end is not None else begins

    if isinstance(begins, datetime):
        payload = {
            'start': {'dateTime': begins.isoformat()},
            'end': {
                'dateTime': (
                    finishes.isoformat()
                    if isinstance(finishes, datetime)
                    else begins.isoformat()
                )
            },
        }
    elif isinstance(begins, date):
        payload = {
            'start': {'date': begins.isoformat()},
            'end': {
                'date': (
                    finishes.isoformat()
                    if isinstance(finishes, date)
                    else begins.isoformat()
                )
            },
        }
    else:
        return None

    return {
        'summary': str(get('SUMMARY', '') or ''),
        'location': str(get('LOCATION', '') or ''),
        '_source': SOURCE_LABEL,
        **payload,
    }


def parse_events(text: str, *, start: date, end: date) -> list[dict]:
    """Expand one feed's recurrence into occurrences inside the window.

    Recurrence is the part worth delegating: this feed carries repeating
    rules, per-occurrence exclusions, and single-instance overrides that
    must suppress the instance they replace. A hand-rolled expander gets
    that last one wrong quietly, so `recurring_ical_events` owns it.
    """
    import icalendar
    import recurring_ical_events

    try:
        calendar = icalendar.Calendar.from_ical(text)
        occurrences = recurring_ical_events.of(calendar).between(start, end)
    except Exception as exc:
        msg = f'Could not parse calendar feed: {exc}'
        raise FeedUnavailableError(msg) from exc

    found = []
    for component in occurrences:
        if not _is_busy(component):
            continue
        event = _as_google_event(component)
        if event is not None:
            found.append(event)
    return found


def fetch_events(
    *,
    days: int = 7,
    since_days: int = 0,
    today: date | None = None,
    urls: Iterable[str] | None = None,
) -> list[dict]:
    """Every configured feed's events, in Google's shape.

    Never raises. A feed that is unreachable, expired or malformed is
    skipped — one dead subscription must not blank out the calendar, and
    no feeds at all is the ordinary case.
    """
    start = (today or date.today()) - timedelta(days=since_days)
    end = start + timedelta(days=since_days + days)

    found: list[dict] = []
    for url in list(urls) if urls is not None else feed_urls():
        # Deliberately broad: one dead subscription must never blank out
        # the calendar, and there is nowhere useful to report it from a
        # background worker. `S112` is about losing a signal that would
        # otherwise be actionable; here the empty tab is the signal.
        try:
            found.extend(parse_events(_get(url), start=start, end=end))
        except Exception:  # noqa: S112
            continue
    return found
