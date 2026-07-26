"""GTD CLI — David Allen's Getting Things Done powered by Notion."""

import shutil
import sys

import click

from gtd.ui import CancelAction


@click.group(invoke_without_command=True)
@click.option('-v', '--verbose', is_flag=True, help='Show details')
@click.pass_context
def cli(ctx: click.Context, verbose: bool) -> None:
    """gtd — Getting Things Done CLI powered by Notion."""
    ctx.ensure_object(dict)
    ctx.obj['verbose'] = verbose

    if ctx.invoked_subcommand is None:
        from gtd.gtd_tui import run_gtd_tui

        run_gtd_tui()


@cli.command()
@click.option(
    '--upgrade',
    is_flag=True,
    help='Upgrade existing database schema',
)
def init(upgrade: bool) -> None:
    """Set up or upgrade the GTD Notion database."""
    from gtd.notion.init import init_database

    try:
        init_database(upgrade=upgrade)
    except CancelAction:
        return


@cli.command()
def triage() -> None:
    """Interactively process items needing triage."""
    from gtd.notion.triage import (
        process_triage,
    )

    try:
        process_triage()
    except CancelAction:
        return


@cli.command(name='filter')
@click.argument('context', nargs=-1, required=True)
@click.pass_context
def filter_context(ctx: click.Context, context: tuple[str, ...]) -> None:
    """Filter by context name (e.g. gtd filter Phone)."""
    from gtd.notion.entries import (
        list_entries,
    )

    context_str = ' '.join(context)
    list_entries(
        context=context_str,
        verbose=ctx.obj.get('verbose', False),
    )


@cli.command()
def today() -> None:
    """Show actionable items for today."""
    from gtd.notion.today import (
        list_today,
    )

    list_today()


@cli.command()
def snooze() -> None:
    """Snooze today's items until tomorrow."""
    from gtd.notion.today import (
        snooze_today,
    )

    try:
        snooze_today()
    except CancelAction:
        return


@cli.command(name='log')
def log_cmd() -> None:
    """Log a note and reschedule a recurring item."""
    from gtd.notion.log import (
        log_and_reschedule,
    )

    try:
        log_and_reschedule()
    except CancelAction:
        return


@cli.command()
def done() -> None:
    """Mark a current project as done (archives it)."""
    from gtd.notion.commands import (
        mark_done,
    )

    try:
        mark_done()
    except CancelAction:
        return


@cli.command()
def review() -> None:
    """Run the GTD weekly review ritual."""
    from gtd.notion.review import (
        weekly_review,
    )

    try:
        weekly_review()
    except CancelAction:
        return


@cli.command()
def update() -> None:
    """Update fields on an existing project."""
    from gtd.notion.entries import (
        update_entry,
    )

    try:
        update_entry()
    except CancelAction:
        return


@cli.command()
def defer() -> None:
    """Defer a project by setting a follow-up date."""
    from gtd.notion.commands import (
        defer_entry,
    )

    try:
        defer_entry()
    except CancelAction:
        return


@cli.command()
def someday() -> None:
    """Review Someday/Maybe items — keep, activate, or drop."""
    from gtd.notion.review import (
        review_someday,
    )

    try:
        review_someday()
    except CancelAction:
        return


@cli.command()
@click.argument('header', nargs=-1)
def capture(header: tuple[str, ...]) -> None:
    """Quick-capture an item to the GTD inbox.

    Examples:
        gtd capture Buy groceries
        gtd capture               (interactive prompt)
    """
    from gtd.notion.capture import capture_item

    try:
        capture_item(header=' '.join(header) if header else None)
    except CancelAction:
        return


@cli.group(invoke_without_command=True)
@click.pass_context
def areas(ctx: click.Context) -> None:
    """Manage Horizons of Focus (Areas of Responsibility)."""
    if ctx.invoked_subcommand is None:
        from gtd.storage import load_areas

        area_list = load_areas()
        if not area_list:
            click.echo('No horizons defined. Use: gtd areas add "Health"')
            return
        for i, a in enumerate(area_list, 1):
            notes = f'  — {a["notes"]}' if a.get('notes') else ''
            click.echo(f'{i}. {a["name"]}{notes}')


@areas.command('add')
@click.argument('name')
@click.option('--notes', default='', help='Optional description or reminder')
def areas_add(name: str, notes: str) -> None:
    """Add a new Horizon of Focus."""
    from gtd.storage import load_areas, save_areas

    area_list = load_areas()
    if any(a['name'].lower() == name.lower() for a in area_list):
        click.echo(f'Area "{name}" already exists.')
        return
    area_list.append({'name': name, 'notes': notes})
    save_areas(area_list)
    click.echo(f'Added: {name}')


@areas.command('remove')
@click.argument('name')
def areas_remove(name: str) -> None:
    """Remove a Horizon of Focus."""
    from gtd.storage import load_areas, save_areas

    area_list = load_areas()
    updated = [a for a in area_list if a['name'].lower() != name.lower()]
    if len(updated) == len(area_list):
        click.echo(f'Area "{name}" not found.')
        return
    save_areas(updated)
    click.echo(f'Removed: {name}')


@areas.command('notes')
@click.argument('name')
@click.argument('notes')
def areas_notes(name: str, notes: str) -> None:
    """Update the notes/description for an area."""
    from gtd.storage import load_areas, save_areas

    area_list = load_areas()
    for a in area_list:
        if a['name'].lower() == name.lower():
            a['notes'] = notes
            save_areas(area_list)
            click.echo(f'Updated notes for: {a["name"]}')
            return
    click.echo(f'Area "{name}" not found.')


@cli.group(invoke_without_command=True)
@click.pass_context
def contexts(ctx: click.Context) -> None:
    """Manage GTD contexts (Computer, Home, Phone, etc.)."""
    if ctx.invoked_subcommand is None:
        from gtd.notion.client import get_contexts

        context_list = sorted(get_contexts())
        if not context_list:
            click.echo('No contexts defined. Use: gtd contexts add "Home"')
            return
        for i, c in enumerate(context_list, 1):
            click.echo(f'{i}. {c}')


@contexts.command('add')
@click.argument('name')
def contexts_add(name: str) -> None:
    """Add a new context."""
    from gtd.notion.client import add_context, get_contexts

    if name in get_contexts():
        click.echo(f'Context "{name}" already exists.')
        return
    add_context(name)
    click.echo(f'Added: {name}')


@contexts.command('remove')
@click.argument('name')
def contexts_remove(name: str) -> None:
    """Remove a context."""
    from gtd.notion.client import (
        get_contexts,
        remove_context,
    )

    if name not in get_contexts():
        click.echo(f'Context "{name}" not found.')
        return
    remove_context(name)
    click.echo(f'Removed: {name}')


@contexts.command('rename')
@click.argument('old_name')
@click.argument('new_name')
def contexts_rename(old_name: str, new_name: str) -> None:
    """Rename a context and update all items with that context."""
    from gtd.notion.client import (
        get_contexts,
        query_database,
        rename_context,
    )

    if old_name not in get_contexts():
        click.echo(f'Context "{old_name}" not found.')
        return
    if new_name in get_contexts():
        click.echo(f'Context "{new_name}" already exists.')
        return

    # Show how many items will be updated
    pages = query_database(
        filter_obj={'property': 'Context', 'select': {'equals': old_name}}
    )
    item_count = len(pages)

    rename_context(old_name, new_name)
    if item_count:
        click.echo(
            f'Renamed: {old_name} → {new_name} ({item_count} item'
            f'{"s" if item_count != 1 else ""} updated)'
        )
    else:
        click.echo(f'Renamed: {old_name} → {new_name}')


@cli.command()
def dump() -> None:
    """Rapid-fire brain dump — capture everything, triage later."""
    from gtd.notion.review import brain_dump

    try:
        brain_dump()
    except CancelAction:
        return


def _interactive_menu(verbose: bool) -> None:  # noqa: C901, PLR0912, PLR0915
    """Launch interactive fzf menu for GTD actions."""
    from gtd.notion.commands import (
        defer_entry,
        mark_done,
        set_waiting_for,
    )
    from gtd.notion.entries import (
        list_entries,
        update_entry,
    )
    from gtd.notion.log import log_and_reschedule
    from gtd.notion.review import (
        brain_dump,
        review_someday,
        weekly_review,
    )
    from gtd.notion.today import (
        list_today,
        snooze_today,
    )
    from gtd.notion.capture import (
        capture_item,
    )
    from gtd.notion.triage import (
        process_triage,
    )
    from gtd.ui import (
        fzf_on_a_list,
        pause,
    )

    if shutil.which('fzf') is None:
        click.echo(
            'Error: fzf is required but not found on PATH.\n'
            'Install it: https://github.com/junegunn/fzf#installation',
        )
        sys.exit(1)

    menu_items = [
        ('Do', 'Today'),
        ('Do', 'Log & Reschedule'),
        ('Do', 'Snooze until tomorrow'),
        ('Do', 'Capture new item'),
        ('Do', 'Brain dump'),
        ('Do', 'Triage inbox'),
        ('Manage', 'Update project'),
        ('Manage', 'Defer project until date'),
        ('Manage', 'Waiting For'),
        ('Manage', 'Mark done (delete)'),
        ('Review', 'Weekly Review'),
        ('Review', 'Review Someday/Maybe'),
        ('View', 'View all projects'),
        ('View', 'Filter by context'),
    ]

    labels = [
        f'{i + 1:>2}. {cat:<10}{action}'
        for i, (cat, action) in enumerate(menu_items)
    ]
    label_to_action = {
        label.strip(): action
        for label, (_, action) in zip(
            labels,
            menu_items,
            strict=True,
        )
    }

    while True:
        try:
            selection = fzf_on_a_list(labels, prompt='GTD')
        except CancelAction:
            break
        if not selection:
            break

        action = label_to_action.get(selection)
        if not action:
            continue

        try:
            match action:
                case 'Today':
                    list_today()
                case 'Log & Reschedule':
                    log_and_reschedule()
                case 'Snooze until tomorrow':
                    snooze_today()
                case 'View all projects':
                    list_entries(verbose=verbose)
                    pause()
                case 'Triage inbox':
                    process_triage()
                case 'Capture new item':
                    capture_item()
                case 'Brain dump':
                    brain_dump()
                case 'Update project':
                    update_entry()
                case 'Defer project until date':
                    defer_entry()
                case 'Waiting For':
                    set_waiting_for()
                case 'Mark done (delete)':
                    mark_done()
                case 'Weekly Review':
                    weekly_review()
                case 'Review Someday/Maybe':
                    review_someday()
                case 'Filter by context':
                    from gtd.notion.client import (
                        get_select_options,
                    )

                    contexts = get_select_options('Context')
                    ctx = fzf_on_a_list(contexts, prompt='Context')
                    if ctx:
                        list_entries(context=ctx, verbose=verbose)
                        pause()
        except CancelAction:
            continue


@cli.group(invoke_without_command=True)
@click.pass_context
def config(ctx: click.Context) -> None:
    """View or set GTD configuration."""
    if ctx.invoked_subcommand is None:
        from gtd.notion.config import load_config

        cfg = load_config()
        if not cfg:
            click.echo('No configuration set.')
            return
        for k, v in cfg.items():
            click.echo(f'{k}: {v}')


@config.command('notes-editor')
@click.argument('mode', type=click.Choice(['inline', 'external']))
def config_notes_editor(mode: str) -> None:
    """Set notes editor: inline (TUI TextArea) or external (uses $EDITOR)."""
    from gtd.notion.config import load_config, save_config

    cfg = load_config()
    cfg['notes_editor'] = mode
    save_config(cfg)
    click.echo(f'notes_editor → {mode}')

    """Launch the legacy fzf-based interactive GTD menu."""
    _interactive_menu(verbose=False)


@cli.command()
def tui() -> None:
    """Launch the interactive GTD TUI."""
    from gtd.gtd_tui import run_gtd_tui

    run_gtd_tui()


@cli.command()
@click.option('--host', default='0.0.0.0', show_default=True)  # noqa: S104
@click.option('--port', default=8000, show_default=True)
@click.option('--reload', is_flag=True, help='Auto-reload (dev only)')
def api(host: str, port: int, reload: bool) -> None:
    """Start the GTD HTTP API server (requires: pip install gtd[api])."""
    try:
        import uvicorn
    except ImportError:
        click.echo(
            'uvicorn not installed. Run: uv pip install "gtd[api]"', err=True
        )
        raise SystemExit(1)  # noqa: B904
    uvicorn.run('gtd.api:app', host=host, port=port, reload=reload)


def main() -> None:
    """Entry point for gtd command."""
    cli()
