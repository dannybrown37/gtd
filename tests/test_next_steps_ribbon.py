"""The Today load ribbon on the Next Steps tab.

The ribbon is the one place calendar data reaches the tab people
actually live in, so what matters is: it says the right thing, it never
steals the highlight, and it is simply absent when there is no calendar.
"""

from __future__ import annotations

import asyncio
from datetime import date, time
from unittest.mock import patch

import pytest

from gtd import gcal, gtd_tui
from gtd.tui import repopulate


today_load_text = gtd_tui._today_load_text  # noqa: SLF001

TODAY = date.today()


def timed(summary: str, start: str, end: str) -> dict:
    return {
        'summary': summary,
        'start': {'dateTime': f'{TODAY.isoformat()}T{start}'},
        'end': {'dateTime': f'{TODAY.isoformat()}T{end}'},
    }


def a_day(*events: dict) -> gcal.CalDay:
    return gcal.group_days(
        list(events),
        start=TODAY,
        num_days=1,
        day_start=time(8),
        day_end=time(17),
    )[0]


class TestRibbonText:
    def test_it_reports_booked_and_free_hours(self):
        text = today_load_text(a_day(timed('A', '09:00:00', '11:00:00')))

        assert '2.0h booked' in text
        assert '7.0h free' in text

    def test_it_names_the_largest_gap(self):
        day = a_day(
            timed('A', '09:00:00', '10:00:00'),
            timed('B', '11:00:00', '11:30:00'),
        )

        assert 'largest gap [green]11:30am-5pm[/green]' in today_load_text(day)

    def test_a_clear_day_offers_the_whole_window(self):
        text = today_load_text(a_day())

        assert '0.0h booked' in text
        assert '8am-5pm' in text

    def test_a_full_day_says_there_is_no_open_time(self):
        text = today_load_text(a_day(timed('All', '08:00:00', '17:00:00')))

        assert 'no open time' in text
        assert 'largest gap' not in text

    @pytest.mark.parametrize(
        ('end', 'colour'),
        [('09:30:00', 'green'), ('12:00:00', 'yellow'), ('16:00:00', 'red')],
    )
    def test_the_booked_hours_are_coloured_by_load(self, end, colour):
        text = today_load_text(a_day(timed('A', '09:00:00', end)))

        assert f'[{colour}]' in text


class TestRibbonInTheList:
    """Mounted behaviour: the ribbon must not act like a row."""

    def _boot(self, *, calendar_events=None, fail=False) -> tuple:
        async def go() -> tuple:
            def fake_fetch(**_: object) -> list:
                if fail:
                    msg = 'no gfunk here'
                    raise gcal.CalendarUnavailableError(msg)
                return calendar_events or []

            with (
                patch.object(gcal, 'fetch_events', side_effect=fake_fetch),
                patch(
                    'gtd.notion.views.next_steps_entries',
                    return_value=[],
                ),
            ):
                app = gtd_tui.GTDApp()
                async with app.run_test() as pilot:
                    for _ in range(20):
                        await pilot.pause()
                    content = app.query_one(gtd_tui.NextStepsContent)
                    lv = content.query_one('#entry-list', gtd_tui.VimListView)
                    ribbons = list(lv.query(gtd_tui.LoadRibbonItem))
                    return (
                        ribbons,
                        lv.index,
                        list(lv.children),
                    )

        return asyncio.run(go())

    def test_no_calendar_means_no_ribbon(self):
        ribbons, _, _ = self._boot(fail=True)

        assert ribbons == []

    def test_a_reachable_calendar_puts_the_ribbon_first(self):
        ribbons, _, children = self._boot(
            calendar_events=[timed('A', '09:00:00', '10:00:00')]
        )

        assert len(ribbons) == 1
        assert children[0] is ribbons[0]
        assert 'booked' in ribbons[0].raw_text

    def test_the_ribbon_never_takes_the_highlight(self):
        ribbons, index, children = self._boot(
            calendar_events=[timed('A', '09:00:00', '10:00:00')]
        )

        assert ribbons[0].disabled
        assert index != children.index(ribbons[0])

    def test_the_ribbon_is_not_mistaken_for_an_entry_or_a_habit(self):
        """`_current_entry` and `_current_habit_item` both type-check."""
        ribbon = gtd_tui.LoadRibbonItem('anything')

        assert not isinstance(ribbon, gtd_tui.EntryListItem)
        assert not isinstance(ribbon, gtd_tui.WeeklyHabitItem)


class TestRibbonSurvivesRebuild:
    def test_a_rebuilt_list_keeps_the_ribbon(self):
        """Filtering by context rebuilds the list from scratch."""

        async def go() -> int:
            with (
                patch.object(gcal, 'fetch_events', return_value=[]),
                patch(
                    'gtd.notion.views.next_steps_entries',
                    return_value=[],
                ),
            ):
                app = gtd_tui.GTDApp()
                async with app.run_test() as pilot:
                    for _ in range(20):
                        await pilot.pause()
                    content = app.query_one(gtd_tui.NextStepsContent)
                    await content._rebuild_list()  # noqa: SLF001
                    await pilot.pause()
                    lv = content.query_one('#entry-list', gtd_tui.VimListView)
                    return len(lv.query(gtd_tui.LoadRibbonItem))

        assert asyncio.run(go()) == 1

    def test_a_second_load_updates_rather_than_duplicates(self):
        async def go() -> tuple:
            with (
                patch.object(gcal, 'fetch_events', return_value=[]),
                patch(
                    'gtd.notion.views.next_steps_entries',
                    return_value=[],
                ),
            ):
                app = gtd_tui.GTDApp()
                async with app.run_test() as pilot:
                    for _ in range(20):
                        await pilot.pause()
                    content = app.query_one(gtd_tui.NextStepsContent)
                    content._show_load_ribbon('second reading')  # noqa: SLF001
                    await pilot.pause()
                    lv = content.query_one('#entry-list', gtd_tui.VimListView)
                    found = list(lv.query(gtd_tui.LoadRibbonItem))
                    return len(found), found[0].raw_text

        count, text = asyncio.run(go())

        assert count == 1
        assert text == 'second reading'


class TestRibbonDoesNotDisturbNavigation:
    def test_repopulate_still_highlights_the_first_real_row(self):
        """The ribbon is disabled, so `repopulate` must skip past it."""

        async def go() -> int | None:
            with (
                patch.object(gcal, 'fetch_events', return_value=[]),
                patch('gtd.notion.views.next_steps_entries', return_value=[]),
            ):
                app = gtd_tui.GTDApp()
                async with app.run_test() as pilot:
                    for _ in range(20):
                        await pilot.pause()
                    content = app.query_one(gtd_tui.NextStepsContent)
                    lv = content.query_one('#entry-list', gtd_tui.VimListView)
                    await repopulate(
                        lv,
                        [
                            gtd_tui.LoadRibbonItem('Today  1.0h booked'),
                            gtd_tui.WeeklyHabitItem('k', 'A habit'),
                        ],
                    )
                    await pilot.pause()
                    return lv.index

        assert asyncio.run(go()) == 1
