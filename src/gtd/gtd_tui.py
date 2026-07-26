"""Unified GTD TUI."""

from __future__ import annotations

import asyncio
import contextlib
import os
import random
import subprocess
import tempfile
from datetime import datetime, timedelta
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from textual.events import Key
    from collections.abc import Callable

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.command import Hit, Hits, Provider
from textual.containers import ScrollableContainer, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Footer,
    Header,
    Label,
    ListItem,
    ListView,
    LoadingIndicator,
    Static,
    TabbedContent,
    TabPane,
    Tabs,
    TextArea,
)
from textual.widgets._footer import FooterKey, FooterLabel

from gtd.notion.models import ProjectEntry
from gtd.notion.schema import STATUSES, STATUS_ICONS
from gtd.tui import (
    ConfirmModal,
    InputModal,
    SelectModal,
    TwoFieldModal,
    VimListView,
)


class SplitFooter(Footer):
    """Footer with a separator between contextual and global bindings."""

    def compose(self) -> ComposeResult:
        if not self._bindings_ready:  # type: ignore[attr-defined]
            return

        # Contextual: active bindings NOT owned by the app
        contextual = [
            (a.binding, a.enabled, a.tooltip)
            for a in self.screen.active_bindings.values()
            if a.binding.show and a.node is not self.app
        ]

        # Global: always sourced directly from app BINDINGS (never overridden)
        global_b = [
            (b, True, '')
            for b in self.app.BINDINGS
            if isinstance(b, Binding) and b.show
        ]

        seen: set[str] = set()
        for binding, enabled, tooltip in contextual:
            if binding.action in seen:
                continue
            seen.add(binding.action)
            yield FooterKey(
                binding.key,
                self.app.get_key_display(binding),
                binding.description,
                binding.action,
                disabled=not enabled,
                tooltip=tooltip or binding.description,
            ).data_bind(compact=Footer.compact)

        if contextual and global_b:
            yield FooterLabel(' ─── ')

        for binding, enabled, tooltip in global_b:
            yield FooterKey(
                binding.key,
                self.app.get_key_display(binding),
                binding.description,
                binding.action,
                disabled=not enabled,
                tooltip=tooltip or binding.description,
            ).data_bind(compact=Footer.compact)


# ── Entry renderers ──────────────────────────────────────────────────────────


def _render_steps_block(steps: list[str]) -> list[str]:
    lines = [f'  [dim]{"Steps:".ljust(12)}[/dim]']
    for i, step in enumerate(steps):
        if i == 0:
            lines.append(f'    [cyan]→[/cyan] {step}')
        else:
            lines.append(f'    [dim]  {i + 1}. {step}[/dim]')
    return lines


def _render_entry_detail(entry: ProjectEntry, notes: str | None = None) -> str:  # noqa: C901, PLR0912
    icon = STATUS_ICONS.get(entry.status, '·')
    lines = [
        f'{icon} [bold cyan]{entry.header.strip()}[/bold cyan]',
        '',
    ]

    def row(label: str, value: str, color: str = '') -> str:
        padded = f'{label}:'.ljust(12)
        if not value:
            return f'  [dim]{padded} (none)[/dim]'
        val = f'[{color}]{value}[/{color}]' if color else value
        return f'  [dim]{padded}[/dim] {val}'

    lines.append(row('Status', entry.status))
    lines.append(row('Context', entry.context))
    steps = entry.steps
    if len(steps) > 1:
        lines.extend(_render_steps_block(steps))
    else:
        lines.append(row('Next Step', entry.current_step))
    lines.append(row('Success', entry.success_condition))
    if entry.due_date:
        lines.append(row('Due', entry.due_date, 'yellow'))
    if entry.follow_up_date:
        lines.append(row('Follow-up', entry.follow_up_date, 'cyan'))
    if entry.created_date:
        try:
            created = datetime.fromisoformat(
                entry.created_date.replace('Z', '+00:00')
            )
            lines.append(row('Created', created.strftime('%Y-%m-%d'), 'dim'))
        except ValueError:
            pass
    if entry.updated_date:
        try:
            updated = datetime.fromisoformat(
                entry.updated_date.replace('Z', '+00:00')
            )
            updated_str = updated.strftime('%Y-%m-%d')
            created_str = (
                datetime.fromisoformat(
                    entry.created_date.replace('Z', '+00:00')
                ).strftime('%Y-%m-%d')
                if entry.created_date
                else None
            )
            if updated_str != created_str:
                lines.append(row('Updated', updated_str, 'dim'))
        except ValueError:
            pass

    lines.append('')
    if notes is None:
        lines.append('[dim]Loading notes...[/dim]')
    elif notes.strip():
        lines.append('[dim]── Notes ──[/dim]')
        for line in notes.split('\n'):
            lines.append(f'  {line}' if line.strip() else '')
    else:
        lines.append('[dim]No notes.[/dim]')

    return '\n'.join(lines)


def _render_entry_summary(entry: ProjectEntry) -> str:
    icon = STATUS_ICONS.get(entry.status, '·')
    ctx = f'  [dim][{entry.context}][/dim]' if entry.context else ''
    due = ''
    if entry.due_date:
        try:
            d = datetime.fromisoformat(entry.due_date)
            due = f'  [yellow]{d:%b %-d}[/yellow]'
        except ValueError:
            due = f'  [yellow]{entry.due_date}[/yellow]'
    next_step = (
        f'\n  [dim]→ {entry.current_step}[/dim]' if entry.current_step else ''
    )
    return f'{icon} {entry.header.strip()}{ctx}{due}{next_step}'


# ── Weekly habit helpers ─────────────────────────────────────────────────────

WEEKLY_HABITS: list[tuple[str, str]] = [
    ('weekly_review', 'Weekly Review'),
]

_GTD_REVIEW_STEPS: list[tuple[str, str]] = [
    ('Process Inbox', 'triage'),
    ('Review Projects', 'projects'),
    ('Review Waiting For', 'waiting'),
    ('Review Someday/Maybe', 'someday'),
    ('Review Horizons of Focus', 'areas'),
    ('Review Calendar (Past & Upcoming)', 'manual'),
    ("Plan Next Week's Priorities", 'manual'),
]

_GTD_REVIEW_CHECKLIST = '\n'.join(
    f'  □ {label}' for label, _ in _GTD_REVIEW_STEPS
)


def _week_start_iso() -> str:
    today = datetime.now().date()
    return (today - timedelta(days=today.weekday())).isoformat()


def _habit_done_this_week(key: str) -> bool:
    from gtd.storage import get_weekly_habit_date

    last = get_weekly_habit_date(key)
    if not last:
        return False
    return last >= _week_start_iso()


def _render_habit_detail(key: str, label: str) -> str:
    from gtd.storage import get_weekly_habit_date

    last = get_weekly_habit_date(key)
    if last:
        try:
            d = datetime.fromisoformat(last)
            days_ago = (datetime.now().date() - d.date()).days
            last_str = (
                'today' if days_ago == 0 else f'{d:%b %-d} ({days_ago}d ago)'
            )
        except ValueError:
            last_str = last
    else:
        last_str = 'never'

    done = _habit_done_this_week(key)
    if done:
        status = '[green]✓ Done this week[/green]'
    else:
        status = '[bold red]⚠ Not done this week[/bold red]'

    lines = [
        f'[bold red]● {label}[/bold red]'
        if not done
        else f'[dim]✓ {label}[/dim]',
        '',
        f'{status}   [dim]last: {last_str}[/dim]',
        '',
    ]

    if key == 'weekly_review':
        lines += [
            '[dim]── GTD Weekly Review checklist ──[/dim]',
            _GTD_REVIEW_CHECKLIST,
        ]

    if not done:
        lines += [
            '',
            '[dim]Press W to plan your week and mark done.[/dim]',
        ]

    return '\n'.join(lines)


# ── Celebration ─────────────────────────────────────────────────────────────

_CELEBRATION_FRAMES = [
    '🎉  ✨  🏆  ✨  🎉',
    '✨  🎊  🥳  🎊  ✨',
    '🏆  🎉  ✨  🎉  🏆',
    '🎊  🥳  🎉  🥳  🎊',
]

_CELEBRATION_MESSAGES = [
    'CRUSHED IT',
    'DONE AND DONE',
    'NAILED IT',
    'GET IN',
    'SHIPPED',
    'BOOM',
    'KILLED IT',
    'OBLITERATED',
]


class CelebrationScreen(ModalScreen):
    DEFAULT_CSS = """
    CelebrationScreen { align: center middle; }
    CelebrationScreen .cel-box {
        border: double $success;
        padding: 1 4;
        width: 52;
        height: auto;
        background: $surface;
    }
    CelebrationScreen .cel-emoji {
        text-align: center;
        width: 1fr;
        margin-bottom: 1;
    }
    CelebrationScreen .cel-title {
        text-align: center;
        text-style: bold;
        color: $success;
        width: 1fr;
    }
    CelebrationScreen .cel-header {
        text-align: center;
        color: $text-muted;
        width: 1fr;
        margin-top: 1;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding('escape,enter,space', 'dismiss_cel', show=False),
    ]

    def __init__(self, header: str) -> None:
        super().__init__()
        self._header = header
        self._frame = 0
        self._msg = random.choice(_CELEBRATION_MESSAGES)

    def compose(self) -> ComposeResult:
        with Vertical(classes='cel-box'):
            yield Static('', classes='cel-emoji', id='cel-emoji', markup=True)
            yield Static(self._msg, classes='cel-title', markup=False)
            yield Static(
                f'"{self._header}"', classes='cel-header', markup=False
            )

    def on_mount(self) -> None:
        self._tick()
        self.set_interval(0.35, self._tick)
        self.set_timer(2.0, self.action_dismiss_cel)

    def _tick(self) -> None:
        frame = _CELEBRATION_FRAMES[self._frame % len(_CELEBRATION_FRAMES)]
        self.query_one('#cel-emoji', Static).update(frame)
        self._frame += 1

    def action_dismiss_cel(self) -> None:
        self.dismiss()


# ── List item widgets ────────────────────────────────────────────────────────


class EntryListItem(ListItem):
    def __init__(self, entry: ProjectEntry) -> None:
        super().__init__()
        self.page_id = entry.page_id
        icon = STATUS_ICONS.get(entry.status, '·')
        ctx = ''
        self._text = f'{icon} {entry.header.strip()}{ctx}'

    def compose(self) -> ComposeResult:
        yield Label(self._text)


class ListEntryListItem(ListItem):
    """List item showing success_condition (streaming service) inline."""

    def __init__(self, entry: ProjectEntry) -> None:
        super().__init__()
        self.page_id = entry.page_id
        svc = (
            f'  [dim]{entry.success_condition}[/dim]'
            if entry.success_condition
            else ''
        )
        self._text = f'📋 {entry.header.strip()}{svc}'

    def compose(self) -> ComposeResult:
        yield Label(self._text, markup=True)


class WeeklyHabitItem(ListItem):
    def __init__(self, key: str, label: str) -> None:
        super().__init__()
        self.habit_key = key
        self.habit_label = label

    def compose(self) -> ComposeResult:
        yield Label(
            f'[bold red]●[/bold red] {self.habit_label}'
            f'  [dim]not done this week[/dim]',
            markup=True,
        )


class SeparatorListItem(ListItem):
    def __init__(self, label: str) -> None:
        super().__init__(disabled=True)
        self._label = label

    @property
    def raw_label(self) -> str:
        return self._label

    def compose(self) -> ComposeResult:
        yield Label(f'[dim]── {self._label} ──[/dim]', markup=True)


class NextStepListItem(EntryListItem):
    """List item showing next step as primary, project name as secondary."""

    def __init__(self, entry: ProjectEntry) -> None:
        super().__init__(entry)
        step = entry.current_step
        project = entry.header.strip()
        due = ''
        if entry.due_date:
            with contextlib.suppress(ValueError):
                d = datetime.fromisoformat(entry.due_date)
                due = f'  [yellow]{d:%b %-d}[/yellow]'
        if step:
            self._text = f'[cyan]→[/cyan] {step}\n  [dim]{project}{due}[/dim]'
        else:
            self._text = f'[dim](no step)[/dim]\n  [dim]{project}{due}[/dim]'

    def compose(self) -> ComposeResult:
        yield Label(self._text, markup=True)


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


async def _prompt_and_get_props(
    app: App,
    entry: ProjectEntry,
    choice: str,
) -> dict | None:
    """Prompt for a field value; return kwargs for build_property_update."""
    from gtd.notion.client import get_select_options
    from gtd.notion.entries import _parse_date_input

    loop = asyncio.get_running_loop()
    props: dict | None = None

    if choice == 'Name':
        value = await app.push_screen_wait(
            InputModal('Update Name', initial=entry.header.strip())
        )
        props = {'name': value} if value else None
    elif choice == 'Status':
        value = await app.push_screen_wait(SelectModal('Status', STATUSES))
        props = {'status': value} if value else None
    elif choice == 'Context':
        contexts = await loop.run_in_executor(
            None, get_select_options, 'Context'
        )
        value = await app.push_screen_wait(
            SelectModal('Context', sorted(contexts), allow_new=True)
        )
        props = {'context': value} if value else None
    elif choice == 'Steps':
        value = await _open_steps_editor(app, initial_text=entry.next_step)
        props = {'next_step': value}
    elif choice == 'Success condition':
        value = await app.push_screen_wait(
            InputModal(
                'Success Condition',
                initial=entry.success_condition,
            )
        )
        props = {'success_condition': value} if value is not None else None
    elif choice in ('Follow-up date', 'Due date'):
        value = await app.push_screen_wait(
            InputModal(choice, 'e.g. Monday, Jul 15, blank to clear')
        )
        if value is not None:
            date = _parse_date_input(value) if value else ''
            key = (
                'follow_up_date' if choice == 'Follow-up date' else 'due_date'
            )
            props = {key: date}

    return props


async def _shared_update_entry(
    app: App,
    entry: ProjectEntry,
    refresh_cb: Callable[[], None],
) -> None:
    """Update entry fields — shared across tab widgets."""
    from gtd.notion.client import build_property_update, update_page

    fields = [
        'Name',
        'Status',
        'Context',
        'Steps',
        'Success condition',
        'Follow-up date',
        'Due date',
    ]
    choice = await app.push_screen_wait(
        SelectModal('Update which field?', fields)
    )
    if not choice:
        return

    props = await _prompt_and_get_props(app, entry, choice)
    if props is None:
        return

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None, update_page, entry.page_id, build_property_update(**props)
    )
    refresh_cb()
    app.notify(f'✓ "{entry.header.strip()}" updated')


async def _open_steps_editor(app: App, initial_text: str = '') -> str:
    """Suspend the TUI and open $EDITOR to edit the steps queue.

    Returns the cleaned text (empty string if cleared). No cancel detection
    — quitting the editor without saving preserves the original content.
    """
    instructions = (
        '# Enter steps, one per line. Numbering is optional.\n'
        '# Example:\n'
        '#   1. Call the vendor about contract\n'
        '#   2. Send signed agreement\n'
        '#   3. Follow up in two weeks\n'
        '# Lines starting with # are ignored.\n\n'
    )
    fd, tmp_path = tempfile.mkstemp(suffix='.md')
    with open(fd, 'w') as f:  # noqa: PTH123
        f.write(instructions + initial_text)
    editor = os.environ.get('EDITOR', 'nvim')
    with app.suspend():
        subprocess.run([editor, '+', tmp_path], check=False)  # noqa: S603
    content = Path(tmp_path).read_text()
    Path(tmp_path).unlink(missing_ok=True)
    lines = [ln for ln in content.split('\n') if not ln.startswith('#')]
    from gtd.notion.models import format_steps, parse_steps

    steps = parse_steps('\n'.join(lines))
    return format_steps(steps)


class EditNotesModal(ModalScreen[str | None]):
    """In-TUI notes editor — no terminal takeover."""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding('ctrl+s', 'save', 'Save'),
        Binding('escape', 'cancel', 'Cancel'),
    ]

    DEFAULT_CSS = """
    EditNotesModal { align: center middle; }
    EditNotesModal .enm-box {
        width: 90%;
        height: 90%;
        border: solid $accent;
        background: $surface;
    }
    EditNotesModal .enm-title {
        background: $panel;
        padding: 0 1;
        height: 1;
        text-style: bold;
    }
    EditNotesModal TextArea { height: 1fr; }
    EditNotesModal .enm-footer {
        background: $panel;
        padding: 0 1;
        height: 1;
    }
    """

    def __init__(self, title: str, initial: str = '') -> None:
        super().__init__()
        self._title = title
        self._initial = initial

    def compose(self) -> ComposeResult:
        with Vertical(classes='enm-box'):
            yield Static(self._title, classes='enm-title')
            yield TextArea(
                self._initial,
                language='markdown',
                id='enm-editor',
            )
            yield Static(
                '[dim]ctrl+s  save · esc  cancel[/dim]',
                classes='enm-footer',
                markup=True,
            )

    def on_mount(self) -> None:
        self.query_one('#enm-editor', TextArea).focus()

    def action_save(self) -> None:
        self.dismiss(self.query_one('#enm-editor', TextArea).text)

    def action_cancel(self) -> None:
        self.dismiss(None)


def _use_inline_editor() -> bool:
    from gtd.notion.config import get_config_value

    return get_config_value('notes_editor') == 'inline'


async def _get_edited_body(app: App, title: str, body: str) -> str | None:
    """Return edited body via inline modal or external $EDITOR per config."""
    if _use_inline_editor():
        return await app.push_screen_wait(EditNotesModal(title, initial=body))
    fd, tmp_path = tempfile.mkstemp(suffix='.md')
    with open(fd, 'w') as f:  # noqa: PTH123
        f.write(body)
    editor = os.environ.get('EDITOR', 'nvim')
    with app.suspend():
        subprocess.run([editor, tmp_path], check=False)  # noqa: S603
    new_body = Path(tmp_path).read_text()
    Path(tmp_path).unlink(missing_ok=True)
    return new_body


async def _shared_edit_notes(
    app: App,
    entry: ProjectEntry,
    notes_cache: dict[str, str],
    refresh_cb: Callable[[], None],
) -> None:
    """Edit notes via inline modal or external $EDITOR per config."""
    from gtd.notion.client import get_page_body, replace_page_body

    loop = asyncio.get_running_loop()
    body = await loop.run_in_executor(None, get_page_body, entry.page_id)

    new_body = await _get_edited_body(app, entry.header.strip(), body)
    if new_body is None or new_body == body:
        return
    await loop.run_in_executor(
        None, replace_page_body, entry.page_id, new_body
    )
    notes_cache[entry.page_id] = new_body
    app.notify('Notes saved')
    refresh_cb()


async def _shared_log_and_reschedule(
    app: App,
    entry: ProjectEntry,
    notes_cache: dict[str, str],
) -> str | None:
    """Edit notes, update context, then prompt/infer next follow-up.

    Returns the new follow-up date string, or None if cancelled.
    """
    from gtd.notion.client import (
        get_page_body,
        get_select_options,
        replace_page_body,
    )

    loop = asyncio.get_running_loop()
    try:
        body = await loop.run_in_executor(None, get_page_body, entry.page_id)
    except Exception as e:
        app.notify(f'Error: {e}', severity='error')
        return None

    new_body = await _get_edited_body(app, entry.header.strip(), body)
    if new_body is None:
        return None
    if new_body != body:
        await loop.run_in_executor(
            None, replace_page_body, entry.page_id, new_body
        )
        notes_cache[entry.page_id] = new_body
        app.notify('Notes saved')

    # Prompt to update context for next step
    contexts = await loop.run_in_executor(None, get_select_options, 'Context')
    new_context = await app.push_screen_wait(
        SelectModal(
            f'Context for next step? ({entry.context or "none"})',
            sorted(contexts),
            allow_new=True,
        )
    )

    from gtd.notion.log import _infer_reschedule_days

    inferred = _infer_reschedule_days(entry.header)
    if inferred:
        next_date = (datetime.now() + timedelta(days=inferred)).strftime(
            '%Y-%m-%d'
        )
    else:
        date_str = await app.push_screen_wait(
            InputModal('Reschedule to', 'e.g. tomorrow, Monday, Jul 15')
        )
        if not date_str:
            return None
        from gtd.notion.entries import _parse_date_input

        next_date = _parse_date_input(date_str)
        if not next_date:
            app.notify('Could not parse date', severity='warning')
            return None

    from gtd.notion.client import build_property_update, update_page

    props = build_property_update(follow_up_date=next_date)
    if new_context:
        props = {**props, **build_property_update(context=new_context)}

    await loop.run_in_executor(
        None,
        update_page,
        entry.page_id,
        props,
    )
    return next_date


# ── Base entry content ───────────────────────────────────────────────────────


class BaseEntryContent(Vertical):
    """Shared two-pane infrastructure for all entry tab widgets."""

    TITLE: ClassVar[str] = ''
    EMPTY_MSG: ClassVar[str] = 'Nothing here.'

    BINDINGS: ClassVar[list[Binding]] = [
        Binding('X', 'complete_step', 'Complete Step', show=True),
    ]

    DEFAULT_CSS = """
    BaseEntryContent { layout: horizontal; height: 1fr; }
    BaseEntryContent #entry-list-pane {
        width: 40%;
        border-right: solid $panel;
    }
    BaseEntryContent #entry-list-header {
        background: $panel;
        padding: 0 1;
        height: 1;
        text-style: bold;
    }
    BaseEntryContent #entry-list { height: 1fr; }
    BaseEntryContent #entry-detail-pane {
        width: 1fr;
        padding: 1 2;
        border: solid transparent;
    }
    BaseEntryContent #entry-detail-pane:focus { border: solid $accent; }
    BaseEntryContent LoadingIndicator { height: 3; }
    """

    def __init__(self) -> None:
        super().__init__()
        self._entries: list[ProjectEntry] = []
        self._notes: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        with Vertical(id='entry-list-pane'):
            yield Static(self.TITLE, id='entry-list-header')
            yield LoadingIndicator(id='entry-loading')
            yield VimListView(id='entry-list')
        with DetailPane(id='entry-detail-pane'):
            yield Static('', id='entry-detail', markup=True)

    def on_mount(self) -> None:
        self._load_entries()

    def _build_filter(self) -> dict:
        raise NotImplementedError

    @work(thread=True)
    def _load_entries(self) -> None:
        from gtd.notion.client import query_database

        try:
            pages = query_database(filter_obj=self._build_filter())
            entries = [ProjectEntry.from_page(p) for p in pages]
            self.app.call_from_thread(self._set_entries, entries)
        except Exception as e:
            msg = f'Notion error: {e}'
            self.app.call_from_thread(
                lambda: self.app.notify(msg, severity='error')
            )
            self.app.call_from_thread(self._set_entries, [])

    def _set_entries(self, entries: list[ProjectEntry]) -> None:
        self._entries = entries
        with contextlib.suppress(Exception):
            self.query_one('#entry-loading', LoadingIndicator).display = False
        lv = self.query_one('#entry-list', VimListView)
        lv.clear()
        for entry in entries:
            lv.append(EntryListItem(entry))
        header = self.query_one('#entry-list-header', Static)
        detail = self.query_one('#entry-detail', Static)
        if not entries:
            header.update(f'{self.TITLE} — empty')
            detail.update(f'[dim]{self.EMPTY_MSG}[/dim]')
        else:
            header.update(f'{self.TITLE}  [dim]({len(entries)})[/dim]')
            lv.index = 0
            self._update_detail()

    @on(ListView.Highlighted, '#entry-list')
    def on_list_highlighted(self) -> None:
        self._update_detail()

    def _current_entry(self) -> ProjectEntry | None:
        idx = self.query_one('#entry-list', VimListView).index
        if idx is None or idx >= len(self._entries):
            return None
        return self._entries[idx]

    def _update_detail(self) -> None:
        entry = self._current_entry()
        if not entry:
            return
        notes = self._notes.get(entry.page_id)
        self.query_one('#entry-detail', Static).update(
            _render_entry_detail(entry, notes)
        )
        if entry.page_id not in self._notes:
            self._load_notes(entry.page_id)

    @work(thread=True, exclusive=True)
    def _load_notes(self, page_id: str) -> None:
        from gtd.notion.client import get_page_body

        try:
            body = get_page_body(page_id)
        except Exception:
            body = ''
        self._notes[page_id] = body
        self.app.call_from_thread(self._maybe_refresh_detail, page_id)

    def _maybe_refresh_detail(self, page_id: str) -> None:
        entry = self._current_entry()
        if entry and entry.page_id == page_id:
            self._update_detail()

    def select_entry(self, page_id: str) -> bool:
        """Highlight and focus the entry. Returns True if found."""
        lv = self.query_one('#entry-list', VimListView)
        for idx, child in enumerate(lv.children):
            if isinstance(child, EntryListItem) and child.page_id == page_id:
                lv.index = idx
                lv.focus()
                return True
        return False

    def _remove_entry(self, page_id: str) -> None:
        self._entries = [e for e in self._entries if e.page_id != page_id]
        lv = self.query_one('#entry-list', VimListView)
        for child in lv.query(EntryListItem):
            if child.page_id == page_id:
                child.remove()
                break
        header = self.query_one('#entry-list-header', Static)
        detail = self.query_one('#entry-detail', Static)
        if not self._entries:
            header.update(f'{self.TITLE} — empty')
            detail.update(f'[dim]{self.EMPTY_MSG}[/dim]')
        else:
            header.update(f'{self.TITLE}  [dim]({len(self._entries)})[/dim]')
        self._update_detail()

    def _remove_entry_and_refocus(self, page_id: str) -> None:
        """Remove entry and refocus list. Use from actions, not triage."""
        self._remove_entry(page_id)
        self.query_one('#entry-list', VimListView).focus()

    def action_refresh(self) -> None:
        self._entries = []
        self._notes = {}
        with contextlib.suppress(Exception):
            self.query_one('#entry-loading', LoadingIndicator).display = True
        self.query_one('#entry-list', VimListView).clear()
        self.query_one('#entry-detail', Static).update('')
        self._load_entries()

    @work
    async def action_update_entry(self) -> None:
        entry = self._current_entry()
        if entry:
            await _shared_update_entry(self.app, entry, self._load_entries)

    @work
    async def action_edit_notes(self) -> None:
        entry = self._current_entry()
        if entry:
            await _shared_edit_notes(
                self.app, entry, self._notes, self._update_detail
            )

    @work
    async def action_mark_done(self) -> None:
        entry = self._current_entry()
        if not entry:
            return
        from gtd.notion.log import _is_recurring

        if _is_recurring(entry):
            choice = await self.app.push_screen_wait(
                SelectModal(
                    f'⚠ "{entry.header.strip()}" is recurring',
                    ['Reschedule', 'Permanently complete'],
                )
            )
            if choice == 'Reschedule':
                next_date = await _shared_log_and_reschedule(
                    self.app, entry, self._notes
                )
                if next_date:
                    self._remove_entry_and_refocus(entry.page_id)
                    self.app.notify(
                        f'✓ "{entry.header.strip()}" → {next_date}'
                    )
                return
            if choice != 'Permanently complete':
                return
            confirmed = await self.app.push_screen_wait(
                ConfirmModal(
                    f'⚠ Permanently complete "{entry.header.strip()}"?'
                )
            )
        else:
            confirmed = await self.app.push_screen_wait(
                ConfirmModal(f'Mark done: "{entry.header.strip()}"?')
            )
        if not confirmed:
            return
        self._done_worker(entry.page_id)
        self._remove_entry_and_refocus(entry.page_id)
        await self.app.push_screen_wait(
            CelebrationScreen(entry.header.strip())
        )

    @work(thread=True)
    def _done_worker(self, page_id: str) -> None:
        from gtd.notion.client import NotionAPIError, archive_page

        with contextlib.suppress(NotionAPIError):
            archive_page(page_id)

    @work(thread=True)
    def _update_worker(self, page_id: str, props: dict) -> None:
        from gtd.notion.client import (
            NotionAPIError,
            build_property_update,
            update_page,
        )

        with contextlib.suppress(NotionAPIError):
            update_page(page_id, build_property_update(**props))

    @work
    async def action_log_and_reschedule(self) -> None:
        entry = self._current_entry()
        if not entry:
            return
        next_date = await _shared_log_and_reschedule(
            self.app, entry, self._notes
        )
        if next_date:
            self._update_detail()
            self.app.notify(f'✓ "{entry.header.strip()}" → {next_date}')

    @work
    async def action_drop_entry(self) -> None:
        entry = self._current_entry()
        if not entry:
            return
        confirmed = await self.app.push_screen_wait(
            ConfirmModal(f'Drop: "{entry.header.strip()}"?')
        )
        if not confirmed:
            return
        self._drop_worker(entry.page_id)
        self._remove_entry_and_refocus(entry.page_id)
        self.app.notify(f'✓ Dropped "{entry.header.strip()}"')

    @work(thread=True)
    def _drop_worker(self, page_id: str) -> None:
        from gtd.notion.client import NotionAPIError, archive_page

        with contextlib.suppress(NotionAPIError):
            archive_page(page_id)

    @work
    async def action_complete_step(self) -> None:
        from gtd.notion.models import advance_steps

        entry = self._current_entry()
        if not entry or not entry.next_step:
            return
        steps = entry.steps
        if len(steps) <= 1:
            confirmed = await self.app.push_screen_wait(
                ConfirmModal(f'Complete final step: "{entry.current_step}"?')
            )
            if not confirmed:
                return
        new_text = advance_steps(entry.next_step)
        self._advance_step_worker(entry.page_id, new_text)
        entry.next_step = new_text
        self._update_detail()
        remaining = len(entry.steps)
        if remaining:
            self.app.notify(f'✓ Step done — {remaining} remaining')
        else:
            self.app.notify('✓ All steps complete!')

    @work(thread=True)
    def _advance_step_worker(self, page_id: str, new_text: str) -> None:
        from gtd.notion.client import build_property_update, update_page

        update_page(page_id, build_property_update(next_step=new_text))

    async def action_activate(self) -> None:
        entry = self._current_entry()
        if not entry:
            return
        self._activate_worker(entry.page_id)
        self._remove_entry_and_refocus(entry.page_id)
        self.app.notify(f'✓ "{entry.header.strip()}" → Current Project')

    @work(thread=True)
    def _activate_worker(self, page_id: str) -> None:
        from gtd.notion.client import build_property_update, update_page

        update_page(page_id, build_property_update(status='Current Project'))

    async def action_move_someday(self) -> None:
        entry = self._current_entry()
        if not entry:
            return
        self._someday_worker(entry.page_id)
        self._remove_entry_and_refocus(entry.page_id)
        self.app.notify(f'✓ "{entry.header.strip()}" → Someday/Maybe')

    @work(thread=True)
    def _someday_worker(self, page_id: str) -> None:
        from gtd.notion.client import build_property_update, update_page

        update_page(page_id, build_property_update(status='Someday/Maybe'))

    @work
    async def action_capture(self) -> None:
        header = await self.app.push_screen_wait(
            InputModal('Capture to Inbox', 'What needs capturing?')
        )
        if not header:
            return
        self._capture_worker(header)
        self.app.notify(f'✓ Captured: "{header}" → Triage')

    @work(thread=True)
    def _capture_worker(self, header: str) -> None:
        from gtd.notion.capture import _create_page

        _create_page(header)


# ── Weekly Review Screen ─────────────────────────────────────────────────────


class ProjectsBrowseScreen(ModalScreen):
    """Browse Current Projects — peruse, optionally act."""

    DEFAULT_CSS = """
    ProjectsBrowseScreen { align: center middle; }
    ProjectsBrowseScreen .modal-box {
        border: solid $accent;
        padding: 1 2;
        width: 72;
        height: auto;
        max-height: 36;
    }
    ProjectsBrowseScreen .sb-title {
        text-style: bold;
        margin-bottom: 1;
        width: 1fr;
    }
    ProjectsBrowseScreen VimListView { height: auto; max-height: 24; }
    ProjectsBrowseScreen .sb-footer { margin-top: 1; color: $text-muted; }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding('escape', 'done', 'Done', show=True),
        Binding('d', 'mark_done', 'Done', show=True),
        Binding('s', 'someday', 'Someday', show=True),
        Binding('e', 'edit_steps', 'Edit Steps', show=True),
        Binding('j', 'cursor_down', show=False),
        Binding('k', 'cursor_up', show=False),
    ]

    def __init__(self, entries: list[ProjectEntry]) -> None:
        super().__init__()
        self._entries = list(entries)
        self._to_done: list[ProjectEntry] = []
        self._to_someday: list[ProjectEntry] = []
        self._step_updates: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        n = len(self._entries)
        s = 's' if n != 1 else ''
        with Vertical(classes='modal-box'):
            yield Static(
                f'Current Projects  [dim]({n} item{s})[/dim]',
                classes='sb-title',
                markup=True,
            )
            yield VimListView(id='sb-list')
            yield Static(
                '[dim]j/k · d: done · s: someday · e: steps · esc: done[/dim]',
                classes='sb-footer',
                markup=True,
            )

    def on_mount(self) -> None:
        lv = self.query_one('#sb-list', VimListView)
        for entry in self._entries:
            lv.append(EntryListItem(entry))
        lv.focus()

    def _current_entry(self) -> ProjectEntry | None:
        lv = self.query_one('#sb-list', VimListView)
        idx = lv.index
        if idx is None or idx >= len(self._entries):
            return None
        return self._entries[idx]

    def _remove_current(self) -> None:
        lv = self.query_one('#sb-list', VimListView)
        idx = lv.index
        if idx is None:
            return
        self._entries.pop(idx)
        child = lv.highlighted_child
        if child:
            child.remove()
        n = len(self._entries)
        s = 's' if n != 1 else ''
        self.query_one('.sb-title', Static).update(
            f'Current Projects  [dim]({n} item{s})[/dim]'
        )
        if not self._entries:
            self.dismiss((self._to_done, self._to_someday, self._step_updates))

    @work
    async def action_mark_done(self) -> None:
        entry = self._current_entry()
        if not entry:
            return
        confirmed = await self.app.push_screen_wait(
            ConfirmModal(f'Mark done: "{entry.header.strip()}"?')
        )
        if confirmed:
            self._to_done.append(entry)
            self._remove_current()

    @work
    async def action_someday(self) -> None:
        entry = self._current_entry()
        if not entry:
            return
        confirmed = await self.app.push_screen_wait(
            ConfirmModal(f'Move to Someday: "{entry.header.strip()}"?')
        )
        if confirmed:
            self._to_someday.append(entry)
            self._remove_current()

    @work
    async def action_edit_steps(self) -> None:
        entry = self._current_entry()
        if not entry:
            return
        val = await _open_steps_editor(
            self.app, initial_text=entry.next_step or ''
        )
        if val is not None:
            self._step_updates[entry.page_id] = val
            self.app.notify(f'Steps updated: {entry.header.strip()[:40]}')

    def action_done(self) -> None:
        self.dismiss((self._to_done, self._to_someday, self._step_updates))

    def action_cursor_down(self) -> None:
        self.query_one('#sb-list', VimListView).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one('#sb-list', VimListView).action_cursor_up()


async def _review_projects(app: App) -> int:
    """Browse Current Projects. Returns count."""
    from gtd.notion.client import (
        archive_page,
        build_property_update,
        query_database,
        update_page,
    )

    loop = asyncio.get_running_loop()
    pages = await loop.run_in_executor(
        None,
        lambda: query_database(
            filter_obj={
                'property': 'Status',
                'select': {'equals': 'Current Project'},
            }
        ),
    )
    entries = [ProjectEntry.from_page(p) for p in pages]
    if not entries:
        app.notify('No current projects.')
        return 0

    result = await app.push_screen_wait(ProjectsBrowseScreen(entries))
    if not result:
        return len(entries)

    to_done, to_someday, step_updates = result
    for entry in to_done:
        await loop.run_in_executor(None, archive_page, entry.page_id)
    for entry in to_someday:
        await loop.run_in_executor(
            None,
            update_page,
            entry.page_id,
            build_property_update(status='Someday/Maybe'),
        )
    for page_id, steps in step_updates.items():
        await loop.run_in_executor(
            None,
            update_page,
            page_id,
            build_property_update(next_step=steps),
        )
    return len(entries)


class WaitingForBrowseScreen(ModalScreen):
    """Browse Waiting For items — peruse, optionally act."""

    DEFAULT_CSS = """
    WaitingForBrowseScreen { align: center middle; }
    WaitingForBrowseScreen .modal-box {
        border: solid $accent;
        padding: 1 2;
        width: 72;
        height: auto;
        max-height: 36;
    }
    WaitingForBrowseScreen .sb-title {
        text-style: bold;
        margin-bottom: 1;
        width: 1fr;
    }
    WaitingForBrowseScreen VimListView { height: auto; max-height: 24; }
    WaitingForBrowseScreen .sb-footer { margin-top: 1; color: $text-muted; }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding('escape', 'done', 'Done', show=True),
        Binding('d', 'heard_back', 'Project Done', show=True),
        Binding('s', 'change_status', 'Change Status', show=True),
        Binding('j', 'cursor_down', show=False),
        Binding('k', 'cursor_up', show=False),
    ]

    def __init__(self, entries: list[ProjectEntry]) -> None:
        super().__init__()
        self._entries = list(entries)
        self._to_done: list[ProjectEntry] = []
        self._status_changes: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        n = len(self._entries)
        s = 's' if n != 1 else ''
        with Vertical(classes='modal-box'):
            yield Static(
                f'Waiting For  [dim]({n} item{s})[/dim]',
                classes='sb-title',
                markup=True,
            )
            yield VimListView(id='sb-list')
            yield Static(
                '[dim]j/k · d: project done · s: change status[/dim]',
                classes='sb-footer',
                markup=True,
            )

    def on_mount(self) -> None:
        lv = self.query_one('#sb-list', VimListView)
        for entry in self._entries:
            lv.append(EntryListItem(entry))
        lv.focus()

    def _current_entry(self) -> ProjectEntry | None:
        lv = self.query_one('#sb-list', VimListView)
        idx = lv.index
        if idx is None or idx >= len(self._entries):
            return None
        return self._entries[idx]

    def _remove_current(self) -> None:
        lv = self.query_one('#sb-list', VimListView)
        idx = lv.index
        if idx is None:
            return
        self._entries.pop(idx)
        child = lv.highlighted_child
        if child:
            child.remove()
        n = len(self._entries)
        s = 's' if n != 1 else ''
        self.query_one('.sb-title', Static).update(
            f'Waiting For  [dim]({n} item{s})[/dim]'
        )
        if not self._entries:
            self.dismiss((self._to_done, self._status_changes))

    @work
    async def action_heard_back(self) -> None:
        entry = self._current_entry()
        if not entry:
            return
        confirmed = await self.app.push_screen_wait(
            ConfirmModal(f'Mark "{entry.header.strip()}" as done?')
        )
        if confirmed:
            self._to_done.append(entry)
            self._remove_current()

    @work
    async def action_change_status(self) -> None:
        entry = self._current_entry()
        if not entry:
            return
        options = [s for s in STATUSES if s != 'Waiting For']
        new_status = await self.app.push_screen_wait(
            SelectModal(
                options,
                title='Move to status',
                subtitle=entry.header.strip(),
            )
        )
        if new_status:
            self._status_changes[entry.page_id] = new_status
            self._remove_current()

    @work
    async def action_activate(self) -> None:
        pass  # replaced by action_change_status

    @work
    async def action_follow_up(self) -> None:
        pass  # replaced by action_change_status

    def action_done(self) -> None:
        self.dismiss((self._to_done, self._status_changes))

    def action_cursor_down(self) -> None:
        self.query_one('#sb-list', VimListView).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one('#sb-list', VimListView).action_cursor_up()


async def _review_waiting_for(app: App) -> int:
    """Browse Waiting For items. Returns count."""
    from gtd.notion.client import (
        archive_page,
        build_property_update,
        query_database,
        update_page,
    )

    loop = asyncio.get_running_loop()
    pages = await loop.run_in_executor(
        None,
        lambda: query_database(
            filter_obj={
                'property': 'Status',
                'select': {'equals': 'Waiting For'},
            }
        ),
    )
    entries = [ProjectEntry.from_page(p) for p in pages]
    if not entries:
        app.notify('No Waiting For items.')
        return 0

    result = await app.push_screen_wait(WaitingForBrowseScreen(entries))
    if not result:
        return len(entries)

    to_done, status_changes = result
    for entry in to_done:
        await loop.run_in_executor(None, archive_page, entry.page_id)
    for page_id, new_status in status_changes.items():
        await loop.run_in_executor(
            None,
            update_page,
            page_id,
            build_property_update(status=new_status),
        )
    return len(entries)


async def _review_someday(app: App) -> int:
    """Browse Someday/Maybe items — perusal, not per-item triage."""
    from gtd.notion.client import (
        build_property_update,
        query_database,
        update_page,
    )
    from gtd.notion.client import archive_page

    loop = asyncio.get_running_loop()
    pages = await loop.run_in_executor(
        None,
        lambda: query_database(
            filter_obj={
                'property': 'Status',
                'select': {'equals': 'Someday/Maybe'},
            }
        ),
    )
    entries = [ProjectEntry.from_page(p) for p in pages]
    if not entries:
        app.notify('Someday/Maybe is empty.')
        return 0

    result = await app.push_screen_wait(SomedayBrowseScreen(entries))
    if not result:
        return len(entries)

    to_activate, to_drop = result
    for entry in to_activate:
        await loop.run_in_executor(
            None,
            update_page,
            entry.page_id,
            build_property_update(status='Current Project'),
        )
    for entry in to_drop:
        await loop.run_in_executor(None, archive_page, entry.page_id)
    return len(entries)


class SomedayBrowseScreen(ModalScreen):
    """Browsable Someday/Maybe list — peruse, optionally act."""

    DEFAULT_CSS = """
    SomedayBrowseScreen { align: center middle; }
    SomedayBrowseScreen .modal-box {
        border: solid $accent;
        padding: 1 2;
        width: 72;
        height: auto;
        max-height: 36;
    }
    SomedayBrowseScreen .sb-title {
        text-style: bold;
        margin-bottom: 1;
    }
    SomedayBrowseScreen VimListView { height: auto; max-height: 24; }
    SomedayBrowseScreen .sb-footer {
        margin-top: 1;
        color: $text-muted;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding('escape', 'done', 'Done', show=True),
        Binding('a', 'activate', 'Activate', show=True),
        Binding('d', 'drop', 'Drop', show=True),
        Binding('j', 'cursor_down', show=False),
        Binding('k', 'cursor_up', show=False),
    ]

    def __init__(self, entries: list[ProjectEntry]) -> None:
        super().__init__()
        self._entries = list(entries)
        self._to_activate: list[ProjectEntry] = []
        self._to_drop: list[ProjectEntry] = []

    def compose(self) -> ComposeResult:
        n = len(self._entries)
        s = 's' if n != 1 else ''
        with Vertical(classes='modal-box'):
            yield Static(
                f'Someday/Maybe  [dim]({n} item{s})[/dim]',
                classes='sb-title',
                markup=True,
            )
            yield VimListView(id='sb-list')
            yield Static(
                '[dim]j/k: browse · a: activate · d: drop · esc: done[/dim]',
                classes='sb-footer',
                markup=True,
            )

    def on_mount(self) -> None:
        lv = self.query_one('#sb-list', VimListView)
        for entry in self._entries:
            lv.append(EntryListItem(entry))
        lv.focus()

    def _current_entry(self) -> ProjectEntry | None:
        lv = self.query_one('#sb-list', VimListView)
        idx = lv.index
        if idx is None or idx >= len(self._entries):
            return None
        return self._entries[idx]

    def _remove_current(self) -> None:
        lv = self.query_one('#sb-list', VimListView)
        idx = lv.index
        if idx is None:
            return
        self._entries.pop(idx)
        child = lv.highlighted_child
        if child:
            child.remove()
        header = self.query_one('.sb-title', Static)
        n = len(self._entries)
        s = 's' if n != 1 else ''
        header.update(f'Someday/Maybe  [dim]({n} item{s})[/dim]')
        if not self._entries:
            self.dismiss((self._to_activate, self._to_drop))

    @work
    async def action_activate(self) -> None:
        entry = self._current_entry()
        if not entry:
            return
        confirmed = await self.app.push_screen_wait(
            ConfirmModal(f'Activate "{entry.header.strip()}"?')
        )
        if confirmed:
            self._to_activate.append(entry)
            self._remove_current()
            self.app.notify(f'Will activate: {entry.header.strip()}')

    @work
    async def action_drop(self) -> None:
        entry = self._current_entry()
        if not entry:
            return
        confirmed = await self.app.push_screen_wait(
            ConfirmModal(f'Drop "{entry.header.strip()}"?')
        )
        if confirmed:
            self._to_drop.append(entry)
            self._remove_current()

    def action_done(self) -> None:
        self.dismiss((self._to_activate, self._to_drop))

    def action_cursor_down(self) -> None:
        self.query_one('#sb-list', VimListView).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one('#sb-list', VimListView).action_cursor_up()


async def _review_areas(app: App) -> int:
    """Walk through Horizons of Focus, prompt for inbox captures."""
    from gtd.notion.capture import _create_page
    from gtd.storage import load_areas

    areas = load_areas()
    if not areas:
        app.notify(
            'No horizons defined. Run: gtd areas add "Health"',
            severity='warning',
        )
        return 0

    loop = asyncio.get_running_loop()
    reviewed = 0
    for area in areas:
        name = area['name']
        notes = area.get('notes', '')
        title = f'Area: {name}' + (f'\n[dim]{notes}[/dim]' if notes else '')
        while True:
            action = await app.push_screen_wait(
                SelectModal(
                    title,
                    [
                        'All good — nothing falling through the cracks',
                        'Capture something to inbox',
                    ],
                )
            )
            if action is None:
                return reviewed
            if action.startswith('All good'):
                reviewed += 1
                break
            capture_text = await app.push_screen_wait(
                InputModal(
                    f'Capture for [{name}]',
                    subtitle=name,
                    placeholder='What needs attention?',
                )
            )
            if capture_text:
                await loop.run_in_executor(
                    None, _create_page, capture_text.strip()
                )
                app.notify('Captured → Inbox')
    return reviewed


class WeeklyReviewScreen(ModalScreen[bool]):
    """Step-by-step guided GTD weekly review."""

    DEFAULT_CSS = """
    WeeklyReviewScreen { align: center middle; }
    WeeklyReviewScreen .modal-box {
        background: $surface;
        border: solid $accent;
        padding: 1 2;
        width: 70;
        height: auto;
        max-height: 30;
    }
    WeeklyReviewScreen .review-title {
        text-style: bold;
        margin-bottom: 1;
    }
    WeeklyReviewScreen .review-item { padding: 0 1; height: 1; }
    WeeklyReviewScreen .review-item:focus { background: $accent 30%; }
    WeeklyReviewScreen .review-footer {
        margin-top: 1;
        color: $text-muted;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding('escape', 'cancel', 'Cancel'),
        Binding('enter,space', 'toggle', 'Check/Launch', show=True),
        Binding('X', 'reset', 'Reset', show=True),
        Binding('j', 'cursor_down', show=False),
        Binding('k', 'cursor_up', show=False),
        Binding('down', 'cursor_down', show=False),
        Binding('up', 'cursor_up', show=False),
    ]

    def __init__(
        self,
        inbox_entries: list[ProjectEntry],
        inbox_count: int,
    ) -> None:
        super().__init__()
        self._inbox_entries = inbox_entries
        self._inbox_count = inbox_count
        self._steps: list[dict] = self._build_steps()
        self._focused = 0
        self._restore_state()

    def _build_steps(self) -> list[dict]:
        steps = []
        for label, action in _GTD_REVIEW_STEPS:
            if action == 'triage':
                if self._inbox_count == 0:
                    display = 'Process Inbox  [dim](empty ✓)[/dim]'
                    done = True
                else:
                    n = self._inbox_count
                    c = 's' if n != 1 else ''
                    display = f'Triage Inbox  [dim]({n} item{c})[/dim]'
                    done = False
                steps.append(
                    {'label': display, 'done': done, 'action': action}
                )
            else:
                steps.append({'label': label, 'done': False, 'action': action})
        return steps

    def _restore_state(self) -> None:
        from gtd.storage import load_review_state

        saved = load_review_state(len(self._steps))
        for i, done in enumerate(saved):
            if done and not self._steps[i]['done']:
                self._steps[i]['done'] = True

    def _save_state(self) -> None:
        from gtd.storage import save_review_state

        save_review_state([s['done'] for s in self._steps])

    def compose(self) -> ComposeResult:
        with Vertical(classes='modal-box'):
            yield Static('── Weekly Review ──', classes='review-title')
            for i, step in enumerate(self._steps):
                mark = '[green]✓[/green]' if step['done'] else '[ ]'
                yield Static(
                    f'{mark}  {step["label"]}',
                    id=f'review-step-{i}',
                    classes='review-item',
                )
            yield Static(
                '[dim]j/k · space: launch · X: reset · esc: cancel[/dim]',
                classes='review-footer',
            )

    def on_mount(self) -> None:
        self._refresh_steps()
        # Resume at first undone step
        for i, step in enumerate(self._steps):
            if not step['done']:
                self._focus_step(i)
                return
        self._focus_step(0)

    def _focus_step(self, idx: int) -> None:
        self._focused = max(0, min(idx, len(self._steps) - 1))
        self._refresh_steps()

    def _refresh_steps(self) -> None:
        for i, step in enumerate(self._steps):
            mark = '[green]✓[/green]' if step['done'] else '[ ]'
            cursor = (
                '[bold cyan]►[/bold cyan] ' if i == self._focused else '  '
            )
            with contextlib.suppress(Exception):
                self.query_one(f'#review-step-{i}', Static).update(
                    f'{cursor}{mark}  {step["label"]}'
                )

    def _advance(self) -> None:
        for i in range(self._focused + 1, len(self._steps)):
            if not self._steps[i]['done']:
                self._focus_step(i)
                return

    def action_cursor_down(self) -> None:
        self._focus_step(self._focused + 1)

    def action_cursor_up(self) -> None:
        self._focus_step(self._focused - 1)

    async def _run_step(self, step: dict) -> bool:
        action = step['action']
        if action == 'triage' and self._inbox_entries:
            inbox_content = self.app.query_one(InboxContent)
            inbox_content.seed_entries(self._inbox_entries)
            return await inbox_content.triage_entries(self._inbox_entries)
        if action == 'projects':
            n = await _review_projects(self.app)
            if n > 0:
                step['label'] = f'Review Projects  [dim]({n} reviewed)[/dim]'
        elif action == 'waiting':
            n = await _review_waiting_for(self.app)
            if n > 0:
                step['label'] = (
                    f'Review Waiting For  [dim]({n} reviewed)[/dim]'
                )
        elif action == 'someday':
            n = await _review_someday(self.app)
            if n > 0:
                step['label'] = (
                    f'Review Someday/Maybe  [dim]({n} reviewed)[/dim]'
                )
        elif action == 'areas':
            n = await _review_areas(self.app)
            if n > 0:
                step['label'] = (
                    f'Review Horizons of Focus  [dim]({n} reviewed)[/dim]'
                )
        return True

    @work
    async def action_toggle(self) -> None:
        step = self._steps[self._focused]
        if step['done']:
            step['done'] = False
            self._refresh_steps()
            self._save_state()
            return

        completed = await self._run_step(step)
        if completed:
            step['done'] = True
            self._save_state()
            self._advance()
        self._refresh_steps()
        if all(s['done'] for s in self._steps):
            self.dismiss(result=True)

    @work
    async def action_reset(self) -> None:
        confirmed = await self.app.push_screen_wait(
            ConfirmModal('Reset weekly review progress?')
        )
        if not confirmed:
            return
        from gtd.storage import reset_review_state

        reset_review_state()
        for step in self._steps:
            step['done'] = False
        self._refresh_steps()
        self._focus_step(0)
        self.app.notify('Review progress reset')

    def action_cancel(self) -> None:
        self.dismiss(result=False)


# ── Today content ────────────────────────────────────────────────────────────


class TodayContent(BaseEntryContent):
    """Today's actionable items — the GTD daily driver."""

    TITLE: ClassVar[str] = 'Today'
    EMPTY_MSG: ClassVar[str] = 'All clear. Nice work.'

    BINDINGS: ClassVar[list[Binding]] = [
        Binding('W', 'complete_habit', 'Complete'),
        Binding('T', 'wait_tomorrow', 'Tomorrow'),
        Binding('S', 'edit_steps', 'Steps'),
        Binding('N', 'edit_notes', 'Notes'),
        Binding('D', 'mark_done', 'Done'),
        Binding('U', 'update_entry', 'Update'),
    ]

    _GTD_ACTIONS: ClassVar[set[str]] = {
        'wait_tomorrow',
        'edit_steps',
        'update_entry',
        'edit_notes',
        'mark_done',
        'complete_step',
    }
    _HABIT_ACTIONS: ClassVar[set[str]] = {'complete_habit'}

    def __init__(self) -> None:
        super().__init__()
        self._habit_items: list[WeeklyHabitItem] = []

    def _build_filter(self) -> dict:
        return {}

    @work(thread=True)
    def _load_entries(self) -> None:
        from gtd.notion.entries import _get_today_entries

        try:
            entries = _get_today_entries()
            self.app.call_from_thread(self._set_entries, entries)
        except Exception as e:
            msg = f'Notion error: {e}'
            self.app.call_from_thread(
                lambda: self.app.notify(msg, severity='error')
            )
            self.app.call_from_thread(self._set_entries, [])

    def _populate_list(
        self,
        lv: VimListView,
        entries: list[ProjectEntry],
    ) -> None:
        lv.clear()
        for item in self._habit_items:
            lv.append(item)
        if self._habit_items and entries:
            lv.append(SeparatorListItem('GTD'))
        for entry in entries:
            lv.append(NextStepListItem(entry))

    def _set_entries(self, entries: list[ProjectEntry]) -> None:
        self._entries = entries
        with contextlib.suppress(Exception):
            self.query_one('#entry-loading', LoadingIndicator).display = False

        self._habit_items = [
            WeeklyHabitItem(key, label)
            for key, label in WEEKLY_HABITS
            if not _habit_done_this_week(key)
        ]

        lv = self.query_one('#entry-list', VimListView)
        self._populate_list(lv, entries)

        header = self.query_one('#entry-list-header', Static)
        detail = self.query_one('#entry-detail', Static)
        has_content = entries or self._habit_items
        if not has_content:
            header.update('Today — nothing actionable 🎉')
            detail.update('[dim]All clear. Nice work.[/dim]')
        else:
            header.update(f'Today  [dim]({len(entries)})[/dim]')
            lv.index = 0
            self._update_detail()

    @on(ListView.Highlighted, '#entry-list')
    def on_list_highlighted(self) -> None:
        self._update_detail()
        self.app.refresh_bindings()

    def _current_entry(self) -> ProjectEntry | None:
        item = self.query_one('#entry-list', VimListView).highlighted_child
        if not isinstance(item, EntryListItem):
            return None
        pid = item.page_id
        return next((e for e in self._entries if e.page_id == pid), None)

    def _current_habit_item(self) -> WeeklyHabitItem | None:
        item = self.query_one('#entry-list', VimListView).highlighted_child
        return item if isinstance(item, WeeklyHabitItem) else None

    def _update_detail(self) -> None:
        habit_item = self._current_habit_item()
        if habit_item is not None:
            detail = _render_habit_detail(
                habit_item.habit_key, habit_item.habit_label
            )
            self.query_one('#entry-detail', Static).update(detail)
            return
        entry = self._current_entry()
        if not entry:
            return
        notes = self._notes.get(entry.page_id)
        self.query_one('#entry-detail', Static).update(
            _render_entry_detail(entry, notes)
        )
        if entry.page_id not in self._notes:
            self._load_notes(entry.page_id)

    def check_action(
        self,
        action: str,
        parameters: tuple[object, ...],  # noqa: ARG002
    ) -> bool | None:
        habit_focused = self._current_habit_item() is not None
        if action in self._HABIT_ACTIONS:
            return habit_focused
        if action in self._GTD_ACTIONS:
            return not habit_focused
        return None

    @work
    async def action_complete_habit(self) -> None:
        item = self._current_habit_item()
        if not item:
            return

        if item.habit_key == 'weekly_review':
            confirmed = await self._run_weekly_review_flow()
        else:
            confirmed = await self.app.push_screen_wait(
                ConfirmModal(f'Mark "{item.habit_label}" done for this week?')
            )

        if confirmed:
            self._dismiss_habit_item(item)

    async def _run_weekly_review_flow(self) -> bool:
        loop = asyncio.get_running_loop()

        inbox_entries: list[ProjectEntry] = []
        inbox_count = 0
        try:
            from gtd.notion.client import query_database

            inbox_filter = {
                'or': [
                    {'property': 'Status', 'select': {'equals': 'Triage'}},
                    {'property': 'Status', 'select': {'is_empty': True}},
                    {'property': 'Context', 'select': {'is_empty': True}},
                    {
                        'and': [
                            {
                                'property': 'Status',
                                'select': {'does_not_equal': 'List'},
                            },
                            {
                                'property': 'Next Actionable Step',
                                'rich_text': {'is_empty': True},
                            },
                        ]
                    },
                    {
                        'and': [
                            {
                                'property': 'Status',
                                'select': {'does_not_equal': 'List'},
                            },
                            {
                                'property': 'Success Condition',
                                'rich_text': {'is_empty': True},
                            },
                        ]
                    },
                ]
            }
            pages = await loop.run_in_executor(
                None,
                lambda: query_database(filter_obj=inbox_filter),
            )
            inbox_entries = [ProjectEntry.from_page(p) for p in pages]
            inbox_count = len(inbox_entries)
        except Exception:
            inbox_count = 0

        return (
            await self.app.push_screen_wait(
                WeeklyReviewScreen(inbox_entries, inbox_count)
            )
            or False
        )

    def _dismiss_habit_item(self, item: WeeklyHabitItem) -> None:
        from gtd.storage import set_weekly_habit_date

        set_weekly_habit_date(item.habit_key)
        lv = self.query_one('#entry-list', VimListView)
        idx = lv.index
        item.remove()
        self._habit_items = [
            h for h in self._habit_items if h.habit_key != item.habit_key
        ]
        lv.index = max(0, (idx or 1) - 1)
        self._update_detail()
        self.app.refresh_bindings()
        self.app.notify(f'✓ {item.habit_label} done for this week')

    def action_refresh_today(self) -> None:
        self.action_refresh()

    @work
    async def action_log(self) -> None:
        entry = self._current_entry()
        if not entry:
            return
        next_date = await _shared_log_and_reschedule(
            self.app, entry, self._notes
        )
        if next_date:
            self._remove_entry_and_refocus(entry.page_id)
            self.app.notify(f'✓ "{entry.header.strip()}" → {next_date}')

    async def action_wait_tomorrow(self) -> None:
        entry = self._current_entry()
        if not entry:
            return
        tomorrow = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
        self._snooze_worker(entry.page_id, tomorrow)
        self._remove_entry_and_refocus(entry.page_id)
        self.app.notify(f'✓ "{entry.header.strip()}" → {tomorrow}')

    @work
    async def action_edit_steps(self) -> None:
        entry = self._current_entry()
        if not entry:
            return
        val = await _open_steps_editor(
            self.app, initial_text=entry.next_step or ''
        )
        if val is None:
            return
        self._update_worker(
            entry.page_id,
            {'next_step': val},
        )
        entry.next_step = val
        self._update_detail()
        self.app.notify('✓ Steps updated')

    @work(thread=True)
    def _snooze_worker(self, page_id: str, date: str) -> None:
        from gtd.notion.client import build_property_update, update_page

        props = build_property_update(follow_up_date=date)
        update_page(page_id, props)

    @work
    async def action_waiting_for(self) -> None:
        entry = self._current_entry()
        if not entry:
            return
        result = await self.app.push_screen_wait(
            TwoFieldModal(
                'Waiting For',
                'Who/what are you waiting on?',
                initial1=entry.next_step or '',
                label2='Follow-up date (optional)',
                placeholder2='e.g. Friday, in 3 days',
            )
        )
        if not result:
            return
        waiting_on, followup_str = result
        from gtd.notion.entries import _parse_date_input

        follow_date = _parse_date_input(followup_str) if followup_str else None
        self._waiting_for_worker(entry.page_id, waiting_on, follow_date)
        self._remove_entry_and_refocus(entry.page_id)
        self.app.notify(f'✓ "{entry.header.strip()}" → Waiting For')

    @work(thread=True)
    def _waiting_for_worker(
        self, page_id: str, waiting_on: str, follow_date: str | None
    ) -> None:
        from gtd.notion.client import build_property_update, update_page

        kwargs: dict = {'status': 'Waiting For'}
        if waiting_on:
            kwargs['next_step'] = waiting_on
        if follow_date:
            kwargs['follow_up_date'] = follow_date
        update_page(page_id, build_property_update(**kwargs))

    @work
    async def action_triage_entry(self) -> None:
        entry = self._current_entry()
        if not entry:
            return
        triaged = await self._triage_one(entry)
        if triaged:
            self._remove_entry(entry.page_id)

    @work
    async def action_triage_all(self) -> None:
        await self.triage_entries(self._entries)

    async def triage_entries(self, entries: list[ProjectEntry]) -> bool:
        """Returns True if completed, False if cancelled."""
        processed = 0
        for entry in list(entries):
            triaged = await self._triage_one(entry)
            if triaged is None:
                return False
            if triaged:
                processed += 1
                self._remove_entry(entry.page_id)
        if processed:
            s = 's' if processed != 1 else ''
            self.app.notify(f'✓ Triaged {processed} item{s}')
        return True

    async def _triage_one(self, entry: ProjectEntry) -> bool | None:  # noqa: PLR0911, PLR0912, C901, PLR0915
        """Triage a single entry — only prompts for missing fields.

        Returns True if saved, False if skipped/deleted, None if cancelled.
        """
        from dateutil import parser as dateparser
        from gtd.notion.client import (
            archive_page,
            build_property_update,
            get_select_options,
            update_page,
        )
        from gtd.notion.triage import TRIAGE_STATUSES

        title = entry.header.strip()
        kwargs: dict = {}

        needs_status = not entry.status or entry.status == 'Triage'

        if entry.status == 'List' and entry.context:
            return True
        if needs_status:
            status = await self.app.push_screen_wait(
                SelectModal(f'Triage: {title}', TRIAGE_STATUSES)
            )
            if status is None:
                return None
            if status == 'Delete':
                confirmed = await self.app.push_screen_wait(
                    ConfirmModal(f'Delete "{title}"?')
                )
                if confirmed:
                    try:
                        await asyncio.get_running_loop().run_in_executor(
                            None, archive_page, entry.page_id
                        )
                    except Exception as e:
                        self.app.notify(
                            f'⚠ Delete failed: {e}',
                            severity='error',
                        )
                        return False
                    self.app.notify(f'✓ Deleted "{title}"')
                    return True
                return False
            kwargs['status'] = status
        else:
            status = entry.status
            action = await self.app.push_screen_wait(
                SelectModal(
                    f'{title}',
                    ['Continue — fill in missing fields', 'Drop this item'],
                )
            )
            if action is None:
                return None
            if action and action.startswith('Drop'):
                confirmed = await self.app.push_screen_wait(
                    ConfirmModal(f'Delete "{title}"?')
                )
                if confirmed:
                    try:
                        await asyncio.get_running_loop().run_in_executor(
                            None, archive_page, entry.page_id
                        )
                    except Exception as e:
                        self.app.notify(
                            f'⚠ Delete failed: {e}',
                            severity='error',
                        )
                        return False
                    self.app.notify(f'✓ Deleted "{title}"')
                    return True
                return False

        subtitle = title

        if not entry.context:
            if status == 'List':
                from gtd.notion.client import get_list_categories

                list_categories = (
                    await asyncio.get_running_loop().run_in_executor(
                        None, get_list_categories
                    )
                )
                list_category = await self.app.push_screen_wait(
                    SelectModal(
                        f'Which list? {title}',
                        sorted(list_categories),
                        allow_new=True,
                    )
                )
                if list_category is None:
                    return None
                kwargs['list_category'] = list_category
            else:
                from gtd.notion.client import (
                    add_context,
                    remove_context,
                )

                loop = asyncio.get_running_loop()
                context = None
                while True:
                    contexts = await loop.run_in_executor(
                        None, get_select_options, 'Context'
                    )
                    context = await self.app.push_screen_wait(
                        SelectModal(
                            f'Context: {title}',
                            [
                                *sorted(contexts),
                                '──────────',
                                '[+ Add new context]',
                                '[- Remove context]',
                            ],
                            allow_new=False,
                        )
                    )
                    if context is None:
                        return None
                    if context == '[+ Add new context]':
                        new_name = await self.app.push_screen_wait(
                            InputModal('New context name')
                        )
                        if new_name:
                            await loop.run_in_executor(
                                None, add_context, new_name
                            )
                            self.app.notify(f'✓ Added context: {new_name}')
                        continue
                    if context == '[- Remove context]':
                        contexts_to_remove = await loop.run_in_executor(
                            None, get_select_options, 'Context'
                        )
                        remove_name = await self.app.push_screen_wait(
                            SelectModal(
                                'Remove context',
                                sorted(contexts_to_remove),
                                allow_new=False,
                            )
                        )
                        if remove_name:
                            await loop.run_in_executor(
                                None, remove_context, remove_name
                            )
                            msg = f'✓ Removed context: {remove_name}'
                            self.app.notify(msg)
                        continue
                    if context != '──────────':
                        break
                kwargs['context'] = context

        if status == 'List':
            props = build_property_update(**kwargs)
            await asyncio.get_running_loop().run_in_executor(
                None, update_page, entry.page_id, props
            )
            cat = kwargs.get('list_category', entry.list_category)
            self.app.notify(f'✓ "{title}" → List [{cat}]')
            return True

        if not entry.next_step:
            if status == 'Waiting For':
                next_step = await self.app.push_screen_wait(
                    InputModal(
                        'Who/what are you waiting on?',
                        title,
                        subtitle=subtitle,
                    )
                )
                if next_step is None:
                    return None
                if next_step:
                    kwargs['next_step'] = next_step
            else:
                val = await _open_steps_editor(self.app)
                if val:
                    kwargs['next_step'] = val

        if not entry.success_condition:
            success_condition = await self.app.push_screen_wait(
                InputModal(
                    'Success condition',
                    'What does done look like?',
                    subtitle=subtitle,
                )
            )
            if success_condition is None:
                return None
            if success_condition:
                kwargs['success_condition'] = success_condition

        if not entry.due_date and status != 'Recurring':
            due_str = await self.app.push_screen_wait(
                InputModal(
                    'Due date (blank to skip)',
                    'e.g. Jul 15, 2026-08-01',
                    subtitle=subtitle,
                )
            )
            if due_str:
                try:
                    parsed = dateparser.parse(due_str, fuzzy=True)
                    kwargs['due_date'] = parsed.strftime('%Y-%m-%d')
                except Exception:
                    self.app.notify(
                        f'Could not parse "{due_str}", skipping due date.',
                        severity='warning',
                    )

        if not entry.follow_up_date:
            follow_up_prompt = (
                'Follow-up date (required)'
                if status == 'Waiting For'
                else 'Follow-up date (blank to skip)'
            )
            follow_str = await self.app.push_screen_wait(
                InputModal(
                    follow_up_prompt,
                    'e.g. Friday, in 3 days',
                    subtitle=subtitle,
                )
            )
            if follow_str:
                try:
                    parsed = dateparser.parse(follow_str, fuzzy=True)
                    kwargs['follow_up_date'] = parsed.strftime('%Y-%m-%d')
                except Exception:
                    self.app.notify(
                        f'Could not parse "{follow_str}", skipping follow-up.',
                        severity='warning',
                    )

        if not kwargs:
            return True

        props = build_property_update(**kwargs)
        try:
            await asyncio.get_running_loop().run_in_executor(
                None, update_page, entry.page_id, props
            )
        except Exception as e:
            self.app.notify(f'⚠ Save failed: {e}', severity='error')
            return False
        final_status = kwargs.get('status', entry.status)
        final_context = kwargs.get('context', entry.context)
        self.app.notify(f'✓ "{title}" → {final_status} [{final_context}]')
        return True

    @work
    async def action_update_entry(self) -> None:  # noqa: PLR0911
        from gtd.notion.client import (
            build_property_update,
            get_select_options,
            update_page,
        )

        entry = self._current_entry()
        if not entry:
            return

        fields = [
            'Name',
            'Status',
            'Context',
            'Steps',
            'Follow-up date',
            'Due date',
        ]
        choice = await self.app.push_screen_wait(
            SelectModal('Update which field?', fields)
        )
        if not choice:
            return

        if choice != 'Status':
            props = await _prompt_and_get_props(self.app, entry, choice)
            if props is None:
                return
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                update_page,
                entry.page_id,
                build_property_update(**props),
            )
            self._load_entries()
            self.app.notify(f'✓ "{entry.header.strip()}" updated')
            return

        # Setting Status requires Context + Next Actionable Step
        status = await self.app.push_screen_wait(
            SelectModal('Status', STATUSES)
        )
        if not status:
            return

        loop = asyncio.get_running_loop()
        context = entry.context
        if not context:
            contexts = await loop.run_in_executor(
                None, get_select_options, 'Context'
            )
            context = await self.app.push_screen_wait(
                SelectModal(
                    'Context (required to move out)',
                    sorted(contexts),
                    allow_new=True,
                )
            )
            if not context:
                self.app.notify(
                    'Context required to move out of Inbox',
                    severity='warning',
                )
                return

        next_step = entry.next_step
        if not next_step:
            next_step = await _open_steps_editor(self.app)
            if not next_step:
                self.app.notify(
                    'Steps required to move out of Inbox',
                    severity='warning',
                )
                return

        await loop.run_in_executor(
            None,
            update_page,
            entry.page_id,
            build_property_update(
                status=status, context=context, next_step=next_step
            ),
        )
        self._load_entries()
        self.app.notify(f'✓ "{entry.header.strip()}" → {status}')


# ── Inbox content ────────────────────────────────────────────────────────────


class InboxContent(BaseEntryContent):
    """Triage inbox — items waiting to be processed."""

    TITLE: ClassVar[str] = 'Inbox'
    EMPTY_MSG: ClassVar[str] = 'Inbox zero! 🎉'

    BINDINGS: ClassVar[list[Binding]] = [
        Binding('T', 'triage_entry', 'Triage'),
        Binding('A', 'triage_all', 'Triage All'),
        Binding('U', 'update_entry', 'Update'),
        Binding('N', 'edit_notes', 'Notes'),
        Binding('D', 'drop_entry', 'Drop'),
    ]

    _ENTRY_ACTIONS: ClassVar[set[str]] = {
        'triage_entry',
        'update_entry',
        'edit_notes',
        'drop_entry',
    }

    def check_action(
        self,
        action: str,
        parameters: tuple[object, ...],  # noqa: ARG002
    ) -> bool | None:
        if action == 'triage_all':
            return True
        if action in self._ENTRY_ACTIONS:
            return self._current_entry() is not None
        return None

    def _build_filter(self) -> dict:
        return {
            'and': [
                {
                    'property': 'Status',
                    'select': {'does_not_equal': 'List'},
                },
                {
                    'or': [
                        {
                            'property': 'Status',
                            'select': {'equals': 'Triage'},
                        },
                        {
                            'property': 'Status',
                            'select': {'is_empty': True},
                        },
                        {
                            'property': 'Context',
                            'select': {'is_empty': True},
                        },
                        {
                            'property': 'Next Actionable Step',
                            'rich_text': {'is_empty': True},
                        },
                        {
                            'property': 'Success Condition',
                            'rich_text': {'is_empty': True},
                        },
                    ],
                },
            ],
        }

    def seed_entries(self, entries: list[ProjectEntry]) -> None:
        """Pre-populate entries (e.g. from weekly review flow)."""
        self._entries = entries

    @work
    async def action_triage_entry(self) -> None:
        entry = self._current_entry()
        if not entry:
            return
        triaged = await self._triage_one(entry)
        if triaged:
            self._remove_entry(entry.page_id)

    @work
    async def action_triage_all(self) -> None:
        await self.triage_entries(self._entries)

    async def triage_entries(self, entries: list[ProjectEntry]) -> bool:
        """Returns True if completed, False if cancelled."""
        processed = 0
        for entry in list(entries):
            triaged = await self._triage_one(entry)
            if triaged is None:
                return False
            if triaged:
                processed += 1
                self._remove_entry(entry.page_id)
        if processed:
            s = 's' if processed != 1 else ''
            self.app.notify(f'✓ Triaged {processed} item{s}')
        return True

    async def _triage_one(self, entry: ProjectEntry) -> bool | None:  # noqa: PLR0911, PLR0912, C901, PLR0915
        """Triage a single entry — only prompts for missing fields.

        Returns True if saved, False if skipped/deleted, None if cancelled.
        """
        from dateutil import parser as dateparser
        from gtd.notion.client import (
            archive_page,
            build_property_update,
            get_select_options,
            update_page,
        )
        from gtd.notion.triage import TRIAGE_STATUSES

        title = entry.header.strip()
        kwargs: dict = {}

        needs_status = not entry.status or entry.status == 'Triage'

        if entry.status == 'List' and entry.context:
            return True
        if needs_status:
            status = await self.app.push_screen_wait(
                SelectModal(f'Triage: {title}', TRIAGE_STATUSES)
            )
            if status is None:
                return None
            if status == 'Delete':
                confirmed = await self.app.push_screen_wait(
                    ConfirmModal(f'Delete "{title}"?')
                )
                if confirmed:
                    try:
                        await asyncio.get_running_loop().run_in_executor(
                            None, archive_page, entry.page_id
                        )
                    except Exception as e:
                        self.app.notify(
                            f'⚠ Delete failed: {e}',
                            severity='error',
                        )
                        return False
                    self.app.notify(f'✓ Deleted "{title}"')
                    return True
                return False
            kwargs['status'] = status
        else:
            status = entry.status
            action = await self.app.push_screen_wait(
                SelectModal(
                    f'{title}',
                    ['Continue — fill in missing fields', 'Drop this item'],
                )
            )
            if action is None:
                return None
            if action and action.startswith('Drop'):
                confirmed = await self.app.push_screen_wait(
                    ConfirmModal(f'Delete "{title}"?')
                )
                if confirmed:
                    try:
                        await asyncio.get_running_loop().run_in_executor(
                            None, archive_page, entry.page_id
                        )
                    except Exception as e:
                        self.app.notify(
                            f'⚠ Delete failed: {e}',
                            severity='error',
                        )
                        return False
                    self.app.notify(f'✓ Deleted "{title}"')
                    return True
                return False

        subtitle = title

        if not entry.context:
            if status == 'List':
                from gtd.notion.client import get_list_categories

                list_categories = (
                    await asyncio.get_running_loop().run_in_executor(
                        None, get_list_categories
                    )
                )
                list_category = await self.app.push_screen_wait(
                    SelectModal(
                        f'Which list? {title}',
                        sorted(list_categories),
                        allow_new=True,
                    )
                )
                if list_category is None:
                    return None
                kwargs['list_category'] = list_category
            else:
                from gtd.notion.client import (
                    add_context,
                    remove_context,
                )

                loop = asyncio.get_running_loop()
                context = None
                while True:
                    contexts = await loop.run_in_executor(
                        None, get_select_options, 'Context'
                    )
                    context = await self.app.push_screen_wait(
                        SelectModal(
                            f'Context: {title}',
                            [
                                *sorted(contexts),
                                '──────────',
                                '[+ Add new context]',
                                '[- Remove context]',
                            ],
                            allow_new=False,
                        )
                    )
                    if context is None:
                        return None
                    if context == '[+ Add new context]':
                        new_name = await self.app.push_screen_wait(
                            InputModal('New context name')
                        )
                        if new_name:
                            await loop.run_in_executor(
                                None, add_context, new_name
                            )
                            self.app.notify(f'✓ Added context: {new_name}')
                        continue
                    if context == '[- Remove context]':
                        contexts_to_remove = await loop.run_in_executor(
                            None, get_select_options, 'Context'
                        )
                        remove_name = await self.app.push_screen_wait(
                            SelectModal(
                                'Remove context',
                                sorted(contexts_to_remove),
                                allow_new=False,
                            )
                        )
                        if remove_name:
                            await loop.run_in_executor(
                                None, remove_context, remove_name
                            )
                            msg = f'✓ Removed context: {remove_name}'
                            self.app.notify(msg)
                        continue
                    if context != '──────────':
                        break
                kwargs['context'] = context

        if status == 'List':
            props = build_property_update(**kwargs)
            await asyncio.get_running_loop().run_in_executor(
                None, update_page, entry.page_id, props
            )
            cat = kwargs.get('list_category', entry.list_category)
            self.app.notify(f'✓ "{title}" → List [{cat}]')
            return True

        if not entry.next_step:
            if status == 'Waiting For':
                next_step = await self.app.push_screen_wait(
                    InputModal(
                        'Who/what are you waiting on?',
                        title,
                        subtitle=subtitle,
                    )
                )
                if next_step is None:
                    return None
                if next_step:
                    kwargs['next_step'] = next_step
            else:
                val = await _open_steps_editor(self.app)
                if val:
                    kwargs['next_step'] = val

        if not entry.success_condition:
            success_condition = await self.app.push_screen_wait(
                InputModal(
                    'Success condition',
                    'What does done look like?',
                    subtitle=subtitle,
                )
            )
            if success_condition is None:
                return None
            if success_condition:
                kwargs['success_condition'] = success_condition

        if not entry.due_date and status != 'Recurring':
            due_str = await self.app.push_screen_wait(
                InputModal(
                    'Due date (blank to skip)',
                    'e.g. Jul 15, 2026-08-01',
                    subtitle=subtitle,
                )
            )
            if due_str:
                try:
                    parsed = dateparser.parse(due_str, fuzzy=True)
                    kwargs['due_date'] = parsed.strftime('%Y-%m-%d')
                except Exception:
                    self.app.notify(
                        f'Could not parse "{due_str}", skipping due date.',
                        severity='warning',
                    )

        if not entry.follow_up_date:
            follow_up_prompt = (
                'Follow-up date (required)'
                if status == 'Waiting For'
                else 'Follow-up date (blank to skip)'
            )
            follow_str = await self.app.push_screen_wait(
                InputModal(
                    follow_up_prompt,
                    'e.g. Friday, in 3 days',
                    subtitle=subtitle,
                )
            )
            if follow_str:
                try:
                    parsed = dateparser.parse(follow_str, fuzzy=True)
                    kwargs['follow_up_date'] = parsed.strftime('%Y-%m-%d')
                except Exception:
                    self.app.notify(
                        f'Could not parse "{follow_str}", skipping follow-up.',
                        severity='warning',
                    )

        if not kwargs:
            return True

        props = build_property_update(**kwargs)
        try:
            await asyncio.get_running_loop().run_in_executor(
                None, update_page, entry.page_id, props
            )
        except Exception as e:
            self.app.notify(f'⚠ Save failed: {e}', severity='error')
            return False
        final_status = kwargs.get('status', entry.status)
        final_context = kwargs.get('context', entry.context)
        self.app.notify(f'✓ "{title}" → {final_status} [{final_context}]')
        return True

    @work
    async def action_update_entry(self) -> None:  # noqa: PLR0911
        from gtd.notion.client import (
            build_property_update,
            get_select_options,
            update_page,
        )

        entry = self._current_entry()
        if not entry:
            return

        fields = [
            'Name',
            'Status',
            'Context',
            'Steps',
            'Follow-up date',
            'Due date',
        ]
        choice = await self.app.push_screen_wait(
            SelectModal('Update which field?', fields)
        )
        if not choice:
            return

        if choice != 'Status':
            props = await _prompt_and_get_props(self.app, entry, choice)
            if props is None:
                return
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                update_page,
                entry.page_id,
                build_property_update(**props),
            )
            self._load_entries()
            self.app.notify(f'✓ "{entry.header.strip()}" updated')
            return

        # Setting Status requires Context + Next Actionable Step
        status = await self.app.push_screen_wait(
            SelectModal('Status', STATUSES)
        )
        if not status:
            return

        loop = asyncio.get_running_loop()
        context = entry.context
        if not context:
            contexts = await loop.run_in_executor(
                None, get_select_options, 'Context'
            )
            context = await self.app.push_screen_wait(
                SelectModal(
                    'Context (required to move out)',
                    sorted(contexts),
                    allow_new=True,
                )
            )
            if not context:
                self.app.notify(
                    'Context required to move out of Inbox',
                    severity='warning',
                )
                return

        next_step = entry.next_step
        if not next_step:
            next_step = await _open_steps_editor(self.app)
            if not next_step:
                self.app.notify(
                    'Steps required to move out of Inbox',
                    severity='warning',
                )
                return

        await loop.run_in_executor(
            None,
            update_page,
            entry.page_id,
            build_property_update(
                status=status, context=context, next_step=next_step
            ),
        )
        self._load_entries()
        self.app.notify(f'✓ "{entry.header.strip()}" → {status}')


# ── Projects content ─────────────────────────────────────────────────────────


class ProjectsContent(BaseEntryContent):
    """All active projects — Current Project and Waiting For."""

    TITLE: ClassVar[str] = 'Projects'
    EMPTY_MSG: ClassVar[str] = 'No active projects.'

    BINDINGS: ClassVar[list[Binding]] = [
        Binding('U', 'update_entry', 'Update'),
        Binding('S', 'edit_steps', 'Steps'),
        Binding('N', 'edit_notes', 'Notes'),
        Binding('D', 'mark_done', 'Done'),
        Binding('M', 'move_someday', 'Someday'),
    ]

    def _build_filter(self) -> dict:
        return {
            'or': [
                {
                    'property': 'Status',
                    'select': {'equals': 'Current Project'},
                },
                {
                    'property': 'Status',
                    'select': {'equals': 'Waiting For'},
                },
            ],
        }

    def _set_entries(self, entries: list[ProjectEntry]) -> None:
        entries.sort(
            key=lambda e: (
                e.context or '\xff',
                e.due_date or '\xff',
            )
        )
        self._entries = entries
        with contextlib.suppress(Exception):
            self.query_one('#entry-loading', LoadingIndicator).display = False
        lv = self.query_one('#entry-list', VimListView)
        lv.clear()
        current_ctx: str | None = None
        for entry in entries:
            ctx = entry.context or ''
            if ctx != current_ctx:
                current_ctx = ctx
                lv.append(SeparatorListItem(ctx or '(no context)'))
            lv.append(EntryListItem(entry))
        header = self.query_one('#entry-list-header', Static)
        detail = self.query_one('#entry-detail', Static)
        if not entries:
            header.update(f'{self.TITLE} — empty')
            detail.update(f'[dim]{self.EMPTY_MSG}[/dim]')
        else:
            header.update(f'{self.TITLE}  [dim]({len(entries)})[/dim]')
            lv.index = 0
            self._update_detail()

    def _current_entry(self) -> ProjectEntry | None:
        item = self.query_one('#entry-list', VimListView).highlighted_child
        if not isinstance(item, EntryListItem):
            return None
        return next(
            (e for e in self._entries if e.page_id == item.page_id), None
        )

    @work
    async def action_edit_steps(self) -> None:
        entry = self._current_entry()
        if not entry:
            return
        val = await _open_steps_editor(
            self.app, initial_text=entry.next_step or ''
        )
        if val is None:
            return
        self._update_worker(entry.page_id, {'next_step': val})
        entry.next_step = val
        self._update_detail()
        self.app.notify('✓ Steps updated')


# ── Next Steps content ───────────────────────────────────────────────────────


class NextStepsContent(BaseEntryContent):
    """Context-divided, filterable next steps for all in-flight projects."""

    TITLE: ClassVar[str] = 'Next Steps'
    EMPTY_MSG: ClassVar[str] = 'No in-flight projects.'

    BINDINGS: ClassVar[list[Binding]] = [
        Binding('U', 'update_entry', 'Update'),
        Binding('S', 'edit_steps', 'Steps'),
        Binding('N', 'edit_notes', 'Notes'),
        Binding('D', 'mark_done', 'Done'),
        Binding('X', 'complete_step', 'Complete Step'),
        Binding('F', 'filter_context', 'Filter ctx'),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._ctx_filter: str | None = None

    def _build_filter(self) -> dict:
        return {
            'property': 'Status',
            'select': {'equals': 'Current Project'},
        }

    def _filtered_entries(self) -> list[ProjectEntry]:
        if self._ctx_filter:
            return [e for e in self._entries if e.context == self._ctx_filter]
        return self._entries

    def _set_entries(self, entries: list[ProjectEntry]) -> None:
        entries.sort(key=lambda e: (e.context or '\xff', e.header.lower()))
        self._entries = entries
        with contextlib.suppress(Exception):
            self.query_one('#entry-loading', LoadingIndicator).display = False
        self._rebuild_list()

    def _rebuild_list(self) -> None:
        filtered = self._filtered_entries()
        lv = self.query_one('#entry-list', VimListView)
        lv.clear()
        current_ctx: str | None = None
        for entry in filtered:
            ctx = entry.context or ''
            if ctx != current_ctx:
                current_ctx = ctx
                lv.append(SeparatorListItem(ctx or '(no context)'))
            lv.append(NextStepListItem(entry))

        detail = self.query_one('#entry-detail', Static)
        header = self.query_one('#entry-list-header', Static)

        ctx_badge = (
            f'  [yellow][{self._ctx_filter}][/yellow]'
            if self._ctx_filter
            else ''
        )
        total = len(self._entries)
        shown = len(filtered)
        count = f'({shown}/{total})' if self._ctx_filter else f'({total})'

        if not filtered:
            header.update(f'{self.TITLE} — empty')
            detail.update(f'[dim]{self.EMPTY_MSG}[/dim]')
        else:
            header.update(f'{self.TITLE}  [dim]{count}[/dim]{ctx_badge}')
            lv.index = 0
            self._update_detail()

    def _current_entry(self) -> ProjectEntry | None:
        item = self.query_one('#entry-list', VimListView).highlighted_child
        if not isinstance(item, NextStepListItem):
            return None
        return next(
            (e for e in self._entries if e.page_id == item.page_id), None
        )

    @work
    async def action_filter_context(self) -> None:
        contexts = sorted({e.context for e in self._entries if e.context})
        options = ['(All)', *contexts]
        choice = await self.app.push_screen_wait(
            SelectModal('Filter by context', options)
        )
        if choice is None:
            return
        self._ctx_filter = None if choice == '(All)' else choice
        self._rebuild_list()

    @work
    async def action_edit_steps(self) -> None:
        entry = self._current_entry()
        if not entry:
            return
        val = await _open_steps_editor(
            self.app, initial_text=entry.next_step or ''
        )
        if val is None:
            return
        self._update_worker(entry.page_id, {'next_step': val})
        entry.next_step = val
        self._update_detail()
        self.app.notify('✓ Steps updated')


# ── Recurring content ────────────────────────────────────────────────────────


class RecurringContent(BaseEntryContent):
    """All recurring tasks — habits and repeating actions."""

    TITLE: ClassVar[str] = 'Recurring'
    EMPTY_MSG: ClassVar[str] = 'No recurring tasks.'

    BINDINGS: ClassVar[list[Binding]] = [
        Binding('U', 'update_entry', 'Update'),
        Binding('N', 'edit_notes', 'Notes'),
        Binding('D', 'drop_entry', 'Drop'),
    ]

    def _build_filter(self) -> dict:
        return {'property': 'Status', 'select': {'equals': 'Recurring'}}

    def _set_entries(self, entries: list[ProjectEntry]) -> None:
        entries = sorted(entries, key=lambda e: e.follow_up_date or '\xff')
        super()._set_entries(entries)


# ── Waiting For content ──────────────────────────────────────────────────────


class WaitingForContent(BaseEntryContent):
    """Items delegated and waiting on someone else."""

    TITLE: ClassVar[str] = 'Waiting For'
    EMPTY_MSG: ClassVar[str] = 'Nothing delegated.'

    BINDINGS: ClassVar[list[Binding]] = [
        Binding('U', 'update_entry', 'Update'),
        Binding('N', 'edit_notes', 'Notes'),
        Binding('D', 'mark_done', 'Done'),
        Binding('A', 'activate', 'Activate'),
    ]

    def _build_filter(self) -> dict:
        return {
            'property': 'Status',
            'select': {'equals': 'Waiting For'},
        }


# ── Someday content ──────────────────────────────────────────────────────────


class SomedayContent(BaseEntryContent):
    """Someday/Maybe — ideas parked for later review."""

    TITLE: ClassVar[str] = 'Someday'
    EMPTY_MSG: ClassVar[str] = 'No someday items.'

    BINDINGS: ClassVar[list[Binding]] = [
        Binding('A', 'activate', 'Activate'),
        Binding('L', 'move_to_list', '→ List'),
        Binding('U', 'update_entry', 'Update'),
        Binding('N', 'edit_notes', 'Notes'),
        Binding('D', 'drop_entry', 'Drop'),
    ]

    def _build_filter(self) -> dict:
        return {
            'property': 'Status',
            'select': {'equals': 'Someday/Maybe'},
        }

    def _set_entries(self, entries: list[ProjectEntry]) -> None:
        entries.sort(
            key=lambda e: (
                e.context or '\xff',
                e.due_date or '\xff',
            )
        )
        self._entries = entries
        with contextlib.suppress(Exception):
            self.query_one('#entry-loading', LoadingIndicator).display = False
        lv = self.query_one('#entry-list', VimListView)
        lv.clear()
        current_ctx: str | None = None
        for entry in entries:
            ctx = entry.context or ''
            if ctx != current_ctx:
                current_ctx = ctx
                lv.append(SeparatorListItem(ctx or '(no context)'))
            lv.append(EntryListItem(entry))
        header = self.query_one('#entry-list-header', Static)
        detail = self.query_one('#entry-detail', Static)
        if not entries:
            header.update(f'{self.TITLE} — empty')
            detail.update(f'[dim]{self.EMPTY_MSG}[/dim]')
        else:
            header.update(f'{self.TITLE}  [dim]({len(entries)})[/dim]')
            lv.index = 0
            self._update_detail()

    def _current_entry(self) -> ProjectEntry | None:
        item = self.query_one('#entry-list', VimListView).highlighted_child
        if not isinstance(item, EntryListItem):
            return None
        return next(
            (e for e in self._entries if e.page_id == item.page_id), None
        )

    @work
    async def action_move_to_list(self) -> None:
        from gtd.notion.client import get_list_categories

        entry = self._current_entry()
        if not entry:
            return
        loop = asyncio.get_running_loop()
        categories = await loop.run_in_executor(None, get_list_categories)
        cat = await self.app.push_screen_wait(
            SelectModal('Add to which list?', sorted(categories))
        )
        if not cat:
            return
        self._move_to_list_worker(entry.page_id, cat)
        self._remove_entry_and_refocus(entry.page_id)
        self.app.notify(f'✓ "{entry.header.strip()}" → {cat}')

    @work(thread=True)
    def _move_to_list_worker(self, page_id: str, category: str) -> None:
        from gtd.notion.client import build_property_update, update_page

        update_page(
            page_id,
            {
                **build_property_update(status='List'),
                'Context': {'select': {'name': category}},
            },
        )


# ── Snoozed content ──────────────────────────────────────────────────────────


class SnoozedContent(BaseEntryContent):
    """Items with a future follow-up date."""

    TITLE: ClassVar[str] = 'Incubation'
    EMPTY_MSG: ClassVar[str] = 'Nothing in the future.'

    BINDINGS: ClassVar[list[Binding]] = [
        Binding('U', 'update_entry', 'Update'),
        Binding('N', 'edit_notes', 'Notes'),
        Binding('D', 'mark_done', 'Done'),
    ]

    def _build_filter(self) -> dict:
        today = datetime.now().strftime('%Y-%m-%d')
        return {
            'and': [
                {
                    'property': 'Status',
                    'select': {'equals': 'Current Project'},
                },
                {
                    'property': 'Follow-Up Date',
                    'date': {'after': today},
                },
            ],
        }


# ── Lists content ────────────────────────────────────────────────────────────


def _render_list_item_detail(entry: ProjectEntry, notes: str | None) -> str:
    lines = [
        f'[bold cyan]{entry.header.strip()}[/bold cyan]',
        f'[dim]{entry.list_category}[/dim]' if entry.list_category else '',
        '',
    ]
    if notes is None:
        lines.append('[dim]Loading notes...[/dim]')
    elif notes.strip():
        lines.append('[dim]── Notes ──[/dim]')
        for line in notes.split('\n'):
            lines.append(f'  {line}' if line.strip() else '')
    else:
        lines.append('[dim]No notes. Press E to add.[/dim]')
    return '\n'.join(lines)


class ListsContent(BaseEntryContent):
    """Curated reference lists backed by Notion (Status=List)."""

    TITLE: ClassVar[str] = 'Lists'
    EMPTY_MSG: ClassVar[str] = 'No list items.'

    BINDINGS: ClassVar[list[Binding]] = [
        Binding('A', 'add_item', 'Add'),
        Binding('U', 'update_item', 'Update'),
        Binding('E', 'edit_notes', 'Notes'),
        Binding('D', 'drop_entry', 'Drop'),
        Binding('F', 'filter_list', 'Filter list'),
        Binding('+', 'add_category', 'New Area'),
        Binding('-', 'remove_category', 'Remove Area'),
        Binding(')', 'rename_category', 'Rename Area'),
        Binding('(', 'move_item', 'Move Item'),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._list_filter: str | None = None
        self._notion_categories: list[str] = []

    def on_mount(self) -> None:
        self._load_notion_categories()
        super().on_mount()

    @work(thread=True)
    def _load_notion_categories(self) -> None:
        """Load list categories from Notion."""
        from gtd.notion.client import get_list_categories

        try:
            self._notion_categories = get_list_categories()
        except Exception:
            self._notion_categories = []

    def _build_filter(self) -> dict:
        return {'property': 'Status', 'select': {'equals': 'List'}}

    def _set_entries(self, entries: list[ProjectEntry]) -> None:
        entries.sort(
            key=lambda e: (e.list_category or '\xff', e.header.lower())
        )
        self._entries = entries
        with contextlib.suppress(Exception):
            self.query_one('#entry-loading', LoadingIndicator).display = False
        self._rebuild_list()

    def _all_categories(self) -> list[str]:
        """Return all list categories from Notion + any extras from entries."""
        seen = set(self._notion_categories)
        extras = sorted(
            e.list_category
            for e in self._entries
            if e.list_category and e.list_category not in seen
        )
        return sorted(self._notion_categories) + list(dict.fromkeys(extras))

    def _rebuild_list(self) -> None:
        categories = (
            [self._list_filter]
            if self._list_filter
            else self._all_categories()
        )
        by_cat: dict[str, list[ProjectEntry]] = {}
        for e in self._entries:
            by_cat.setdefault(e.list_category or '', []).append(e)

        lv = self.query_one('#entry-list', VimListView)
        lv.clear()
        for cat in categories:
            items = by_cat.get(cat, [])
            items.sort(key=lambda e: (e.success_condition or '\xff', e.header))
            n = len(items)
            label = f'{cat}  ({n})' if n else f'{cat}  [dim](empty)[/dim]'
            lv.append(SeparatorListItem(label))
            for entry in items:
                lv.append(ListEntryListItem(entry))

        total = len(self._entries)
        shown = sum(len(by_cat.get(c, [])) for c in categories)
        header = self.query_one('#entry-list-header', Static)
        ctx_badge = (
            f'  [yellow][{self._list_filter}][/yellow]'
            if self._list_filter
            else ''
        )
        count = f'({shown}/{total})' if self._list_filter else f'({total})'
        header.update(f'{self.TITLE}  [dim]{count}[/dim]{ctx_badge}')
        if lv.children:
            lv.index = 0
            self._update_detail()

    def _current_entry(self) -> ProjectEntry | None:
        item = self.query_one('#entry-list', VimListView).highlighted_child
        if not isinstance(item, EntryListItem | ListEntryListItem):
            return None
        return next(
            (e for e in self._entries if e.page_id == item.page_id), None
        )

    def _current_category(self) -> str | None:
        """Return category name for the nearest separator at/above cursor."""
        lv = self.query_one('#entry-list', VimListView)
        idx = lv.index
        if idx is None:
            return None
        for child in reversed(list(lv.children)[: idx + 1]):
            if isinstance(child, SeparatorListItem):
                return child.raw_label.split('  ')[0]
        return None

    def _update_detail(self) -> None:
        entry = self._current_entry()
        detail = self.query_one('#entry-detail', Static)
        if not entry:
            cat = self._current_category()
            if cat:
                detail.update(
                    f'[bold]{cat}[/bold]\n\n'
                    '[dim]Press N to add an item to this list.[/dim]'
                )
            else:
                detail.update('')
            return
        notes = self._notes.get(entry.page_id)
        detail.update(_render_list_item_detail(entry, notes))
        if entry.page_id not in self._notes:
            self._load_notes(entry.page_id)

    def action_refresh(self) -> None:
        self._list_filter = None
        super().action_refresh()

    def check_action(
        self,
        action: str,
        parameters: tuple[object, ...],
    ) -> bool | None:
        if action in ('complete_step', 'update_entry'):
            return False
        if action == 'update_item':
            return self._current_entry() is not None
        return super().check_action(action, parameters)

    @work
    async def action_add_item(self) -> None:
        from gtd.notion.client import (
            get_projects_db_id,
            get_token,
            NOTION_API_URL,
            NOTION_VERSION,
        )
        import httpx
        from gtd.notion.client import _handle_response
        from datetime import UTC, datetime as _dt

        target = await self.app.push_screen_wait(
            SelectModal('Add to which list?', self._all_categories())
        )
        if not target:
            return

        name = await self.app.push_screen_wait(
            InputModal(f'Add to {target}', 'Item name')
        )
        if not name:
            return

        service = await self.app.push_screen_wait(
            InputModal(
                f'Add to {target}',
                'Extra info (optional)',
            )
        )

        loop = asyncio.get_running_loop()
        db_id = await loop.run_in_executor(None, get_projects_db_id)
        token = await loop.run_in_executor(None, get_token)
        url = f'{NOTION_API_URL}/pages'
        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
            'Notion-Version': NOTION_VERSION,
        }
        props: dict = {
            'Header': {'title': [{'text': {'content': name.strip()}}]},
            'Status': {'select': {'name': 'List'}},
            'List Category': {'select': {'name': target}},
            'Created Date': {
                'date': {
                    'start': _dt.now(tz=UTC).strftime('%Y-%m-%dT%H:%M:%SZ')
                }
            },
        }
        if service and service.strip():
            props['Success Condition'] = {
                'rich_text': [{'text': {'content': service.strip()}}]
            }
        payload = {'parent': {'database_id': db_id}, 'properties': props}
        response = await loop.run_in_executor(
            None,
            lambda: httpx.post(url, headers=headers, json=payload),
        )
        _handle_response(response)
        self._load_entries()
        self.app.notify(f'✓ Added "{name.strip()}" to {target}')

    @work
    async def action_update_item(self) -> None:
        from gtd.notion.client import build_property_update, update_page

        entry = self._current_entry()
        if not entry:
            return
        fields = ['Name', 'Extra info', 'Context']
        choice = await self.app.push_screen_wait(
            SelectModal('Update which field?', fields)
        )
        if not choice:
            return
        if choice == 'Name':
            value = await self.app.push_screen_wait(
                InputModal('Name', initial=entry.header.strip())
            )
            if not value:
                return
            props = build_property_update(name=value.strip())
        elif choice == 'Extra info':
            value = await self.app.push_screen_wait(
                InputModal(
                    'Extra info',
                    'blank to clear',
                    initial=entry.success_condition or '',
                )
            )
            if value is None:
                return
            props = build_property_update(
                success_condition=value.strip() or ''
            )
        elif choice == 'Context':
            from gtd.notion.client import get_select_options

            loop = asyncio.get_running_loop()
            contexts = await loop.run_in_executor(
                None, get_select_options, 'Context'
            )
            value = await self.app.push_screen_wait(
                SelectModal('Context', sorted(contexts), allow_new=True)
            )
            if not value:
                return
            props = build_property_update(context=value)
        else:
            return
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, update_page, entry.page_id, props)
        self._load_entries()
        self.app.notify('✓ Updated')

    @work
    async def action_move_item(self) -> None:
        from gtd.notion.client import build_property_update, update_page

        entry = self._current_entry()
        if not entry:
            return
        destinations = [
            *sorted(self._all_categories()),
            '── Status ──',
            'Someday/Maybe',
            'Current Project',
        ]
        dest = await self.app.push_screen_wait(
            SelectModal('Move to…', destinations)
        )
        if not dest or dest == '── Status ──':
            return
        loop = asyncio.get_running_loop()
        if dest in ('Someday/Maybe', 'Current Project'):
            props = build_property_update(status=dest)
        else:
            props = {
                **build_property_update(status='List', list_category=dest),
            }
        await loop.run_in_executor(
            None, lambda: update_page(entry.page_id, props)
        )
        self._remove_entry_and_refocus(entry.page_id)
        self.app.notify(f'✓ "{entry.header.strip()}" → {dest}')

    async def action_add_category(self) -> None:
        from gtd.notion.client import add_list_category

        name = await self.app.push_screen_wait(
            InputModal('New list category', 'Category name')
        )
        if not name or not name.strip():
            return
        name = name.strip()
        if name in self._notion_categories:
            self.app.notify(f'"{name}" already exists', severity='warning')
            return
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, add_list_category, name)
            self._notion_categories = sorted([*self._notion_categories, name])
            self._rebuild_list()
            self.app.notify(f'✓ Added category "{name}"')
        except Exception as e:
            self.app.notify(f'Failed to add category: {e}', severity='error')

    async def action_remove_category(self) -> None:
        from gtd.notion.client import remove_list_category

        if not self._notion_categories:
            self.app.notify('No categories to remove', severity='warning')
            return
        cat = await self.app.push_screen_wait(
            SelectModal('Remove Area', sorted(self._notion_categories))
        )
        if not cat:
            return
        items = [e for e in self._entries if e.list_category == cat]
        if items:
            confirmed = await self.app.push_screen_wait(
                ConfirmModal(
                    f'"{cat}" has {len(items)} item(s). Remove anyway?'
                )
            )
            if not confirmed:
                return
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, remove_list_category, cat)
            self._notion_categories = [
                c for c in self._notion_categories if c != cat
            ]
            self._rebuild_list()
            self.app.notify(f'✓ Removed category "{cat}"')
        except Exception as e:
            self.app.notify(
                f'Failed to remove category: {e}', severity='error'
            )

    async def action_rename_category(self) -> None:
        from gtd.notion.client import rename_list_category, update_page

        if not self._notion_categories:
            self.app.notify('No categories to rename', severity='warning')
            return
        old = await self.app.push_screen_wait(
            SelectModal('Rename which area?', sorted(self._notion_categories))
        )
        if not old:
            return
        new = await self.app.push_screen_wait(
            InputModal('Rename area', 'New name', initial=old)
        )
        if not new or not new.strip() or new.strip() == old:
            return
        new = new.strip()
        if new in self._notion_categories:
            self.app.notify(f'"{new}" already exists', severity='warning')
            return
        try:
            loop = asyncio.get_running_loop()
            # Update schema
            await loop.run_in_executor(None, rename_list_category, old, new)
            # Update entries
            affected = [e for e in self._entries if e.list_category == old]
            for entry in affected:
                await loop.run_in_executor(
                    None,
                    lambda eid=entry.page_id: update_page(
                        eid, {'List Category': {'select': {'name': new}}}
                    ),
                )
            self._notion_categories = [
                new if c == old else c for c in self._notion_categories
            ]
            self._load_entries()
            self.app.notify(
                f'✓ Renamed "{old}" → "{new}"'
                + (f' ({len(affected)} items updated)' if affected else '')
            )
        except Exception as e:
            self.app.notify(
                f'Failed to rename category: {e}', severity='error'
            )

    @work
    async def action_filter_list(self) -> None:
        options = ['(All)', *self._all_categories()]
        choice = await self.app.push_screen_wait(
            SelectModal('Show which list?', options)
        )
        if choice is None:
            return
        self._list_filter = None if choice == '(All)' else choice
        self._rebuild_list()


def _classify_network_error(err: Exception) -> tuple[str, str]:
    """(message, severity) for network errors; ('', '') means re-raise."""
    import httpx

    if isinstance(err, httpx.TimeoutException):
        return 'Notion timed out — try again', 'warning'
    if isinstance(err, httpx.RequestError):
        return f'Network error: {err}', 'error'
    return '', ''


_TAB_LABELS: dict[str, str] = {
    'tab-today': 'Today',
    'tab-next-steps': 'Next Steps',
    'tab-inbox': 'Inbox',
    'tab-waiting': 'Waiting For',
    'tab-snoozed': 'Incubation',
    'tab-recurring': 'Recurring',
    'tab-someday': 'Someday',
}


def _entry_canonical_tab(entry: ProjectEntry, today: str) -> str:
    status = entry.status or ''
    if status == 'Current Project':
        if entry.follow_up_date and entry.follow_up_date > today:
            return 'tab-snoozed'
        return 'tab-next-steps'
    if status == 'Waiting For':
        return 'tab-waiting'
    if status == 'Recurring':
        return 'tab-recurring'
    if status == 'Someday/Maybe':
        return 'tab-someday'
    return 'tab-inbox'


class GTDSearchProvider(Provider):
    """Fuzzy search across all GTD entries."""

    _entry_index: list[tuple[str, str, ProjectEntry]]

    async def startup(self) -> None:
        self._entry_index = []
        try:
            entries = await asyncio.to_thread(self._fetch_all_entries)
            today = datetime.now().date().isoformat()
            seen: set[str] = set()
            for entry in entries:
                if entry.page_id in seen:
                    continue
                seen.add(entry.page_id)
                tab_id = _entry_canonical_tab(entry, today)
                self._entry_index.append((tab_id, _TAB_LABELS[tab_id], entry))
        except Exception:  # noqa: S110
            pass

    @staticmethod
    def _fetch_all_entries() -> list[ProjectEntry]:
        from gtd.notion.client import query_database

        pages = query_database(
            filter_obj={
                'or': [
                    {'property': 'Status', 'select': {'equals': s}}
                    for s in [
                        'Triage',
                        'Current Project',
                        'Waiting For',
                        'Recurring',
                        'Someday/Maybe',
                    ]
                ]
                + [{'property': 'Status', 'select': {'is_empty': True}}]
            }
        )
        return [ProjectEntry.from_page(p) for p in pages]

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)

        for tab_id, tab_label, entry in self._entry_index:
            search_text = entry.header
            if entry.context:
                search_text += f' {entry.context}'
            if entry.next_step:
                search_text += f' {entry.next_step}'
            score = matcher.match(search_text)
            if score > 0:
                icon = STATUS_ICONS.get(entry.status or '', '·')
                ctx = f'  [{entry.context}]' if entry.context else ''
                yield Hit(
                    score,
                    matcher.highlight(f'{icon} {entry.header.strip()}'),
                    partial(self._navigate_entry, tab_id, entry.page_id),
                    help=f'{tab_label}{ctx}',
                )

    async def _navigate_entry(self, tab_id: str, page_id: str) -> None:
        tc = self.app.query_one('#tabs', TabbedContent)
        tc.active = tab_id
        await asyncio.sleep(0.05)
        with contextlib.suppress(Exception):
            pane = tc.query_one(f'#{tab_id}', TabPane)
            pane.query_one(BaseEntryContent).select_entry(page_id)


class GTDApp(App[None]):
    TITLE = 'GTD'
    COMMANDS: ClassVar[set] = {GTDSearchProvider}
    ENABLE_COMMAND_PALETTE = True

    BINDINGS: ClassVar[list[Binding]] = [
        Binding('ctrl+p', 'command_palette', show=False),
        Binding('/', 'command_palette', 'Search', show=True),
        Binding('h', 'tab_left', '←', priority=True),
        Binding('j', 'focus_list', '↓', priority=False),
        Binding('k', 'focus_list_up', '↑', priority=False),
        Binding('l', 'tab_right', '→', priority=True),
        Binding('tab', 'tab_right', 'Switch Pane', priority=False),
        Binding('down', 'focus_list', show=False),
        Binding('C', 'capture', 'Capture'),
        Binding('R', 'refresh', 'Refresh'),
        Binding('q', 'quit', 'Quit', priority=True),
        Binding('escape', 'quit', show=False),
    ]

    DEFAULT_CSS = """
    GTDApp { background: $surface; }
    TabPane { padding: 0; }
    """

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(id='tabs'):
            with TabPane('Today', id='tab-today'):
                yield TodayContent()
            with TabPane('Next Steps', id='tab-next-steps'):
                yield NextStepsContent()
            with TabPane('Inbox', id='tab-inbox'):
                yield InboxContent()
            with TabPane('Projects', id='tab-projects'):
                yield ProjectsContent()
            with TabPane('Waiting For', id='tab-waiting'):
                yield WaitingForContent()
            with TabPane('Incubation', id='tab-snoozed'):
                yield SnoozedContent()
            with TabPane('Recurring', id='tab-recurring'):
                yield RecurringContent()
            with TabPane('Someday', id='tab-someday'):
                yield SomedayContent()
            with TabPane('Lists', id='tab-lists'):
                yield ListsContent()
        yield SplitFooter()

    @on(VimListView.FocusTabBar)
    def on_focus_tab_bar(self) -> None:
        self.query_one(Tabs).focus()

    @on(TabbedContent.TabActivated)
    def on_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        with contextlib.suppress(Exception):
            pane = self.query_one(f'#{event.tab.id}', TabPane)
            pane.query_one(VimListView).focus()

    def action_focus_list(self) -> None:
        """Drop focus from tab bar back to active tab's list."""
        tc = self.query_one('#tabs', TabbedContent)
        with contextlib.suppress(Exception):
            pane = tc.query_one(f'#{tc.active}', TabPane)
            lv = pane.query_one(VimListView)
            lv.focus()
            lv.action_cursor_down()

    def action_focus_list_up(self) -> None:
        """Focus active tab's list and move up."""
        tc = self.query_one('#tabs', TabbedContent)
        with contextlib.suppress(Exception):
            pane = tc.query_one(f'#{tc.active}', TabPane)
            lv = pane.query_one(VimListView)
            lv.focus()
            lv.action_cursor_up()

    def action_capture(self) -> None:
        """Capture a new item — available from anywhere in the app."""
        tc = self.query_one('#tabs', TabbedContent)
        with contextlib.suppress(Exception):
            pane = tc.query_one(f'#{tc.active}', TabPane)
            pane.query_one(BaseEntryContent).action_capture()

    def action_refresh(self) -> None:
        """Reload the active tab's list."""
        tc = self.query_one('#tabs', TabbedContent)
        with contextlib.suppress(Exception):
            pane = tc.query_one(f'#{tc.active}', TabPane)
            with contextlib.suppress(Exception):
                pane.query_one(BaseEntryContent).action_refresh()
                return
            pane.query_one(ListsContent).action_refresh()

    def action_tab_right(self) -> None:
        tc = self.query_one('#tabs', TabbedContent)
        tab_ids = [p.id for p in tc.query(TabPane)]
        if not tab_ids:
            return
        try:
            idx = tab_ids.index(tc.active)
            tc.active = tab_ids[(idx + 1) % len(tab_ids)]
        except (ValueError, KeyError):
            pass

    def action_tab_left(self) -> None:
        tc = self.query_one('#tabs', TabbedContent)
        tab_ids = [p.id for p in tc.query(TabPane)]
        if not tab_ids:
            return
        try:
            idx = tab_ids.index(tc.active)
            tc.active = tab_ids[(idx - 1) % len(tab_ids)]
        except (ValueError, KeyError):
            pass

    def _handle_exception(self, error: Exception) -> None:
        from textual.worker import WorkerFailed

        if isinstance(error, WorkerFailed) and error.__cause__ is not None:
            msg, severity = _classify_network_error(error.__cause__)
            if msg:
                self.notify(msg, severity=severity)
                return
        super()._handle_exception(error)


def run_gtd_tui() -> None:
    """Launch the unified GTD TUI."""
    GTDApp().run()
