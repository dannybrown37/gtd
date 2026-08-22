"""The Calendar tab: rendering, and the empty state that matters most.

Most of this exercises pure render functions, because that is where the
tab's actual content lives. The pilot tests cover the two behaviours that
only exist once mounted: a missing calendar must render as advice rather
than an error, and the list must open on today.
"""

from __future__ import annotations

import asyncio
from datetime import date, time
from unittest.mock import patch

import pytest

from gtd import gcal, gtd_tui
from gtd.notion.models import ProjectEntry


MON = date(2026, 8, 24)

# Bound once rather than reaching through the module in twenty assertions.
cal_day_row = gtd_tui._cal_day_row  # noqa: SLF001
render_day_detail = gtd_tui._render_cal_day_detail  # noqa: SLF001
entries_on = gtd_tui._entries_on  # noqa: SLF001


def timed(summary, start, end, location='') -> dict:
    return {
        'summary': summary,
        'start': {'dateTime': start},
        'end': {'dateTime': end},
        'location': location,
    }


def a_day(*events: dict, start: date = MON) -> gcal.CalDay:
    return gcal.group_days(
        list(events),
        start=start,
        num_days=1,
        day_start=time(8),
        day_end=time(17),
    )[0]


def an_entry(**kwargs) -> ProjectEntry:
    fields = {
        'page_id': 'p1',
        'header': 'Call the plumber',
        'status': 'Current Project',
        'context': '@phone',
        'next_step': '',
        'success_condition': '',
        'due_date': None,
        'follow_up_date': None,
        'created_date': '2026-08-01',
    }
    fields.update(kwargs)
    return ProjectEntry(**fields)


class TestDayRow:
    def test_a_clear_day_says_so(self):
        row = cal_day_row(a_day(), today=MON)

        assert 'clear' in row

    def test_a_busy_day_shows_count_and_hours(self):
        day = a_day(
            timed('A', '2026-08-24T09:00:00', '2026-08-24T10:00:00'),
            timed('B', '2026-08-24T11:00:00', '2026-08-24T12:00:00'),
        )

        row = cal_day_row(day, today=MON)

        assert '2x' in row
        assert '2.0h' in row

    def test_today_is_bold_and_the_past_is_dim(self):
        day = a_day()

        assert '[bold]' in cal_day_row(day, today=MON)
        assert '[dim]Mon' in cal_day_row(day, today=date(2026, 8, 26))

    @pytest.mark.parametrize(
        ('hours', 'colour'),
        [(1, 'green'), (3, 'yellow'), (7, 'red')],
    )
    def test_load_is_coloured_by_how_full_the_day_is(self, hours, colour):
        day = a_day(
            timed(
                'A',
                '2026-08-24T09:00:00',
                f'2026-08-24T{9 + hours:02d}:00:00',
            )
        )

        assert f'[{colour}]' in cal_day_row(day, today=MON)


class TestDayDetail:
    def test_events_are_listed_with_their_times(self):
        day = a_day(
            timed('Standup', '2026-08-24T09:00:00', '2026-08-24T09:30:00')
        )

        detail = render_day_detail(day, [])

        assert 'Standup' in detail
        assert '9am-9:30am' in detail

    def test_a_location_is_trimmed_to_its_first_part(self):
        day = a_day(
            timed(
                'Lunch',
                '2026-08-24T12:00:00',
                '2026-08-24T13:00:00',
                location='Cafe Roma, 12 High St, Springfield',
            )
        )

        detail = render_day_detail(day, [])

        assert 'Cafe Roma' in detail
        assert 'Springfield' not in detail

    def test_an_empty_day_says_nothing_is_scheduled(self):
        detail = render_day_detail(a_day(), [])

        assert 'Nothing scheduled' in detail

    def test_all_day_items_are_shown_without_a_time(self):
        day = a_day({'summary': 'OOO', 'start': {'date': '2026-08-24'}})

        detail = render_day_detail(day, [])

        assert 'OOO' in detail
        assert 'Nothing scheduled' not in detail

    def test_open_time_lists_the_usable_gaps(self):
        day = a_day(timed('A', '2026-08-24T09:00:00', '2026-08-24T10:00:00'))

        detail = render_day_detail(day, [])

        assert 'Open time' in detail
        assert '10am-5pm' in detail

    def test_a_full_day_reports_no_usable_gaps(self):
        day = a_day(timed('All', '2026-08-24T08:00:00', '2026-08-24T17:00:00'))

        detail = render_day_detail(day, [])

        assert 'No usable gaps' in detail

    def test_overlaps_are_called_out(self):
        day = a_day(
            timed('A', '2026-08-24T09:00:00', '2026-08-24T10:00:00'),
            timed('B', '2026-08-24T09:30:00', '2026-08-24T10:30:00'),
        )

        detail = render_day_detail(day, [])

        assert '1 overlap' in detail

    def test_next_steps_due_that_day_appear_under_the_calendar(self):
        entries = [
            an_entry(header='Call the plumber', due_date='2026-08-24'),
            an_entry(page_id='p2', header='Not today', due_date='2026-08-25'),
        ]

        detail = render_day_detail(a_day(), entries)

        assert 'Call the plumber' in detail
        assert 'Not today' not in detail

    def test_a_follow_up_date_counts_as_due_that_day(self):
        entries = [an_entry(header='Chase Bob', follow_up_date='2026-08-24')]

        detail = render_day_detail(a_day(), entries)

        assert 'Chase Bob' in detail

    def test_a_notion_datetime_still_matches_the_day(self):
        """Notion can hand back a full timestamp, not just a date."""
        entries = [
            an_entry(header='Timed', due_date='2026-08-24T15:00:00.000+01:00')
        ]

        detail = render_day_detail(a_day(), entries)

        assert 'Timed' in detail

    def test_a_day_with_nothing_due_says_so(self):
        detail = render_day_detail(a_day(), [])

        assert 'Nothing due' in detail


class TestEntriesOn:
    def test_an_entry_with_no_dates_is_never_matched(self):
        assert entries_on([an_entry()], MON) == []


class TestMounted:
    """Behaviour that only exists once the widget is on screen."""

    def _run(self, *, events=None, unavailable=None, entries=()) -> tuple:
        async def go() -> tuple:
            def fake_fetch(**_: object) -> list:
                if unavailable is not None:
                    raise gcal.CalendarUnavailableError(unavailable)
                return events or []

            with (
                patch.object(gcal, 'fetch_events', side_effect=fake_fetch),
                patch(
                    'gtd.notion.views.next_steps_entries',
                    return_value=list(entries),
                ),
            ):
                app = gtd_tui.GTDApp()
                async with app.run_test() as pilot:
                    tabs = app.query_one('#tabs', gtd_tui.TabbedContent)
                    tabs.active = 'tab-calendar'
                    await pilot.pause()
                    content = app.query_one(gtd_tui.CalendarContent)
                    for _ in range(20):
                        await pilot.pause()
                    header = content.query_one(
                        '#entry-list-header', gtd_tui.Static
                    )
                    detail = content.query_one('#entry-detail', gtd_tui.Static)
                    return (
                        str(header.render()),
                        str(detail.render()),
                        list(content._days),  # noqa: SLF001
                        content.query_one(
                            '#entry-list', gtd_tui.VimListView
                        ).index,
                    )

        return asyncio.run(go())

    def test_a_missing_calendar_renders_the_hint_not_an_error(self):
        header, detail, days, _ = self._run(unavailable='Run gfunk mount-up')

        assert 'Run gfunk mount-up' in detail
        assert 'off' in header
        assert days == []

    def test_the_tab_exists_and_loads_a_window_of_days(self):
        _, _, days, _ = self._run(events=[])

        assert len(days) == (gtd_tui.CAL_DAYS_BEHIND + gtd_tui.CAL_DAYS_AHEAD)

    def test_the_list_opens_on_today(self):
        _, _, days, index = self._run(events=[])

        assert days[index].date == date.today()

    def test_a_notion_outage_still_renders_the_calendar(self):
        async def go() -> tuple:
            with (
                patch.object(gcal, 'fetch_events', return_value=[]),
                patch(
                    'gtd.notion.views.next_steps_entries',
                    side_effect=RuntimeError('Notion is down'),
                ),
            ):
                app = gtd_tui.GTDApp()
                async with app.run_test() as pilot:
                    tabs = app.query_one('#tabs', gtd_tui.TabbedContent)
                    tabs.active = 'tab-calendar'
                    for _ in range(20):
                        await pilot.pause()
                    content = app.query_one(gtd_tui.CalendarContent)
                    return (
                        len(content._days),  # noqa: SLF001
                        content._entries,  # noqa: SLF001
                    )

        days, entries = asyncio.run(go())

        assert days > 0
        assert entries == []
