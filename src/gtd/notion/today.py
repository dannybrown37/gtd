"""Today view and snooze commands."""

from datetime import datetime, timedelta


__all__ = ['list_today', 'snooze_today']

from gtd.notion.client import (
    archive_page,
    build_property_update,
    get_page_body,
    update_page,
)
from gtd.notion.entries import (
    _edit_entry_fields,
    _entry_preview_text,
    _escape_for_shell,
    _get_today_entries,
    _parse_date_input,
    select_entry,
)
from gtd.notion.log import _confirm_delete, _log_and_reschedule_entry
from gtd.ui import fzf_on_a_list, prompt_input


def list_today() -> None:  # noqa: C901, PLR0912
    """Interactive today view — pick items and take action."""
    actionable = _get_today_entries()

    if not actionable:
        print('\n  Nothing actionable today. Nice! 🎉\n')
        return

    while actionable:
        entry = select_entry(
            actionable,
            prompt='Today',
            include_body=True,
        )
        if not entry:
            break

        body = get_page_body(entry.page_id)
        preview = _escape_for_shell(_entry_preview_text(entry, body))

        action = fzf_on_a_list(
            [
                'Log & Reschedule',
                'Snooze until tomorrow',
                'Waiting For',
                'Update fields',
                'Mark done (moves to trash)',
                'Back to list',
            ],
            prompt=f'"{entry.header.strip()}"',
            preview=f"echo '{preview}'",
        )
        if not action or action == 'Back to list':
            continue

        tomorrow = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')

        match action:
            case 'Log & Reschedule':
                _log_and_reschedule_entry(entry)
                actionable = [
                    e for e in actionable if e.page_id != entry.page_id
                ]
            case 'Snooze until tomorrow':
                props = build_property_update(
                    follow_up_date=tomorrow,
                )
                update_page(entry.page_id, props)
                print(
                    f'  ✓ "{entry.header.strip()}" snoozed until {tomorrow}',
                )
                actionable = [
                    e for e in actionable if e.page_id != entry.page_id
                ]
            case 'Waiting For':
                waiting_on = prompt_input(
                    'Who/what are you waiting on? ',
                )
                if waiting_on:
                    followup = prompt_input(
                        'Follow-up date (e.g. Friday, in 3 days): ',
                    )
                    follow_date = (
                        _parse_date_input(followup) if followup else None
                    )
                    kwargs: dict = {
                        'status': 'Waiting For',
                        'next_step': waiting_on,
                    }
                    if follow_date:
                        kwargs['follow_up_date'] = follow_date
                    props = build_property_update(**kwargs)
                    update_page(entry.page_id, props)
                    print(
                        f'  ✓ "{entry.header.strip()}" → Waiting For',
                    )
                    actionable = [
                        e for e in actionable if e.page_id != entry.page_id
                    ]
            case 'Update fields':
                _edit_entry_fields(entry)
            case 'Mark done (moves to trash)':
                if _confirm_delete(entry):
                    archive_page(entry.page_id)
                    print(f'  ✓ "{entry.header.strip()}" → deleted')
                    actionable = [
                        e for e in actionable if e.page_id != entry.page_id
                    ]
                else:
                    print('  Cancelled.')


def snooze_today() -> None:
    """Snooze today's actionable items until tomorrow."""
    tomorrow = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
    actionable = _get_today_entries()

    if not actionable:
        print('Nothing to snooze — no actionable items today.')
        return

    headers = [e.header.strip() for e in actionable]
    selections = fzf_on_a_list(
        headers,
        multiple=True,
        prompt='Snooze until tomorrow',
    )
    if not selections:
        return

    selected_set = set(selections)
    count = 0
    for entry in actionable:
        if entry.header.strip() in selected_set:
            props = build_property_update(follow_up_date=tomorrow)
            update_page(entry.page_id, props)
            count += 1

    suffix = 's' if count != 1 else ''
    print(f'✓ Snoozed {count} item{suffix} until {tomorrow}')
