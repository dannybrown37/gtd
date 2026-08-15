"""Interactive triage flow for processing inbox items."""

from dateutil import parser as dateparser

from gtd.notion.client import (
    archive_page,
    build_property_update,
    get_list_categories,
    get_page_body,
    get_select_options,
    update_page,
)
from gtd.notion.entries import (
    _entry_preview_text,
    _escape_for_shell,
    select_entry,
)
from gtd.notion.models import ProjectEntry
from gtd.notion.schema import STATUSES as ALL_STATUSES
from gtd.notion.schema import (
    AGENDA_STATUS,
    WAITING_FOR_STATUS,
    agenda_person_from_header,
    default_waiting_for_follow_up,
    is_agenda_context,
)
from gtd.notion.views import inbox_entries
from gtd.ui import CancelAction, fzf_on_a_list, prompt_input


TRIAGE_STATUSES = [s for s in ALL_STATUSES if s != 'Triage'] + ['Delete']


def _process_single_entry(entry: ProjectEntry) -> bool:  # noqa: C901, PLR0911, PLR0912, PLR0915
    """Process one triage item. Returns True if processed, False if skipped."""
    body = get_page_body(entry.page_id)
    preview = _escape_for_shell(_entry_preview_text(entry, body))

    agenda_person = agenda_person_from_header(entry.header)

    # Status -- agenda items are always Current Project, so don't ask.
    status: str | None
    if agenda_person:
        status = AGENDA_STATUS
    else:
        status = fzf_on_a_list(
            TRIAGE_STATUSES,
            prompt=f'"{entry.header}" → Status',
            preview=f"echo '{preview}'",
        )
        if not status:
            return False

        if status == 'Delete':
            confirm = prompt_input(
                f'  Delete "{entry.header.strip()}"? (y/N): '
            )
            if confirm and confirm.lower() == 'y':
                archive_page(entry.page_id)
                print(f'  ✓ "{entry.header.strip()}" deleted')
                return True
            print('  Cancelled.')
            return False

    # Someday/Maybe is organised by Area, not Context -- a parked idea has
    # no next action, so nothing else in this flow applies to it either.
    if status == 'Someday/Maybe':
        from gtd.notion.client import get_areas

        area = entry.area or None
        if not area:
            area = fzf_on_a_list(
                sorted(get_areas()),
                prompt=f'"{entry.header}" → Area',
            )
        update_page(
            entry.page_id,
            build_property_update(status=status, area=area or None),
        )
        print(f'  ✓ "{entry.header}" → {status} [{area or "no area"}]')
        return True

    # Context (skip for List items)
    context = None
    if status != 'List' and agenda_person:
        # The header already named the person -- don't ask again. Creating
        # the select option is idempotent, so a known person is a no-op.
        from gtd.notion.client import add_context

        add_context(agenda_person)
        context = agenda_person
    elif status != 'List':
        from gtd.notion.client import (
            add_context,
            remove_context,
        )

        contexts = sorted(get_select_options('Context'))
        while True:
            context = fzf_on_a_list(
                [*contexts, '[+ Add new]', '[- Remove]'],
                prompt=f'"{entry.header}" → Context',
            )
            if not context:
                return False
            if context == '[+ Add new]':
                new_ctx = prompt_input('New context name: ')
                if new_ctx:
                    add_context(new_ctx)
                    contexts = sorted(get_select_options('Context'))
                continue
            if context == '[- Remove]':
                remove_opts = sorted(get_select_options('Context'))
                remove_ctx = fzf_on_a_list(
                    remove_opts,
                    prompt='Remove context',
                )
                if remove_ctx:
                    remove_context(remove_ctx)
                    contexts = sorted(get_select_options('Context'))
                continue
            break

    # List Category (for List status)
    list_category = None
    if status == 'List':
        list_category = entry.list_category or None
        if not list_category:
            categories = get_list_categories()
            list_category = fzf_on_a_list(
                categories,
                prompt=f'"{entry.header}" → List Category',
            )
            if not list_category:
                return False

    # Next Actionable Step
    next_step = None
    success_condition = None
    if status != 'List' and not (agenda_person or is_agenda_context(context)):
        if status == 'Waiting For':
            next_step = prompt_input(
                'Who/what are you waiting on? ',
            )
        else:
            next_step = prompt_input('Next actionable step: ')
        if next_step is None:
            return False

        # Success Condition
        success_condition = prompt_input(
            'Success condition (what does done look like?): ',
        )
        if success_condition is None:
            return False

    # Optional Due Date
    due_date_input = prompt_input(
        'Due date (e.g. Jul 15, 2026-08-01, blank to skip): ',
    )
    due_iso: str | None = None
    if due_date_input:
        parsed = dateparser.parse(due_date_input, fuzzy=True)
        if parsed:
            due_iso = parsed.strftime('%Y-%m-%d')
        else:
            print(f'  Could not parse "{due_date_input}", skipping due date.')

    # Follow-Up Date (required for Waiting For)
    if status == WAITING_FOR_STATUS:
        default = default_waiting_for_follow_up()
        follow_up_input = prompt_input(
            f'Follow-up date (blank for {default}): ',
        )
    else:
        follow_up_input = prompt_input(
            'Follow-up date (blank to skip): ',
        )
    follow_up_iso: str | None = None
    if follow_up_input:
        parsed = dateparser.parse(follow_up_input, fuzzy=True)
        if parsed:
            follow_up_iso = parsed.strftime('%Y-%m-%d')
        else:
            print(
                f'  Could not parse "{follow_up_input}", '
                f'skipping follow-up date.',
            )
    elif status == WAITING_FOR_STATUS:
        follow_up_iso = default_waiting_for_follow_up()
        print(f'  Follow-up date defaulted to {follow_up_iso}.')

    # Build and send update
    props = build_property_update(
        status=status,
        context=context,
        list_category=list_category,
        next_step=next_step or None,
        success_condition=success_condition or None,
        due_date=due_iso,
        follow_up_date=follow_up_iso,
    )
    update_page(entry.page_id, props)
    print(f'  ✓ "{entry.header}" → {status} [{context}]')
    return True


def process_triage() -> None:
    """Interactive triage processing flow."""
    entries = inbox_entries()
    if not entries:
        print('No items in Triage. Inbox zero! 🎉')
        return

    print(
        f'\n  {len(entries)} item{"s" if len(entries) != 1 else ""}'
        f' in Triage\n'
    )

    if len(entries) == 1:
        items_to_process = entries
    else:
        process_all = '▶ Process all in order'
        choices = [process_all, 'Pick one']
        choice = fzf_on_a_list(choices, prompt='Triage')
        if not choice:
            return
        if choice == process_all:
            items_to_process = entries
        else:
            entry = select_entry(entries, prompt='Triage')
            if not entry:
                return
            items_to_process = [entry]

    processed = 0
    for entry in items_to_process:
        try:
            if _process_single_entry(entry):
                processed += 1
        except CancelAction:
            break

    if processed:
        suffix = 's' if processed != 1 else ''
        print(f'\n  ✓ Processed {processed} item{suffix}')
