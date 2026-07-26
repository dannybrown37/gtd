#!/usr/bin/env python3
"""Update README.md menu section from cli.py source."""

import ast
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GTD_SOURCE = PROJECT_ROOT / 'src' / 'gtd' / 'cli.py'
README = PROJECT_ROOT / 'README.md'

BEGIN_MARKER = '<!-- BEGIN MENU -->'
END_MARKER = '<!-- END MENU -->'


def extract_menu_items() -> list[tuple[str, str]]:
    """Parse cli.py AST to extract menu_items list."""
    tree = ast.parse(GTD_SOURCE.read_text())

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


def format_menu_table(items: list[tuple[str, str]]) -> str:
    """Format menu items as a markdown table."""
    lines = ['| Category | Action |', '| --- | --- |']
    for cat, action in items:
        lines.append(f'| {cat} | {action} |')
    return '\n'.join(lines)


def patch_readme(menu_md: str) -> bool:
    """Replace content between markers in README. Returns True if changed."""
    content = README.read_text()
    pattern = re.compile(
        rf'({re.escape(BEGIN_MARKER)}\n).*?(\n{re.escape(END_MARKER)})',
        re.DOTALL,
    )
    match = pattern.search(content)
    if not match:
        msg = f'Markers {BEGIN_MARKER} / {END_MARKER} not found in README.md'
        raise RuntimeError(msg)

    new_content = pattern.sub(rf'\g<1>{menu_md}\g<2>', content)
    if new_content == content:
        return False

    README.write_text(new_content)
    return True


def main() -> None:
    items = extract_menu_items()
    table = format_menu_table(items)
    if patch_readme(table):
        print(f'✓ README.md updated ({len(items)} menu items)')
    else:
        print('README.md already up to date')


if __name__ == '__main__':
    main()
