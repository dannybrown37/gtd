"""Subscribed iCalendar feeds (the work Outlook calendar).

Every fixture here is synthetic. The real feed URL is a bearer secret —
anyone holding it can read the calendar forever — so it lives only in the
environment, never in the repo and never in a test.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from gtd import ics


ET = ZoneInfo('America/New_York')


def build_ics(*vevents: str) -> str:
    """A minimal Exchange-shaped calendar wrapping the given VEVENTs."""
    body = '\n'.join(vevents)
    return (
        'BEGIN:VCALENDAR\n'
        'METHOD:PUBLISH\n'
        'PRODID:Microsoft Exchange Server 2010\n'
        'VERSION:2.0\n'
        f'{body}\n'
        'END:VCALENDAR\n'
    )


def vevent(
    *,
    uid: str = 'uid-1',
    summary: str = 'Busy',
    start: str = '20260824T090000',
    end: str = '20260824T100000',
    tzid: str | None = 'Eastern Standard Time',
    busy: str = 'BUSY',
    status: str = 'CONFIRMED',
    rrule: str | None = None,
    extra: str = '',
) -> str:
    when = f';TZID={tzid}' if tzid else ''
    lines = [
        'BEGIN:VEVENT',
        f'UID:{uid}',
        f'SUMMARY:{summary}',
        f'DTSTART{when}:{start}',
        f'DTEND{when}:{end}',
        f'X-MICROSOFT-CDO-BUSYSTATUS:{busy}',
        f'STATUS:{status}',
    ]
    if rrule:
        lines.append(f'RRULE:{rrule}')
    if extra:
        lines.append(extra)
    lines.append('END:VEVENT')
    return '\n'.join(lines)


WINDOW_START = date(2026, 8, 24)
WINDOW_END = date(2026, 8, 31)


def parse(text: str, *, start: date = WINDOW_START, end: date = WINDOW_END):
    return ics.parse_events(text, start=start, end=end)


class TestParsing:
    def test_a_single_event_becomes_a_google_shaped_dict(self):
        found = parse(build_ics(vevent(summary='Standup')))

        assert len(found) == 1
        event = found[0]
        assert event['summary'] == 'Standup'
        assert 'dateTime' in event['start']
        assert 'dateTime' in event['end']

    def test_a_windows_timezone_name_is_resolved(self):
        """Exchange emits 'Eastern Standard Time', not 'America/New_York'."""
        found = parse(build_ics(vevent(start='20260824T090000')))

        moment = datetime.fromisoformat(found[0]['start']['dateTime'])
        assert moment.utcoffset() == timedelta(hours=-4)

    def test_events_outside_the_window_are_not_returned(self):
        found = parse(
            build_ics(vevent(start='20260901T090000', end='20260901T100000'))
        )

        assert found == []

    def test_a_weekly_rule_is_expanded_into_occurrences(self):
        found = parse(
            build_ics(
                vevent(
                    start='20260824T090000',
                    end='20260824T093000',
                    rrule='FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR',
                )
            )
        )

        assert len(found) == 5

    def test_an_excluded_occurrence_is_dropped(self):
        found = parse(
            build_ics(
                vevent(
                    start='20260824T090000',
                    end='20260824T093000',
                    rrule='FREQ=DAILY',
                    extra=(
                        'EXDATE;TZID=Eastern Standard Time:20260825T090000'
                    ),
                )
            )
        )

        days = {e['start']['dateTime'][:10] for e in found}
        assert '2026-08-25' not in days

    def test_an_all_day_event_keeps_a_date_not_a_time(self):
        found = parse(
            build_ics(
                'BEGIN:VEVENT\n'
                'UID:allday\n'
                'SUMMARY:Away\n'
                'DTSTART;VALUE=DATE:20260824\n'
                'DTEND;VALUE=DATE:20260825\n'
                'END:VEVENT'
            )
        )

        assert found[0]['start'] == {'date': '2026-08-24'}

    def test_every_event_is_marked_with_its_source(self):
        found = parse(build_ics(vevent()))

        assert found[0]['_source'] == ics.SOURCE_LABEL

    def test_an_event_with_no_summary_is_still_returned(self):
        found = parse(build_ics(vevent(summary='')))

        assert len(found) == 1

    def test_a_broken_feed_raises_rather_than_returning_nonsense(self):
        with pytest.raises(ics.FeedUnavailableError):
            parse('this is not a calendar')

    def test_an_empty_calendar_is_simply_no_events(self):
        assert parse(build_ics()) == []


class TestBusyStatus:
    """Free blocks are explicitly not-busy; everything else holds time."""

    @pytest.mark.parametrize('status', ['BUSY', 'TENTATIVE', 'OOF'])
    def test_committed_statuses_are_kept(self, status):
        found = parse(build_ics(vevent(busy=status)))

        assert len(found) == 1

    def test_free_blocks_are_dropped(self):
        found = parse(build_ics(vevent(summary='Free', busy='FREE')))

        assert found == []

    def test_a_transparent_event_is_dropped_too(self):
        found = parse(build_ics(vevent(extra='TRANSP:TRANSPARENT')))

        assert found == []

    def test_an_event_with_no_busy_status_is_assumed_busy(self):
        found = parse(
            build_ics(
                'BEGIN:VEVENT\n'
                'UID:plain\n'
                'SUMMARY:Meeting\n'
                'DTSTART;TZID=Eastern Standard Time:20260824T090000\n'
                'DTEND;TZID=Eastern Standard Time:20260824T100000\n'
                'END:VEVENT'
            )
        )

        assert len(found) == 1

    def test_a_cancelled_event_is_dropped(self):
        found = parse(build_ics(vevent(status='CANCELLED')))

        assert found == []


class TestFeedUrls:
    def test_no_configuration_means_no_feeds(self, monkeypatch):
        monkeypatch.delenv(ics.FEED_URL_ENV, raising=False)
        monkeypatch.setattr(ics, 'get_config_value', lambda _: None)

        assert ics.feed_urls() == []

    def test_the_env_var_wins_over_config(self, monkeypatch):
        monkeypatch.setenv(ics.FEED_URL_ENV, 'https://example.com/a.ics')
        monkeypatch.setattr(
            ics, 'get_config_value', lambda _: 'https://example.com/b.ics'
        )

        assert ics.feed_urls() == ['https://example.com/a.ics']

    def test_several_feeds_can_be_comma_separated(self, monkeypatch):
        monkeypatch.setenv(
            ics.FEED_URL_ENV,
            'https://example.com/a.ics, https://example.com/b.ics',
        )

        assert len(ics.feed_urls()) == 2

    def test_blank_entries_are_ignored(self, monkeypatch):
        monkeypatch.setenv(ics.FEED_URL_ENV, 'https://example.com/a.ics,,  ')

        assert ics.feed_urls() == ['https://example.com/a.ics']

    @pytest.mark.parametrize(
        'url',
        [
            pytest.param('file:///etc/passwd', id='file'),
            pytest.param('ftp://example.com/a.ics', id='ftp'),
            pytest.param('not a url', id='garbage'),
        ],
    )
    def test_only_http_urls_are_accepted(self, monkeypatch, url):
        """A feed URL comes from config; it must not become a file read."""
        monkeypatch.setenv(ics.FEED_URL_ENV, url)

        assert ics.feed_urls() == []

    def test_webcal_is_rewritten_to_https(self, monkeypatch):
        """Outlook and Apple hand out webcal:// links."""
        monkeypatch.setenv(ics.FEED_URL_ENV, 'webcal://example.com/a.ics')

        assert ics.feed_urls() == ['https://example.com/a.ics']


class TestFetching:
    def test_no_feeds_configured_returns_nothing(self, monkeypatch):
        monkeypatch.setattr(ics, 'feed_urls', list)

        assert ics.fetch_events(days=7) == []

    def test_a_configured_feed_is_read_and_parsed(self, monkeypatch):
        monkeypatch.setattr(
            ics, 'feed_urls', lambda: ['https://example.com/a.ics']
        )
        monkeypatch.setattr(ics, '_get', lambda _url: build_ics(vevent()))

        found = ics.fetch_events(days=7, since_days=0, today=date(2026, 8, 24))

        assert len(found) == 1

    def test_one_broken_feed_does_not_lose_the_others(self, monkeypatch):
        def fake_get(url: str) -> str:
            if 'bad' in url:
                msg = 'boom'
                raise ics.FeedUnavailableError(msg)
            return build_ics(vevent())

        monkeypatch.setattr(
            ics,
            'feed_urls',
            lambda: ['https://example.com/bad.ics', 'https://e.com/ok.ics'],
        )
        monkeypatch.setattr(ics, '_get', fake_get)

        found = ics.fetch_events(days=7, since_days=0, today=date(2026, 8, 24))

        assert len(found) == 1

    def test_every_feed_failing_is_still_not_an_exception(self, monkeypatch):
        def fake_get(_url: str) -> str:
            msg = 'boom'
            raise ics.FeedUnavailableError(msg)

        monkeypatch.setattr(
            ics, 'feed_urls', lambda: ['https://example.com/a.ics']
        )
        monkeypatch.setattr(ics, '_get', fake_get)

        assert ics.fetch_events(days=7) == []
