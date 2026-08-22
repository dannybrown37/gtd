"""The calendar adapter: parsing, day grouping, and free-time maths.

Every test here feeds synthetic Google event dicts through the pure
functions. Nothing in this file may reach the network or the `gfunk`
binary — `test_gcal_unavailable.py` owns the subprocess boundary.
"""

from datetime import date, datetime, time

import pytest

from gtd import gcal


def timed(summary, start, end, location='') -> dict:
    return {
        'summary': summary,
        'start': {'dateTime': start},
        'end': {'dateTime': end},
        'location': location,
    }


def all_day(summary, day) -> dict:
    return {
        'summary': summary,
        'start': {'date': day},
        'end': {'date': day},
    }


MON = date(2026, 8, 24)


def test_group_days_buckets_events_by_local_date():
    events = [
        timed('Standup', '2026-08-24T09:00:00', '2026-08-24T09:30:00'),
        timed('Review', '2026-08-25T14:00:00', '2026-08-25T15:00:00'),
    ]

    days = gcal.group_days(events, start=MON, num_days=2)

    assert [d.date for d in days] == [MON, date(2026, 8, 25)]
    assert [e.summary for e in days[0].events] == ['Standup']
    assert [e.summary for e in days[1].events] == ['Review']


def test_group_days_pads_empty_days_so_the_week_is_whole():
    days = gcal.group_days([], start=MON, num_days=5)

    assert len(days) == 5
    assert all(not d.events for d in days)
    assert days[-1].date == date(2026, 8, 28)


def test_group_days_totals_booked_hours():
    events = [
        timed('A', '2026-08-24T09:00:00', '2026-08-24T10:30:00'),
        timed('B', '2026-08-24T13:00:00', '2026-08-24T14:00:00'),
    ]

    day = gcal.group_days(events, start=MON, num_days=1)[0]

    assert day.total_hours == pytest.approx(2.5)


def test_all_day_events_are_listed_but_never_counted_as_booked_time():
    events = [all_day('Danny OOO', '2026-08-24')]

    day = gcal.group_days(events, start=MON, num_days=1)[0]

    assert day.all_day == ['Danny OOO']
    assert day.events == []
    assert day.total_hours == 0.0


def test_overlapping_events_are_counted_as_conflicts():
    events = [
        timed('A', '2026-08-24T09:00:00', '2026-08-24T10:00:00'),
        timed('B', '2026-08-24T09:30:00', '2026-08-24T10:30:00'),
    ]

    day = gcal.group_days(events, start=MON, num_days=1)[0]

    assert day.conflicts == 1


def test_back_to_back_events_are_not_a_conflict():
    events = [
        timed('A', '2026-08-24T09:00:00', '2026-08-24T10:00:00'),
        timed('B', '2026-08-24T10:00:00', '2026-08-24T11:00:00'),
    ]

    day = gcal.group_days(events, start=MON, num_days=1)[0]

    assert day.conflicts == 0


def test_timezone_aware_events_are_bucketed_in_local_time():
    """Google returns offsets; a naive parse would bucket the wrong day."""
    events = [
        timed('Late', '2026-08-24T23:30:00+00:00', '2026-08-25T00:30:00+00:00')
    ]

    days = gcal.group_days(events, start=MON, num_days=2)

    assert sum(len(d.events) for d in days) == 1


def test_an_event_running_past_midnight_lands_on_its_start_day():
    events = [timed('Night', '2026-08-24T23:00:00', '2026-08-25T01:00:00')]

    days = gcal.group_days(events, start=MON, num_days=2)

    assert [e.summary for e in days[0].events] == ['Night']
    assert days[1].events == []


def test_events_outside_the_requested_window_are_dropped():
    events = [
        timed('Next month', '2026-09-30T09:00:00', '2026-09-30T10:00:00')
    ]

    days = gcal.group_days(events, start=MON, num_days=3)

    assert sum(len(d.events) for d in days) == 0


def test_free_spans_are_the_gaps_inside_working_hours():
    events = [
        timed('A', '2026-08-24T09:00:00', '2026-08-24T10:00:00'),
        timed('B', '2026-08-24T13:00:00', '2026-08-24T14:00:00'),
    ]

    day = gcal.group_days(
        events, start=MON, num_days=1, day_start=time(8), day_end=time(17)
    )[0]

    assert day.free_spans == [
        (time(8), time(9)),
        (time(10), time(13)),
        (time(14), time(17)),
    ]


def test_an_empty_day_is_one_long_free_span():
    day = gcal.group_days(
        [], start=MON, num_days=1, day_start=time(8), day_end=time(17)
    )[0]

    assert day.free_spans == [(time(8), time(17))]


def test_overlapping_events_do_not_produce_a_negative_free_span():
    events = [
        timed('A', '2026-08-24T09:00:00', '2026-08-24T12:00:00'),
        timed('B', '2026-08-24T10:00:00', '2026-08-24T11:00:00'),
    ]

    day = gcal.group_days(
        events, start=MON, num_days=1, day_start=time(8), day_end=time(17)
    )[0]

    assert day.free_spans == [(time(8), time(9)), (time(12), time(17))]


def test_events_outside_working_hours_do_not_eat_the_free_day():
    events = [timed('Dawn', '2026-08-24T05:00:00', '2026-08-24T06:00:00')]

    day = gcal.group_days(
        events, start=MON, num_days=1, day_start=time(8), day_end=time(17)
    )[0]

    assert day.free_spans == [(time(8), time(17))]
    assert day.total_hours == pytest.approx(1.0)


def test_free_hours_sums_the_gaps():
    events = [timed('A', '2026-08-24T09:00:00', '2026-08-24T10:00:00')]

    day = gcal.group_days(
        events, start=MON, num_days=1, day_start=time(8), day_end=time(17)
    )[0]

    assert gcal.free_hours(day) == pytest.approx(8.0)


def test_largest_gap_is_the_longest_free_span():
    events = [
        timed('A', '2026-08-24T09:00:00', '2026-08-24T10:00:00'),
        timed('B', '2026-08-24T13:00:00', '2026-08-24T14:00:00'),
    ]

    day = gcal.group_days(
        events, start=MON, num_days=1, day_start=time(8), day_end=time(18)
    )[0]

    assert gcal.largest_gap(day) == (time(14), time(18))


def test_largest_gap_is_none_on_a_fully_booked_day():
    events = [timed('All of it', '2026-08-24T08:00:00', '2026-08-24T17:00:00')]

    day = gcal.group_days(
        events, start=MON, num_days=1, day_start=time(8), day_end=time(17)
    )[0]

    assert gcal.largest_gap(day) is None


@pytest.mark.parametrize(
    ('hours', 'expected'),
    [
        (0.0, 'light'),
        (1.9, 'light'),
        (2.0, 'moderate'),
        (4.9, 'moderate'),
        (5.0, 'heavy'),
        (9.0, 'heavy'),
    ],
)
def test_load_label(hours, expected):
    assert gcal.load_label(hours) == expected


def test_an_event_with_no_title_still_renders():
    events = [
        {
            'start': {'dateTime': '2026-08-24T09:00:00'},
            'end': {'dateTime': '2026-08-24T10:00:00'},
        }
    ]

    day = gcal.group_days(events, start=MON, num_days=1)[0]

    assert day.events[0].summary == '(no title)'


def test_an_event_missing_its_end_is_treated_as_zero_length():
    events = [{'start': {'dateTime': '2026-08-24T09:00:00'}, 'end': {}}]

    day = gcal.group_days(events, start=MON, num_days=1)[0]

    assert day.total_hours == 0.0
    assert day.events[0].end == datetime(2026, 8, 24, 9, 0)


def test_malformed_events_are_skipped_rather_than_crashing_the_tab():
    events = [
        {'summary': 'Broken', 'start': {}},
        timed('Fine', '2026-08-24T09:00:00', '2026-08-24T10:00:00'),
    ]

    day = gcal.group_days(events, start=MON, num_days=1)[0]

    assert [e.summary for e in day.events] == ['Fine']


def a_day(
    *events: dict, day_start: time = time(8), day_end: time = time(17)
) -> gcal.CalDay:
    return gcal.group_days(
        list(events),
        start=MON,
        num_days=1,
        day_start=day_start,
        day_end=day_end,
    )[0]


def test_busy_bar_is_one_cell_per_half_hour_of_the_window():
    bar = gcal.busy_bar(a_day(), day_start=time(8), day_end=time(17))

    assert len(bar) == 18
    assert set(bar) == {gcal.BAR_FREE}


def test_busy_bar_fills_the_booked_slots():
    day = a_day(timed('A', '2026-08-24T09:00:00', '2026-08-24T10:00:00'))

    bar = gcal.busy_bar(day, day_start=time(8), day_end=time(17))

    assert bar[:2] == gcal.BAR_FREE * 2
    assert bar[2:4] == gcal.BAR_BUSY * 2
    assert bar[4:] == gcal.BAR_FREE * 14


def test_busy_bar_ignores_time_outside_the_window():
    day = a_day(
        timed('Dawn', '2026-08-24T05:00:00', '2026-08-24T06:00:00'),
        timed('Night', '2026-08-24T22:00:00', '2026-08-24T23:00:00'),
    )

    bar = gcal.busy_bar(day, day_start=time(8), day_end=time(17))

    assert set(bar) == {gcal.BAR_FREE}


def test_a_part_hour_event_still_marks_its_slot():
    day = a_day(timed('Quick', '2026-08-24T09:10:00', '2026-08-24T09:20:00'))

    bar = gcal.busy_bar(day, day_start=time(8), day_end=time(17))

    assert bar[2] == gcal.BAR_BUSY


def test_usable_gaps_drop_the_holes_too_short_to_work_in():
    day = a_day(
        timed('A', '2026-08-24T08:30:00', '2026-08-24T09:00:00'),
        timed('B', '2026-08-24T09:15:00', '2026-08-24T10:00:00'),
    )

    gaps = gcal.usable_gaps(day)

    assert (time(9, 0), time(9, 15)) not in gaps
    assert (time(10, 0), time(17, 0)) in gaps


def test_a_gap_exactly_at_the_threshold_counts():
    day = a_day(timed('A', '2026-08-24T08:45:00', '2026-08-24T17:00:00'))

    assert gcal.usable_gaps(day) == [(time(8), time(8, 45))]
