"""Shared Textual widgets and modals for the GTD TUI."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from textual import on
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Input,
    Label,
    ListItem,
    ListView,
    Static,
)

if TYPE_CHECKING:
    from textual.app import ComposeResult
    from textual.events import Key


_MODAL_CSS = """
.modal-box {
    background: $surface;
    border: solid $accent;
    padding: 1 2;
    height: auto;
}
.modal-title {
    text-style: bold;
    margin-bottom: 1;
    height: auto;
    width: 1fr;
}
.modal-subtitle {
    color: $text-muted;
    margin-bottom: 1;
    height: auto;
    width: 1fr;
}
.field-label {
    margin-top: 1;
    color: $text-muted;
}
.modal-buttons {
    margin-top: 1;
    align-horizontal: right;
}
.modal-buttons Button {
    margin-left: 1;
}
"""


class InputModal(ModalScreen[str | None]):
    """Single text input modal."""

    DEFAULT_CSS = (
        _MODAL_CSS
        + """
    InputModal { align: center middle; }
    InputModal .modal-box { width: 60; }
    """
    )

    BINDINGS: ClassVar[list[Binding]] = [Binding('escape', 'cancel', 'Cancel')]

    def __init__(
        self,
        title: str,
        placeholder: str = '',
        initial: str = '',
        subtitle: str = '',
    ) -> None:
        super().__init__()
        self._title = title
        self._placeholder = placeholder
        self._initial = initial
        self._subtitle = subtitle

    def compose(self) -> ComposeResult:
        with Vertical(classes='modal-box'):
            yield Label(self._title, classes='modal-title')
            if self._subtitle:
                yield Label(self._subtitle, classes='modal-subtitle')
            yield Input(
                value=self._initial,
                placeholder=self._placeholder,
                id='the-input',
            )

    def on_mount(self) -> None:
        self.query_one('#the-input', Input).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    @on(Input.Submitted)
    def submitted(self) -> None:
        self.dismiss(self.query_one('#the-input', Input).value.strip())


class TwoFieldModal(ModalScreen[tuple[str, str] | None]):
    """Two-field input modal."""

    DEFAULT_CSS = (
        _MODAL_CSS
        + """
    TwoFieldModal { align: center middle; }
    TwoFieldModal .modal-box { width: 70; }
    """
    )

    BINDINGS: ClassVar[list[Binding]] = [
        Binding('escape', 'cancel', 'Cancel', show=False)
    ]

    def __init__(
        self,
        title: str,
        label1: str,
        *,
        placeholder1: str = '',
        label2: str = '',
        placeholder2: str = '',
        initial1: str = '',
        initial2: str = '',
    ) -> None:
        super().__init__()
        self._title = title
        self._label1 = label1
        self._placeholder1 = placeholder1
        self._label2 = label2
        self._placeholder2 = placeholder2
        self._initial1 = initial1
        self._initial2 = initial2

    def compose(self) -> ComposeResult:
        with Vertical(classes='modal-box'):
            yield Label(self._title, classes='modal-title')
            yield Label(self._label1, classes='field-label')
            yield Input(
                value=self._initial1,
                placeholder=self._placeholder1,
                id='input1',
            )
            yield Label(self._label2, classes='field-label')
            yield Input(
                value=self._initial2,
                placeholder=self._placeholder2,
                id='input2',
            )
            with Horizontal(classes='modal-buttons'):
                yield Button('OK', variant='primary', id='ok')
                yield Button('Cancel', id='cancel')

    def on_mount(self) -> None:
        self.query_one('#input1', Input).focus()

    @on(Button.Pressed, '#ok')
    def confirm(self) -> None:
        v1 = self.query_one('#input1', Input).value.strip()
        v2 = self.query_one('#input2', Input).value.strip()
        self.dismiss((v1, v2) if v1 else None)

    @on(Button.Pressed, '#cancel')
    def action_cancel(self) -> None:
        self.dismiss(None)


class SelectModal(ModalScreen[str | None]):
    """Filterable selection modal.

    Filter mode (Input focused): type to filter, arrows navigate.
    Browse mode (ListView focused): j/k navigate, any printable char
    (except j/k) jumps back to Input. Tab toggles between modes.

    If allow_new=True, typing a value with no matches and pressing Enter
    creates a new option with that value.
    """

    DEFAULT_CSS = (
        _MODAL_CSS
        + """
    SelectModal { align: center middle; }
    SelectModal .modal-box { width: 70; max-height: 35; }
    SelectModal Input { margin-bottom: 1; }
    SelectModal ListView {
        height: auto;
        max-height: 20;
        border: solid $panel;
    }
    SelectModal #new-hint { padding: 0 1; color: $text-muted; }
    """
    )

    BINDINGS: ClassVar[list[Binding]] = [
        Binding('escape', 'cancel', 'Cancel'),
        Binding('down', 'cursor_down', show=False),
        Binding('up', 'cursor_up', show=False),
        Binding('j', 'cursor_down', show=False),
        Binding('k', 'cursor_up', show=False),
        Binding('tab', 'toggle_focus', show=False),
    ]

    def __init__(
        self,
        title: str,
        items: list[str],
        *,
        allow_new: bool = False,
    ) -> None:
        super().__init__()
        self._title = title
        self._all_items = items
        self._filtered = list(items)
        self._allow_new = allow_new

    def compose(self) -> ComposeResult:
        with Vertical(classes='modal-box'):
            yield Label(self._title, classes='modal-title')
            yield Input(placeholder='tab → filter', id='filter-input')
            yield ListView(
                *[ListItem(Label(item)) for item in self._all_items],
                id='select-list',
            )
            yield Static('', id='new-hint')

    def on_mount(self) -> None:
        lv = self.query_one('#select-list', ListView)
        if self._all_items:
            lv.index = 0
        lv.focus()
        self.query_one('#new-hint', Static).display = False

    @on(Input.Changed, '#filter-input')
    def filter_changed(self, event: Input.Changed) -> None:
        query = event.value.lower()
        self._filtered = [i for i in self._all_items if query in i.lower()]
        lv = self.query_one('#select-list', ListView)
        hint = self.query_one('#new-hint', Static)
        lv.clear()
        for item in self._filtered:
            lv.append(ListItem(Label(item)))
        if self._filtered:
            lv.index = 0
        if self._allow_new and event.value and not self._filtered:
            hint.update(f'↵ to create new: "{event.value}"')
            hint.display = True
            lv.display = False
        else:
            hint.display = False
            lv.display = True

    @on(Input.Submitted, '#filter-input')
    def input_submitted(self) -> None:
        inp = self.query_one('#filter-input', Input)
        if self._allow_new and inp.value and not self._filtered:
            self.dismiss(inp.value)
            return
        self._select_current()

    @on(ListView.Selected, '#select-list')
    def item_selected(self) -> None:
        self._select_current()

    def on_key(self, event: Key) -> None:
        lv = self.query_one('#select-list', ListView)
        inp = self.query_one('#filter-input', Input)
        nav_keys = ('j', 'k')
        browsing = lv.has_focus and event.is_printable
        if browsing and event.character not in nav_keys:
            inp.focus()
            inp.value += event.character
            inp.cursor_position = len(inp.value)
            event.stop()

    def _select_current(self) -> None:
        lv = self.query_one('#select-list', ListView)
        idx = lv.index
        if idx is not None and idx < len(self._filtered):
            self.dismiss(self._filtered[idx])

    def action_cursor_down(self) -> None:
        self.query_one('#select-list', ListView).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one('#select-list', ListView).action_cursor_up()

    def action_toggle_focus(self) -> None:
        lv = self.query_one('#select-list', ListView)
        inp = self.query_one('#filter-input', Input)
        if inp.has_focus:
            lv.focus()
        else:
            inp.focus()

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfirmModal(ModalScreen[bool]):
    """Yes/no confirmation modal."""

    DEFAULT_CSS = (
        _MODAL_CSS
        + """
    ConfirmModal { align: center middle; }
    ConfirmModal .modal-box { width: 60; border: solid $warning; }
    """
    )

    BINDINGS: ClassVar[list[Binding]] = [
        Binding('escape', 'cancel', 'Cancel'),
        Binding('y', 'yes', show=False),
        Binding('n', 'cancel', show=False),
    ]

    def __init__(self, question: str) -> None:
        super().__init__()
        self._question = question

    def compose(self) -> ComposeResult:
        with Vertical(classes='modal-box'):
            yield Label(self._question, classes='modal-title')
            with Horizontal(classes='modal-buttons'):
                yield Button('Yes (y)', variant='warning', id='yes')
                yield Button('No (n/esc)', variant='primary', id='no')

    def on_mount(self) -> None:
        self.query_one('#no', Button).focus()

    def action_yes(self) -> None:
        self.dismiss(result=True)

    @on(Button.Pressed, '#yes')
    def yes(self) -> None:
        self.dismiss(result=True)

    @on(Button.Pressed, '#no')
    def action_cancel(self) -> None:
        self.dismiss(result=False)


class DetailPane(ScrollableContainer):
    """Scrollable detail pane — focusable for keyboard scrolling."""

    can_focus = True

    def on_key(self, event: Key) -> None:
        if event.key == 'j':
            event.stop()
            self.scroll_relative(y=3, animate=False)
        elif event.key == 'k':
            event.stop()
            self.scroll_relative(y=-3, animate=False)
        elif event.key == 'G':
            event.stop()
            self.scroll_end(animate=False)
        elif event.key == 'g':
            event.stop()
            self.scroll_home(animate=False)


class VimListView(ListView):
    """ListView with j/k/G/g vim-style navigation.

    Pressing k at the top posts FocusTabBar so the parent can send focus
    to the tab bar; j is handled normally by ListView.
    """

    class FocusTabBar(Message):
        """Posted when k is pressed at the top of the list."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding('ctrl+p', 'cursor_up', show=False),
        Binding('j', 'cursor_down', show=False),
        Binding('k', 'cursor_up_or_tabs', show=False),
        Binding('up', 'cursor_up_or_tabs', show=False),
        Binding('G', 'scroll_end', show=False),
        Binding('g', 'scroll_home', show=False),
    ]

    def action_cursor_up_or_tabs(self) -> None:
        first_enabled = next(
            (i for i, child in enumerate(self._nodes) if not child.disabled),
            0,
        )
        if self.index is None or self.index <= first_enabled:
            self.post_message(self.FocusTabBar())
        else:
            self.action_cursor_up()
