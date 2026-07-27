"""Weekly review and Someday/Maybe review flows."""

__all__ = ['brain_dump', 'review_someday', 'weekly_review']

from gtd.notion.capture import capture_item
from gtd.notion.client import (
    archive_page,
    build_property_update,
    get_page_body,
    query_database,
    update_page,
)
from gtd.notion.entries import (
    _edit_entry_fields,
    _entry_preview_text,
    _escape_for_shell,
    update_entry_by_ref,
)
from gtd.notion.log import _confirm_delete
from gtd.notion.models import ProjectEntry
from gtd.notion.triage import get_inbox_entries, process_triage
from gtd.ui import fzf_on_a_list, pause


def review_someday() -> None:  # noqa: C901
    """Review Someday/Maybe items — keep, activate, or drop each."""
    pages = query_database(
        filter_obj={
            'property': 'Status',
            'select': {'equals': 'Someday/Maybe'},
        },
    )
    entries = [ProjectEntry.from_page(p) for p in pages]

    if not entries:
        print('No Someday/Maybe items. 🎉')
        return

    print(
        f'\n  ── Someday/Maybe Review ({len(entries)} items) ──\n',
    )

    activated = 0
    dropped = 0
    total = len(entries)

    for i, entry in enumerate(entries, 1):
        body = get_page_body(entry.page_id)
        preview = _escape_for_shell(
            _entry_preview_text(entry, body),
        )

        action = fzf_on_a_list(
            [
                'Keep',
                'Update',
                'Activate (→ Current Project)',
                'Drop (archive)',
            ],
            prompt=f'[{i}/{total}] "{entry.header.strip()}"',
            preview=f"echo '{preview}'",
        )
        if not action:
            break

        if action == 'Update':
            update_entry_by_ref(entry)
        elif action.startswith('Activate'):
            props = build_property_update(status='Current Project')
            update_page(entry.page_id, props)
            print(f'  ✓ Activated: {entry.header.strip()}')
            activated += 1
        elif action.startswith('Drop'):
            if _confirm_delete(entry):
                archive_page(entry.page_id)
                print(f'  ✓ Dropped: {entry.header.strip()}')
                dropped += 1
            else:
                print('  Kept.')

        print()

    parts = []
    if activated:
        parts.append(f'{activated} activated')
    if dropped:
        parts.append(f'{dropped} dropped')
    kept = len(entries) - activated - dropped
    if kept:
        parts.append(f'{kept} kept')
    if parts:
        print(f'  Review complete: {", ".join(parts)}')


def brain_dump() -> None:
    """Rapid-fire capture loop — get everything out of your head."""
    print('\n── 🧠 Brain Dump ──')
    print('  Get it all out. Capture everything.\n')

    captured = 0
    while True:
        action = fzf_on_a_list(
            ['Capture an idea', 'Done brainstorming'],
            prompt='Brain dump',
        )
        if not action or action == 'Done brainstorming':
            break
        capture_item()
        captured += 1

    if captured:
        print(f'  ✓ Captured {captured} new item(s) → Triage\n')
    else:
        print('  No new items captured.\n')


def _review_get_clear() -> None:
    """Phase 1: Get Clear — empty inbox."""
    print('─── Phase 1: Get Clear ───')
    print('  Goal: Empty your inbox. Process every item.\n')

    triage_items = get_inbox_entries()
    if triage_items:
        summary_lines = [
            f'── Triage Inbox ({len(triage_items)} items) ──',
            '',
        ]
        for item in triage_items:
            summary_lines.append(f'  • {item.header.strip()}')
        preview = _escape_for_shell('\n'.join(summary_lines))

        print(f'  ⚠ {len(triage_items)} item(s) in Triage\n')
        action = fzf_on_a_list(
            ['Process triage now', 'Skip for now'],
            prompt='Inbox',
            preview=f"echo '{preview}'",
        )
        if action == 'Process triage now':
            process_triage()
            print()
    else:
        print('  ✓ Inbox zero! 🎉\n')


def _review_get_current() -> None:  # noqa: C901, PLR0912, PLR0915
    """Phase 2: Get Current — review active projects and Someday/Maybe."""
    print('─── Phase 2: Get Current ───')
    print('  Goal: Review every active project. Is the next action right?\n')

    current_pages = query_database(
        filter_obj={
            'property': 'Status',
            'select': {'equals': 'Current Project'},
        },
    )
    current_entries = [ProjectEntry.from_page(p) for p in current_pages]

    if not current_entries:
        print('  No current projects.\n')
    else:
        missing_next = [e for e in current_entries if not e.next_step]
        print(f'  {len(current_entries)} current project(s)')
        if missing_next:
            print(
                f'  ⚠ {len(missing_next)} missing a next action: '
                + ', '.join(e.header.strip() for e in missing_next),
            )
        print()

        total = len(current_entries)
        updated = 0
        for i, entry in enumerate(current_entries, 1):
            body = get_page_body(entry.page_id)
            preview = _escape_for_shell(_entry_preview_text(entry, body))

            action = fzf_on_a_list(
                [
                    'Looks good',
                    'Update fields',
                    'Mark done',
                    'Move to Someday/Maybe',
                ],
                prompt=f'[{i}/{total}] {entry.header.strip()}',
                preview=f"echo '{preview}'",
            )
            if not action:
                break

            match action:
                case 'Update fields':
                    _edit_entry_fields(entry)
                    updated += 1
                case 'Mark done':
                    if _confirm_delete(entry):
                        archive_page(entry.page_id)
                        print(
                            f'  ✓ "{entry.header.strip()}" → deleted',
                        )
                    else:
                        print('  Cancelled.')
                case 'Move to Someday/Maybe':
                    props = build_property_update(status='Someday/Maybe')
                    update_page(entry.page_id, props)
                    print(f'  ✓ "{entry.header.strip()}" → Someday/Maybe')
                case _:
                    pass

            print()

        if updated:
            print(f'  {updated} project(s) updated\n')

    # Someday/Maybe review
    someday_pages = query_database(
        filter_obj={
            'property': 'Status',
            'select': {'equals': 'Someday/Maybe'},
        },
    )
    someday_entries = [ProjectEntry.from_page(p) for p in someday_pages]

    if not someday_entries:
        print('  No Someday/Maybe items.\n')
    else:
        summary_lines = [
            f'── Someday/Maybe ({len(someday_entries)} items) ──',
            '',
        ]
        for entry in someday_entries:
            ctx = f' [{entry.context}]' if entry.context else ''
            summary_lines.append(f'  • {entry.header.strip()}{ctx}')
        preview = _escape_for_shell('\n'.join(summary_lines))

        print(f'  {len(someday_entries)} Someday/Maybe item(s)\n')
        action = fzf_on_a_list(
            ['Review now', 'Skip for now'],
            prompt='Someday/Maybe',
            preview=f"echo '{preview}'",
        )
        if action == 'Review now':
            review_someday()
            print()


def _review_get_creative() -> None:
    """Phase 3: Get Creative — brain dump new ideas."""
    print('─── Phase 3: Get Creative ───')
    print('  Goal: Brain dump. Any new projects, ideas, or someday/maybes?\n')

    captured = 0
    while True:
        action = fzf_on_a_list(
            ['Capture an idea', 'Done brainstorming'],
            prompt='Brain dump',
        )
        if not action or action == 'Done brainstorming':
            break
        capture_item()
        captured += 1

    if captured:
        print(f'  ✓ Captured {captured} new item(s) → Triage\n')
    else:
        print('  No new items captured.\n')


def weekly_review() -> None:
    """Guided weekly review: Clear → Current → Creative."""
    print('\n══════════════════════════════════')
    print('       📋 GTD Weekly Review')
    print('══════════════════════════════════\n')

    _review_get_clear()
    pause('Press Enter to continue to next step...')
    _review_get_current()
    pause('Press Enter to continue to next step...')
    _review_get_creative()

    print('══════════════════════════════════')
    print('  ✓ Weekly review complete!')
    print('══════════════════════════════════\n')
