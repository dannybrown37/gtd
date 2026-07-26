"""Manage commands: mark done, defer, waiting for, notion dispatch."""

from gtd.notion.client import (
    archive_page,
    build_property_update,
    query_database,
    update_page,
)
from gtd.notion.entries import (
    _edit_notes,
    _parse_date_input,
    list_entries,
    select_entry,
    show_triage,
)
from gtd.notion.log import _confirm_delete
from gtd.notion.models import ProjectEntry
from gtd.notion.triage import process_triage
from gtd.ui import CancelAction, prompt_input


def mark_done() -> None:
    """Interactively select a Current Project to mark as done."""
    pages = query_database(
        filter_obj={
            'property': 'Status',
            'select': {'equals': 'Current Project'},
        },
    )
    entries = [ProjectEntry.from_page(p) for p in pages]

    if not entries:
        print('No current projects.')
        return

    entry = select_entry(entries, prompt='Mark done')
    if not entry:
        return

    if not _confirm_delete(entry):
        print('  Cancelled.')
        return

    archive_page(entry.page_id)
    print(f'✓ "{entry.header}" → deleted')


def defer_entry() -> None:
    """Set a follow-up date on a current project."""
    pages = query_database(
        filter_obj={
            'property': 'Status',
            'select': {'equals': 'Current Project'},
        },
    )
    entries = [ProjectEntry.from_page(p) for p in pages]

    if not entries:
        print('No current projects.')
        return

    entry = select_entry(entries, prompt='Defer')
    if not entry:
        return

    date_input = prompt_input(
        'Follow-up date (e.g. Monday, Jul 15, in 3 days): ',
    )
    if not date_input:
        return

    date_str = _parse_date_input(date_input)
    if not date_str:
        return

    props = build_property_update(follow_up_date=date_str)
    update_page(entry.page_id, props)
    print(f'✓ "{entry.header}" deferred until {date_str}')


def set_waiting_for() -> None:
    """Move a current project to Waiting For status."""
    pages = query_database(
        filter_obj={
            'property': 'Status',
            'select': {'equals': 'Current Project'},
        },
    )
    entries = [ProjectEntry.from_page(p) for p in pages]

    if not entries:
        print('No current projects.')
        return

    entry = select_entry(entries, prompt='Waiting For')
    if not entry:
        return

    waiting_on = prompt_input(
        'Who/what are you waiting on? '
        f'(current: {entry.next_step or "none"}): ',
    )
    if waiting_on is None:
        return

    followup = prompt_input(
        'Follow-up date (e.g. Friday, in 3 days): ',
    )
    follow_up_date = _parse_date_input(followup) if followup else None

    kwargs: dict = {'status': 'Waiting For'}
    if waiting_on:
        kwargs['next_step'] = waiting_on
    if follow_up_date:
        kwargs['follow_up_date'] = follow_up_date

    props = build_property_update(**kwargs)
    update_page(entry.page_id, props)
    msg = f'✓ "{entry.header.strip()}" → Waiting For'
    if follow_up_date:
        msg += f' (follow up {follow_up_date})'
    print(msg)

    _edit_notes(entry)


def notion_command(args: list[str]) -> None:
    """Dispatch notion subcommands.

    Usage:
        pm notion             — list all entries grouped by status
        pm notion triage      — show items needing triage
        pm notion process     — interactively process triage items
        pm notion <context>   — filter by context name
        pm notion -v [...]    — verbose output (show details)
    """
    verbose = '-v' in args or '--verbose' in args
    args = [a for a in args if a not in ('-v', '--verbose')]

    if not args:
        list_entries(verbose=verbose)
    elif args[0] == 'triage':
        show_triage(verbose=verbose)
    elif args[0] == 'process':
        try:
            process_triage()
        except CancelAction:
            return
    elif args[0] == 'help':
        print(notion_command.__doc__)
    else:
        context = ' '.join(args)
        list_entries(context=context, verbose=verbose)
