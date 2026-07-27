import asyncio
from unittest.mock import MagicMock, patch

import httpx
import pytest
from textual.app import App, ComposeResult
from textual.widgets import Label, ListItem, ListView, Static

from gtd.gtd_tui import (
    NextStepsContent,
    SeparatorListItem,
    _classify_network_error,
    _open_steps_editor,
    _render_entry_detail,
    _render_entry_summary,
)
from gtd.notion.models import ProjectEntry
from gtd.notion.schema import STATUS_ICONS
from gtd.tui import SelectModal, VimListView, repopulate


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
