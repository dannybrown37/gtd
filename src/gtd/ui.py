import subprocess
from typing import Literal, overload

__all__ = [
    'CancelAction',
    'fzf_on_a_list',
    'pause',
    'prompt_input',
]


FZF_CTRL_C_CODE = 130


@overload
def fzf_on_a_list(
    items: list[str],
    *,
    multiple: Literal[True],
    prompt: str = '',
    preview: str | None = None,
) -> list[str] | None: ...


@overload
def fzf_on_a_list(
    items: list[str],
    *,
    multiple: Literal[False] = False,
    prompt: str = '',
    preview: str | None = None,
) -> str | None: ...


def fzf_on_a_list(
    items: list[str],
    *,
    multiple: bool = False,
    prompt: str = '',
    preview: str | None = None,
) -> str | list[str] | None:
    """Run fzf on a list of strings."""
    prompt = f'{prompt}: ' if prompt and not prompt.endswith(': ') else prompt
    if multiple:
        cmd = [
            'fzf',
            '-m',
            '--prompt',
            f'{prompt}Shift+Tab to unselect > ',
        ]
    else:
        cmd = ['fzf', '--prompt', prompt]
    if preview is not None:
        cmd.extend(['--preview', preview, '--preview-window', 'up:wrap'])
    result = subprocess.run(  # noqa: S603
        cmd,
        input='\n'.join(items),
        stdout=subprocess.PIPE,
        text=True,
        check=False,
    )
    if result.returncode == FZF_CTRL_C_CODE:
        raise CancelAction
    if not multiple:
        return result.stdout.strip() or None
    return [item.strip() for item in result.stdout.split('\n') if item.strip()]


def pause(label: str = 'Press Enter to go back to menu...') -> None:
    try:
        input(f'\n{label}')
    except KeyboardInterrupt:
        print()


class CancelAction(Exception):  # noqa: N818
    """Raised when user presses Ctrl+C to abort the current action."""


def prompt_input(label: str) -> str | None:
    """Like input() but Ctrl+C raises CancelAction to return to menu."""
    try:
        return input(label).strip()
    except KeyboardInterrupt:
        print()
        raise CancelAction from None
