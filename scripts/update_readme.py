#!/usr/bin/env python3
"""Update README.md menu and HTTP API sections from source."""

import ast
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CLI_SOURCE = PROJECT_ROOT / 'src' / 'gtd' / 'cli.py'
API_SOURCE = PROJECT_ROOT / 'src' / 'gtd' / 'api.py'
README = PROJECT_ROOT / 'README.md'

MENU_BEGIN = '<!-- BEGIN MENU -->'
MENU_END = '<!-- END MENU -->'
API_BEGIN = '<!-- BEGIN API MENU -->'
API_END = '<!-- END API MENU -->'

ROUTE_METHODS = {'get', 'post', 'put', 'patch', 'delete'}


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

    content = README.read_text()
    new_content = patch_section(
        content, MENU_BEGIN, MENU_END, format_menu_table(menu_items)
    )
    new_content = patch_section(
        new_content, API_BEGIN, API_END, format_api_table(api_routes)
    )

    if new_content == content:
        print('README.md already up to date')
        return

    README.write_text(new_content)
    print(
        f'✓ README.md updated ({len(menu_items)} menu items, '
        f'{len(api_routes)} API routes)'
    )


if __name__ == '__main__':
    main()
