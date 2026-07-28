#!/usr/bin/env python3
"""Update README.md menu, CLI, tree, and HTTP API sections from source."""

import ast
import re
from pathlib import Path

import click

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CLI_SOURCE = PROJECT_ROOT / 'src' / 'gtd' / 'cli.py'
API_SOURCE = PROJECT_ROOT / 'src' / 'gtd' / 'api.py'
README = PROJECT_ROOT / 'README.md'

MENU_BEGIN = '<!-- BEGIN MENU -->'
MENU_END = '<!-- END MENU -->'
API_BEGIN = '<!-- BEGIN API MENU -->'
API_END = '<!-- END API MENU -->'
CLI_BEGIN = '<!-- BEGIN CLI -->'
CLI_END = '<!-- END CLI -->'
TREE_BEGIN = '<!-- BEGIN TREE -->'
TREE_END = '<!-- END TREE -->'

ROUTE_METHODS = {'get', 'post', 'put', 'patch', 'delete'}
TREE_ROOTS = ['src/gtd', 'scripts']
TREE_EXCLUDE = {'__pycache__', '__init__.py'}


def extract_menu_items() -> list[tuple[str, str]]:
    """Parse cli.py AST to extract menu_items list."""
    tree = ast.parse(CLI_SOURCE.read_text())

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == 'menu_items':
                items = []
                assert isinstance(node.value, ast.List)
                for elt in node.value.elts:
                    assert isinstance(elt, ast.Tuple)
                    cat = elt.elts[0]
                    action = elt.elts[1]
                    assert isinstance(cat, ast.Constant)
                    assert isinstance(action, ast.Constant)
                    items.append((cat.value, action.value))
                return items

    msg = 'Could not find menu_items in cli.py'
    raise RuntimeError(msg)


def extract_api_routes() -> list[tuple[str, str, str]]:
    """Parse api.py AST for Flask routes.

    Returns a list of (method, path, description) tuples.
    """
    tree = ast.parse(API_SOURCE.read_text())
    routes = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            if not (
                isinstance(dec.func, ast.Attribute)
                and isinstance(dec.func.value, ast.Name)
                and dec.func.value.id == 'app'
                and dec.func.attr in ROUTE_METHODS
            ):
                continue
            if not (dec.args and isinstance(dec.args[0], ast.Constant)):
                continue
            method = dec.func.attr.upper()
            path = dec.args[0].value
            docstring = ast.get_docstring(node) or ''
            description = docstring.strip().split('\n')[0]
            routes.append((method, path, description))

    routes.sort(key=lambda r: r[1])
    return routes


def extract_cli_commands() -> list[tuple[str, str]]:
    """Walk the live click CLI group for command paths + short help."""
    import sys

    sys.path.insert(0, str(PROJECT_ROOT / 'src'))
    from gtd.cli import cli as cli_group

    rows: list[tuple[str, str]] = []

    def walk(group: click.Group, prefix: str) -> None:
        for name, cmd in group.commands.items():
            full = f'{prefix} {name}'
            help_text = (cmd.help or '').strip().split('\n')[0]
            rows.append((full, help_text))
            if isinstance(cmd, click.Group):
                walk(cmd, full)

    walk(cli_group, 'gtd')
    return rows


def module_docstring(path: Path) -> str:
    """First line of a Python file's module docstring, or ''."""
    doc = ast.get_docstring(ast.parse(path.read_text())) or ''
    return doc.strip().split('\n')[0]


def build_tree(base: Path, prefix: str = '') -> list[tuple[str, str]]:
    """Recursively build (rendered_line, docstring) pairs for a directory."""
    entries = sorted(p for p in base.iterdir() if p.name not in TREE_EXCLUDE)
    files = [p for p in entries if p.is_file() and p.suffix == '.py']
    dirs = [p for p in entries if p.is_dir() and p.name != '__pycache__']
    ordered = files + dirs

    lines: list[tuple[str, str]] = []
    for i, p in enumerate(ordered):
        last = i == len(ordered) - 1
        connector = '└── ' if last else '├── '
        if p.is_dir():
            lines.append((f'{prefix}{connector}{p.name}/', ''))
            extension = '    ' if last else '│   '
            lines.extend(build_tree(p, prefix + extension))
        else:
            lines.append((f'{prefix}{connector}{p.name}', module_docstring(p)))
    return lines


def format_project_tree() -> str:
    """Render TREE_ROOTS as aligned, commented ASCII trees."""
    blocks: list[str] = []
    for root in TREE_ROOTS:
        base = PROJECT_ROOT / root
        entries = build_tree(base)
        width = max((len(line) for line, _doc in entries), default=0)
        rendered = [f'{root}/']
        for line, doc in entries:
            rendered.append(f'{line.ljust(width)} # {doc}' if doc else line)
        blocks.append('\n'.join(rendered))
    return '\n'.join(blocks)


def format_cli_table(rows: list[tuple[str, str]]) -> str:
    """Format CLI command rows as a markdown table."""
    lines = ['| Command | Description |', '| --- | --- |']
    for cmd, desc in rows:
        lines.append(f'| `{cmd}` | {desc} |')
    return '\n'.join(lines)


def format_menu_table(items: list[tuple[str, str]]) -> str:
    """Format menu items as a markdown table."""
    lines = ['| Category | Action |', '| --- | --- |']
    for cat, action in items:
        lines.append(f'| {cat} | {action} |')
    return '\n'.join(lines)


def format_api_table(routes: list[tuple[str, str, str]]) -> str:
    """Format API routes as a markdown table."""
    lines = ['| Method | Path | Description |', '| --- | --- | --- |']
    for method, path, description in routes:
        lines.append(f'| `{method}` | `{path}` | {description} |')
    return '\n'.join(lines)


def patch_section(content: str, begin: str, end: str, body_md: str) -> str:
    """Replace content between markers. Returns the patched content."""
    pattern = re.compile(
        rf'({re.escape(begin)}\n).*?(\n{re.escape(end)})',
        re.DOTALL,
    )
    if not pattern.search(content):
        msg = f'Markers {begin} / {end} not found in README.md'
        raise RuntimeError(msg)
    return pattern.sub(rf'\g<1>{body_md}\g<2>', content)


def main() -> None:
    menu_items = extract_menu_items()
    api_routes = extract_api_routes()
    cli_commands = extract_cli_commands()

    content = README.read_text()
    new_content = patch_section(
        content, MENU_BEGIN, MENU_END, format_menu_table(menu_items)
    )
    new_content = patch_section(
        new_content, API_BEGIN, API_END, format_api_table(api_routes)
    )
    new_content = patch_section(
        new_content, CLI_BEGIN, CLI_END, format_cli_table(cli_commands)
    )
    new_content = patch_section(
        new_content,
        TREE_BEGIN,
        TREE_END,
        f'```\n{format_project_tree()}\n```',
    )

    if new_content == content:
        print('README.md already up to date')
        return

    README.write_text(new_content)
    print(
        f'✓ README.md updated ({len(menu_items)} menu items, '
        f'{len(api_routes)} API routes, {len(cli_commands)} CLI commands)'
    )


if __name__ == '__main__':
    main()
