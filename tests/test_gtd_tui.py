import asyncio
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import httpx
import pytest
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import (
    Label,
    ListItem,
    ListView,
    Static,
    TabbedContent,
    TabPane,
    Tabs,
)

import gtd.gtd_tui
import gtd.tui
from gtd.gtd_tui import (
    BaseEntryContent,
    GTDApp,
    ListsContent,
    NextStepsContent,
    ProjectsBrowseScreen,
    SeparatorListItem,
    SomedayBrowseScreen,
    TodayContent,
    WaitingForBrowseScreen,
    _classify_network_error,
    _open_steps_editor,
    _render_entry_detail,
    _render_entry_summary,
)
from gtd.notion.models import ProjectEntry
from gtd.notion.schema import STATUS_ICONS
from gtd.tui import (
    SelectModal,
    VimListView,
    remove_list_item,
    repopulate,
)


def _entry(**kwargs) -> ProjectEntry:
    defaults = {
        'page_id': 'abc123',
        'header': 'Buy milk',
        'status': 'Current Project',
        'context': 'Home',
        'next_step': 'Go to store',
        'success_condition': 'Fridge fully stocked',
        'due_date': None,
        'follow_up_date': None,
        'created_date': '2026-07-01T00:00:00',
        'updated_date': '',
    }
    return ProjectEntry(**{**defaults, **kwargs})


class TestRenderEntryDetail:
    def test_shows_header(self):
        result = _render_entry_detail(_entry(header='Buy milk'))
        assert 'Buy milk' in result

    def test_shows_status(self):
        result = _render_entry_detail(_entry(status='Current Project'))
        assert 'Current Project' in result

    def test_shows_context(self):
        result = _render_entry_detail(_entry(context='Work'))
        assert 'Work' in result

    def test_shows_next_step(self):
        result = _render_entry_detail(_entry(next_step='Write tests'))
        assert 'Write tests' in result

    def test_shows_due_date_when_present(self):
        result = _render_entry_detail(_entry(due_date='2026-07-20'))
        assert '2026-07-20' in result

    def test_no_due_line_when_absent(self):
        result = _render_entry_detail(_entry(due_date=None))
        assert 'Due' not in result

    def test_shows_follow_up_when_present(self):
        result = _render_entry_detail(_entry(follow_up_date='2026-07-25'))
        assert '2026-07-25' in result

    def test_loading_state_when_notes_none(self):
        result = _render_entry_detail(_entry(), notes=None)
        assert 'Loading' in result

    def test_shows_notes_content(self):
        result = _render_entry_detail(_entry(), notes='Important context here')
        assert 'Important context here' in result

    def test_no_notes_message_when_empty(self):
        result = _render_entry_detail(_entry(), notes='')
        assert 'No notes' in result

    def test_status_icon_in_output(self):
        result = _render_entry_detail(_entry(status='Current Project'))
        assert STATUS_ICONS['Current Project'] in result

    def test_triage_icon(self):
        result = _render_entry_detail(_entry(status='Triage'))
        assert STATUS_ICONS['Triage'] in result

    def test_empty_next_step_shows_none(self):
        result = _render_entry_detail(_entry(next_step=''))
        assert '(none)' in result

    def test_shows_success_condition(self):
        result = _render_entry_detail(
            _entry(success_condition='Ship the feature')
        )
        assert 'Ship the feature' in result

    def test_empty_success_condition_shows_none(self):
        result = _render_entry_detail(_entry(success_condition=''))
        assert '(none)' in result

    def test_multiline_notes_shown(self):
        result = _render_entry_detail(_entry(), notes='Line 1\nLine 2\nLine 3')
        assert 'Line 1' in result
        assert 'Line 3' in result


class TestRenderEntrySummary:
    def test_shows_header(self):
        result = _render_entry_summary(_entry(header='Buy milk'))
        assert 'Buy milk' in result

    def test_shows_context(self):
        result = _render_entry_summary(_entry(context='Work'))
        assert 'Work' in result

    def test_shows_status_icon(self):
        result = _render_entry_summary(_entry(status='Current Project'))
        assert STATUS_ICONS['Current Project'] in result

    def test_shows_due_date_when_present(self):
        result = _render_entry_summary(_entry(due_date='2026-07-20'))
        assert 'Jul 20' in result or '2026-07-20' in result

    def test_shows_next_step(self):
        result = _render_entry_summary(_entry(next_step='Write tests'))
        assert 'Write tests' in result


class TestClassifyNetworkError:
    def test_read_timeout_returns_warning(self):
        msg, severity = _classify_network_error(
            httpx.ReadTimeout('timed out'),
        )
        assert 'timed out' in msg.lower()
        assert severity == 'warning'

    def test_connect_timeout_returns_warning(self):
        msg, severity = _classify_network_error(
            httpx.ConnectTimeout('timed out')
        )
        assert severity == 'warning'
        assert msg

    def test_request_error_returns_error_severity(self):
        msg, severity = _classify_network_error(
            httpx.ConnectError('connection refused')
        )
        assert severity == 'error'
        assert msg

    def test_unrelated_exception_returns_empty(self):
        msg, severity = _classify_network_error(ValueError('something else'))
        assert msg == ''
        assert severity == ''

    def test_non_network_does_not_swallow(self):
        msg, _ = _classify_network_error(RuntimeError('boom'))
        assert msg == ''


class TestOpenStepsEditor:
    def _run(self, monkeypatch, editor='vim') -> list[str]:
        monkeypatch.setenv('EDITOR', editor)
        fake_app = MagicMock()
        fake_app.suspend.return_value.__enter__ = MagicMock(return_value=None)
        fake_app.suspend.return_value.__exit__ = MagicMock(return_value=False)

        captured = {}

        def fake_run(args, check=False) -> None:  # noqa: ARG001, FBT002
            captured['args'] = args

        with patch('gtd.gtd_tui.subprocess.run', side_effect=fake_run):
            asyncio.run(_open_steps_editor(fake_app))
        return captured['args']

    def test_opens_editor_at_last_line(self, monkeypatch):
        args = self._run(monkeypatch)
        assert args[0] == 'vim'
        assert args[1] == '+'
        assert args[2].endswith('.md')


class _ListHost(App):
    def compose(self) -> ComposeResult:
        yield VimListView(id='lv')


def _items(n: int, *, separator_first: bool = False) -> list[ListItem]:
    items: list[ListItem] = []
    if separator_first:
        items.append(SeparatorListItem('Context'))
    items.extend(ListItem(Label(f'item{i}')) for i in range(n))
    return items


class TestVimListViewRepopulate:
    """Regression coverage for the deferred-prune highlight bug.

    clear() defers the DOM prune, so an index assigned in the same frame
    highlights the outgoing items instead of the incoming ones. Only visible
    with a single item, where j/k can't change index to re-fire the watcher.
    """

    def _repopulate_twice(
        self,
        n: int,
        *,
        separator_first: bool = False,
    ) -> tuple[list[bool], int | None]:
        async def run() -> tuple[list[bool], int | None]:
            app = _ListHost()
            async with app.run_test() as pilot:
                lv = app.query_one('#lv', VimListView)
                await repopulate(lv, _items(1))
                await pilot.pause()
                await repopulate(
                    lv, _items(n, separator_first=separator_first)
                )
                await pilot.pause()
                return (
                    [i.highlighted for i in lv.query(ListItem)],
                    lv.index,
                )

        return asyncio.run(run())

    @pytest.mark.parametrize('n', [1, 2, 5])
    def test_first_item_highlighted_after_refresh(self, n):
        highlighted, index = self._repopulate_twice(n)
        assert index == 0
        assert highlighted == [True] + [False] * (n - 1)

    @pytest.mark.parametrize('n', [1, 3])
    def test_skips_leading_separator(self, n):
        highlighted, index = self._repopulate_twice(n, separator_first=True)
        assert index == 1
        assert highlighted == [False, True] + [False] * (n - 1)

    def test_stale_items_are_removed(self):
        async def run() -> int:
            app = _ListHost()
            async with app.run_test() as pilot:
                lv = app.query_one('#lv', VimListView)
                await repopulate(lv, _items(4))
                await pilot.pause()
                await repopulate(lv, _items(2))
                await pilot.pause()
                return len(lv.query(ListItem))

        assert asyncio.run(run()) == 2

    def test_empty_clears_index(self):
        async def run() -> tuple[int, int | None]:
            app = _ListHost()
            async with app.run_test() as pilot:
                lv = app.query_one('#lv', VimListView)
                await repopulate(lv, _items(3))
                await pilot.pause()
                await repopulate(lv, [])
                await pilot.pause()
                return len(lv.query(ListItem)), lv.index

        assert asyncio.run(run()) == (0, None)

    def test_all_separators_leaves_nothing_highlighted(self):
        async def run() -> tuple[list[bool], int | None]:
            app = _ListHost()
            async with app.run_test() as pilot:
                lv = app.query_one('#lv', VimListView)
                await repopulate(
                    lv, [SeparatorListItem('A'), SeparatorListItem('B')]
                )
                await pilot.pause()
                return (
                    [i.highlighted for i in lv.query(ListItem)],
                    lv.index,
                )

        highlighted, index = asyncio.run(run())
        assert index is None
        assert highlighted == [False, False]


class TestRemoveListItem:
    """Regression coverage for the removed-item highlight bug.

    ListItem.remove() leaves ListView.index alone, so removing the
    highlighted item leaves the index pointing at whatever slides into its
    place without highlighting it. Most visible with one item left, where
    j/k can't change index to re-fire the watcher.
    """

    def _remove_at(
        self,
        n: int,
        remove_index: int,
        highlight_index: int,
        *,
        separator_first: bool = False,
    ) -> tuple[list[bool], int | None]:
        async def run() -> tuple[list[bool], int | None]:
            app = _ListHost()
            async with app.run_test() as pilot:
                lv = app.query_one('#lv', VimListView)
                await repopulate(
                    lv, _items(n, separator_first=separator_first)
                )
                await pilot.pause()
                lv.index = highlight_index
                await pilot.pause()
                remove_list_item(lv, lv.children[remove_index])
                await pilot.pause()
                return (
                    [i.highlighted for i in lv.query(ListItem)],
                    lv.index,
                )

        return asyncio.run(run())

    def test_survivor_highlighted_when_index_unchanged(self):
        """Two items, first highlighted and removed — index stays 0."""
        highlighted, index = self._remove_at(2, 0, 0)
        assert index == 0
        assert highlighted == [True]

    def test_last_item_removed_falls_back_to_previous(self):
        highlighted, index = self._remove_at(3, 2, 2)
        assert index == 1
        assert highlighted == [False, True]

    def test_middle_item_removed_highlights_successor(self):
        highlighted, index = self._remove_at(3, 1, 1)
        assert index == 1
        assert highlighted == [False, True]

    def test_removing_above_highlight_shifts_index_down(self):
        highlighted, index = self._remove_at(3, 0, 2)
        assert index == 1
        assert highlighted == [False, True]

    def test_removing_below_highlight_leaves_index_alone(self):
        highlighted, index = self._remove_at(3, 2, 0)
        assert index == 0
        assert highlighted == [True, False]

    def test_skips_separator_when_falling_back(self):
        """Sole entry under a separator: nothing left to highlight."""
        highlighted, index = self._remove_at(1, 1, 1, separator_first=True)
        assert index is None
        assert highlighted == [False]

    def test_removing_only_item_clears_index(self):
        highlighted, index = self._remove_at(1, 0, 0)
        assert index is None
        assert highlighted == []


class TestVimListViewJumps:
    """G / gg must move the highlight, not just scroll the viewport.

    The original bindings pointed at scroll_end/scroll_home, which move the
    scroll offset and leave index untouched.
    """

    def _press(
        self,
        items: list[ListItem],
        *keys: str,
    ) -> tuple[int | None, list[bool]]:
        async def run() -> tuple[int | None, list[bool]]:
            app = _ListHost()
            async with app.run_test() as pilot:
                lv = app.query_one('#lv', VimListView)
                await repopulate(lv, items)
                await pilot.pause()
                await pilot.press(*keys)
                await pilot.pause()
                return lv.index, [i.highlighted for i in lv.query(ListItem)]

        return asyncio.run(run())

    def test_shift_g_highlights_last_item(self):
        index, highlighted = self._press(_items(4), 'G')
        assert index == 3
        assert highlighted == [False, False, False, True]

    def test_shift_g_skips_trailing_separator(self):
        items = [*_items(3), SeparatorListItem('End')]
        index, highlighted = self._press(items, 'G')
        assert index == 2
        assert highlighted == [False, False, True, False]

    def test_gg_returns_to_first_item(self):
        index, highlighted = self._press(_items(4), 'G', 'g', 'g')
        assert index == 0
        assert highlighted == [True, False, False, False]

    def test_gg_skips_leading_separator(self):
        index, highlighted = self._press(
            _items(3, separator_first=True), 'G', 'g', 'g'
        )
        assert index == 1
        assert highlighted == [False, True, False, False]

    def test_single_g_does_not_jump(self):
        index, _ = self._press(_items(4), 'G', 'g')
        assert index == 3

    def test_interrupted_g_does_not_jump(self):
        index, _ = self._press(_items(4), 'G', 'g', 'k', 'g')
        assert index == 2

    @pytest.mark.parametrize('keys', [('G',), ('g', 'g')])
    def test_empty_list_is_a_no_op(self, keys):
        index, highlighted = self._press([], *keys)
        assert index is None
        assert highlighted == []

    @pytest.mark.parametrize('keys', [('G',), ('g', 'g')])
    def test_all_separators_highlights_nothing(self, keys):
        items = [SeparatorListItem('A'), SeparatorListItem('B')]
        index, highlighted = self._press(items, *keys)
        assert index is None
        assert highlighted == [False, False]


def _declared_bindings(module) -> list[tuple[str, str, str, bool]]:
    """(class name, key, action, priority) for BINDINGS declared in module.

    Reads each class's own __dict__ so inherited BINDINGS aren't counted twice.
    """
    collected: list[tuple[str, str, str, bool]] = []
    for obj in vars(module).values():
        if not isinstance(obj, type):
            continue
        for entry in obj.__dict__.get('BINDINGS', ()):
            if isinstance(entry, Binding):
                keys, action, priority = (
                    entry.key,
                    entry.action,
                    entry.priority,
                )
            else:
                keys, action, priority = entry[0], entry[1], False
            for key in str(keys).split(','):
                collected.append((obj.__name__, key.strip(), action, priority))
    return collected


class TestVimJumpBindingsAreUncontested:
    """Guard rail: g/G belong to the jump motions and nothing else.

    A later `G`/`g` binding elsewhere would silently shadow these — and an
    App-level priority binding would beat the focused list outright.
    """

    def _bindings_for(self, key: str) -> set[tuple[str, str]]:
        modules = (gtd.tui, gtd.gtd_tui)
        return {
            (cls, action)
            for module in modules
            for cls, k, action, _ in _declared_bindings(module)
            if k == key
        }

    def test_only_known_shift_g_bindings_exist(self):
        assert self._bindings_for('G') == {
            ('VimListView', 'cursor_bottom'),
            ('GTDApp', 'focus_list_bottom'),
        }

    def test_only_known_g_bindings_exist(self):
        assert self._bindings_for('g') == {
            ('VimListView', 'cursor_top_pending'),
        }

    @pytest.mark.parametrize('key', ['g', 'G'])
    def test_jump_keys_are_never_priority(self, key):
        # A priority binding fires before the focused widget's own, which
        # would break G/gg while the list itself is focused.
        priorities = [
            (cls, action)
            for module in (gtd.tui, gtd.gtd_tui)
            for cls, k, action, priority in _declared_bindings(module)
            if k == key and priority
        ]
        assert priorities == []


class TestTabBarBottomJump:
    """G from the tab bar should enter the list and land on the last item."""

    def _boot_and_press(
        self,
        *keys: str,
        item_count: int = 4,
    ) -> tuple[str | None, int | None]:
        async def run() -> tuple[str | None, int | None]:
            with (
                patch.object(BaseEntryContent, '_load_entries'),
                patch.object(BaseEntryContent, '_load_notes'),
                patch.object(TodayContent, '_load_entries'),
                patch.object(ListsContent, '_load_notion_categories'),
            ):
                app = GTDApp()
                async with app.run_test() as pilot:
                    await pilot.pause()
                    tc = app.query_one('#tabs', TabbedContent)
                    pane = tc.query_one(f'#{tc.active}', TabPane)
                    lv = pane.query_one(VimListView)
                    await repopulate(lv, _items(item_count))
                    app.query_one(Tabs).focus()
                    await pilot.pause()
                    await pilot.press(*keys)
                    await pilot.pause()
                    # `is not None`, not truthiness: Widget defines
                    # __len__, so an empty list widget is falsy.
                    focused = app.focused
                    return (
                        focused.id if focused is not None else None,
                        lv.index,
                    )

        return asyncio.run(run())

    def test_shift_g_focuses_list_at_bottom(self):
        focused_id, index = self._boot_and_press('G')
        assert focused_id == 'entry-list'
        assert index == 3

    def test_shift_g_then_gg_returns_to_top(self):
        focused_id, index = self._boot_and_press('G', 'g', 'g')
        assert focused_id == 'entry-list'
        assert index == 0

    def test_shift_g_on_empty_list_does_not_crash(self):
        focused_id, index = self._boot_and_press('G', item_count=0)
        assert focused_id == 'entry-list'
        assert index is None

    def test_j_still_enters_the_list(self):
        # G must not disturb the existing tab-bar-to-list motion.
        focused_id, _ = self._boot_and_press('j')
        assert focused_id == 'entry-list'


class _TabHost(App):
    def __init__(self, content) -> None:
        super().__init__()
        self._content = content

    def compose(self) -> ComposeResult:
        yield self._content


class TestFilterRebuildHighlight:
    """The user-facing trigger: rebuilding an already-populated list.

    A refresh clears a frame ahead of the thread worker, so its list is
    genuinely empty by repopulate time. The filter rebuilds run in a single
    frame over live items, which is where the stale-node bug bites.
    """

    def _next_steps_highlight(self, ctx: str) -> list[bool]:
        async def run() -> list[bool]:
            content = NextStepsContent()
            app = _TabHost(content)
            entries = [
                _entry(page_id='a', header='Alpha', context='Home'),
                _entry(page_id='b', header='Bravo', context='Work'),
                _entry(page_id='c', header='Charlie', context='Work'),
            ]
            with (
                patch.object(NextStepsContent, '_load_entries'),
                patch.object(NextStepsContent, '_load_notes'),
            ):
                async with app.run_test() as pilot:
                    await content._set_entries(entries)  # noqa: SLF001
                    await pilot.pause()
                    content._ctx_filter = ctx  # noqa: SLF001
                    await content._rebuild_list()  # noqa: SLF001
                    await pilot.pause()
                    lv = content.query_one('#entry-list', VimListView)
                    return [i.highlighted for i in lv.query(ListItem)]

        return asyncio.run(run())

    def test_single_match_is_highlighted(self):
        # 'Home' has exactly one entry — the reported one-item case.
        highlighted = self._next_steps_highlight('Home')
        assert highlighted == [False, True]  # separator, then the entry

    def test_multi_match_highlights_first_entry(self):
        highlighted = self._next_steps_highlight('Work')
        assert highlighted == [False, True, False]

    def test_select_modal_filtered_to_one_is_highlighted(self):
        async def run() -> list[bool]:
            app = _TabHost(Static(''))
            async with app.run_test() as pilot:
                app.push_screen(
                    SelectModal('Pick', ['alpha', 'beta', 'gamma'])
                )
                await pilot.pause()
                await pilot.press('tab', 'b')
                for _ in range(4):
                    await pilot.pause()
                lv = app.screen.query_one('#select-list', ListView)
                return [i.highlighted for i in lv.query(ListItem)]

        assert asyncio.run(run()) == [True]


class TestTodayHabitRow:
    """The Weekly Review row is permanent — done or not — and unseparated.

    It used to vanish once complete, and a `── GTD ──` separator divided it
    from the entries. Now it stays put, flipping from a red bullet to a green
    one that reports when the review last happened.
    """

    def _today_rows(
        self,
        last_done: str | None,
        entries: list[ProjectEntry] | None = None,
    ) -> list[str]:
        async def run() -> list[str]:
            content = TodayContent()
            app = _TabHost(content)
            with (
                patch.object(TodayContent, '_load_entries'),
                patch.object(TodayContent, '_load_notes'),
                patch(
                    'gtd.storage.get_weekly_habit_date',
                    return_value=last_done,
                ),
            ):
                async with app.run_test() as pilot:
                    await content._set_entries(list(entries or []))  # noqa: SLF001
                    await pilot.pause()
                    lv = content.query_one('#entry-list', VimListView)
                    return [
                        str(i.query_one(Label).content)
                        for i in lv.query(ListItem)
                    ]

        return asyncio.run(run())

    def test_pending_review_shows_red_bullet(self):
        rows = self._today_rows(None)
        assert rows[0].startswith('[bold red]●[/bold red] Weekly Review')
        assert 'not done this week' in rows[0]

    def test_completed_review_stays_listed_in_green(self):
        today = datetime.now().date().isoformat()
        rows = self._today_rows(today)
        assert len(rows) == 1
        assert rows[0].startswith('[green]●[/green] Weekly Review')
        assert 'last: today' in rows[0]

    def test_completed_review_reports_the_date(self):
        # Monday of this week, so it counts as done but isn't today.
        monday = datetime.now().date() - timedelta(
            days=datetime.now().date().weekday()
        )
        rows = self._today_rows(monday.isoformat())
        days_ago = (datetime.now().date() - monday).days
        expected = 'today' if days_ago == 0 else f'{monday:%b %-d}'
        assert '[green]●[/green]' in rows[0]
        assert f'last: {expected}' in rows[0]

    def test_no_gtd_separator_between_habit_and_entries(self):
        rows = self._today_rows(
            None,
            [_entry(page_id='a', header='Alpha', context='Home')],
        )
        assert not any('GTD' in row for row in rows)
        # habit, context separator, entry — no divider before the context.
        assert len(rows) == 3
        assert 'Weekly Review' in rows[0]
        assert 'Home' in rows[1]

    def test_marking_done_flips_the_row_in_place(self):
        async def run() -> tuple[int, str]:
            content = TodayContent()
            app = _TabHost(content)
            done: list[str] = []
            with (
                patch.object(TodayContent, '_load_entries'),
                patch.object(TodayContent, '_load_notes'),
                patch(
                    'gtd.storage.get_weekly_habit_date',
                    side_effect=lambda _k: done[0] if done else None,
                ),
                patch(
                    'gtd.storage.set_weekly_habit_date',
                    side_effect=lambda _k: done.append(
                        datetime.now().date().isoformat()
                    ),
                ),
            ):
                async with app.run_test() as pilot:
                    await content._set_entries([])  # noqa: SLF001
                    await pilot.pause()
                    lv = content.query_one('#entry-list', VimListView)
                    item = lv.children[0]
                    content._mark_habit_done(item)  # noqa: SLF001
                    await pilot.pause()
                    return (
                        len(lv.query(ListItem)),
                        str(lv.children[0].query_one(Label).content),
                    )

        count, row = asyncio.run(run())
        assert count == 1  # the row stays on the list
        assert row.startswith('[green]●[/green] Weekly Review')
        assert 'last: today' in row


_BROWSE_SCREENS = [
    ProjectsBrowseScreen,
    WaitingForBrowseScreen,
    SomedayBrowseScreen,
]


@pytest.mark.parametrize(
    'screen_cls', _BROWSE_SCREENS, ids=lambda c: c.__name__
)
class TestReviewStepScoping:
    """Weekly Review sub-screens must not label two keys the same thing.

    `esc` finishes the review step; `d` acts on the highlighted item. Both
    were described as "Done", which made it impossible to tell which key
    completed the step and which completed the ticket.
    """

    def test_no_two_visible_bindings_share_a_description(self, screen_cls):
        shown = [b.description.lower() for b in screen_cls.BINDINGS if b.show]
        assert len(shown) == len(set(shown))

    def test_escape_is_scoped_to_the_step(self, screen_cls):
        esc = next(b for b in screen_cls.BINDINGS if b.key == 'escape')
        assert esc.action == 'finish_step'
        assert 'step' in esc.description.lower()
        assert hasattr(screen_cls, 'action_finish_step')

    def test_item_keys_never_say_bare_done(self, screen_cls):
        item_keys = [
            b for b in screen_cls.BINDINGS if b.show and b.key != 'escape'
        ]
        assert item_keys
        assert all(b.description.lower() != 'done' for b in item_keys)

    def test_footer_names_both_scopes(self, screen_cls):
        entries = [_entry(page_id='a', header='Alpha')]

        async def run() -> list[str]:
            app = _TabHost(Static(''))
            async with app.run_test(size=(100, 24)) as pilot:
                app.push_screen(screen_cls(entries))
                for _ in range(3):
                    await pilot.pause()
                return [
                    str(s.content)
                    for s in app.screen.query('.sb-footer').results(Static)
                ]

        footers = asyncio.run(run())
        assert any('this item' in f for f in footers)
        assert any('this step' in f for f in footers)


class _BrowseHost(App):
    def compose(self) -> ComposeResult:
        yield Static('')


async def _push(app, screen, pilot, pauses: int = 3) -> ModalScreen:
    app.push_screen(screen)
    for _ in range(pauses):
        await pilot.pause()
    return screen


@pytest.mark.parametrize(
    'screen_cls', _BROWSE_SCREENS, ids=lambda c: c.__name__
)
class TestBrowseScreenHighlight:
    """Rows are populated after mount, so nothing was ever highlighted.

    ListView only picks an initial index during its own mount. Appending
    afterwards left index None, so `_current_entry()` returned None and every
    item action was a silent no-op until j/k was pressed.
    """

    def _first_row_state(self, screen_cls) -> tuple[int | None, bool]:
        entries = [
            _entry(page_id='a', header='Alpha'),
            _entry(page_id='b', header='Bravo'),
        ]

        async def run() -> tuple[int | None, bool]:
            app = _BrowseHost()
            async with app.run_test(size=(100, 24)) as pilot:
                screen = await _push(app, screen_cls(entries), pilot)
                lv = screen.query_one('#sb-list', VimListView)
                return lv.index, screen._current_entry() is not None  # noqa: SLF001

        return asyncio.run(run())

    def test_first_row_is_highlighted_on_open(self, screen_cls):
        index, _ = self._first_row_state(screen_cls)
        assert index == 0

    def test_item_actions_have_a_target_immediately(self, screen_cls):
        _, has_entry = self._first_row_state(screen_cls)
        assert has_entry


_UPDATE_SCREENS = [ProjectsBrowseScreen, WaitingForBrowseScreen]


class TestBrowseUpdateAction:
    """`u: update project` replaced `d: complete project`.

    Nothing on the review screens should archive a page any more.
    """

    @pytest.mark.parametrize(
        'screen_cls', _UPDATE_SCREENS, ids=lambda c: c.__name__
    )
    def test_offers_update_not_complete(self, screen_cls):
        actions = {b.action for b in screen_cls.BINDINGS}
        assert 'update_entry' in actions
        assert 'mark_done' not in actions
        assert 'heard_back' not in actions
        descriptions = ' '.join(
            b.description.lower() for b in screen_cls.BINDINGS if b.show
        )
        assert 'complete' not in descriptions

    @pytest.mark.parametrize(
        'screen_cls', _UPDATE_SCREENS, ids=lambda c: c.__name__
    )
    def test_update_writes_and_refreshes_the_row(self, screen_cls):
        entries = [
            _entry(page_id='a', header='Alpha'),
            _entry(page_id='b', header='Bravo'),
        ]
        writes: list[tuple[str, dict]] = []

        async def run() -> tuple[list[str], str]:
            app = _BrowseHost()
            with (
                patch(
                    'gtd.notion.client.update_page',
                    side_effect=lambda pid, props: writes.append((pid, props)),
                ),
                patch(
                    'gtd.notion.client.build_property_update',
                    side_effect=lambda **kw: kw,
                ),
            ):
                async with app.run_test(size=(100, 24)) as pilot:
                    screen = await _push(app, screen_cls(entries), pilot)
                    screen.action_update_entry()
                    for _ in range(4):
                        await pilot.pause()
                    await pilot.press('enter')  # 'Name' — the first field
                    for _ in range(4):
                        await pilot.pause()
                    for _ in range(len('Alpha')):
                        await pilot.press('backspace')
                    for ch in 'Zulu':
                        await pilot.press(ch)
                    await pilot.press('enter')
                    for _ in range(6):
                        await pilot.pause()
                    lv = screen.query_one('#sb-list', VimListView)
                    rows = [
                        str(i.query_one(Label).content)
                        for i in lv.query(ListItem)
                    ]
                    return rows, screen._entries[0].header  # noqa: SLF001

        rows, header = asyncio.run(run())
        assert writes == [('a', {'name': 'Zulu'})]
        assert header == 'Zulu'  # local entry mirrors the write
        assert 'Zulu' in rows[0]  # and so does the row label
        assert 'Bravo' in rows[1]

    def test_waiting_for_change_status_opens(self):
        """SelectModal was called with a duplicated `title` kwarg."""
        entries = [_entry(page_id='a', header='Alpha', status='Waiting For')]

        async def run() -> tuple[str, object]:
            app = _BrowseHost()
            async with app.run_test(size=(100, 24)) as pilot:
                screen = await _push(
                    app, WaitingForBrowseScreen(entries), pilot
                )
                worker = screen.action_change_status()
                for _ in range(5):
                    await pilot.pause()
                return type(app.screen).__name__, worker.error

        screen_name, error = asyncio.run(run())
        assert error is None
        assert screen_name == 'SelectModal'
