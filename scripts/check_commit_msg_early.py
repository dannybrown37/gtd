"""Validate the commit message during the *pre-commit* stage, not commit-msg.

Git runs hooks in the order pre-commit -> prepare-commit-msg -> editor ->
commit-msg, so a `stages: [commit-msg]` commitizen hook can only ever reject
a bad message *after* the slow pre-commit hooks (tests, ruff, ...) have run.
No amount of hook ordering or `fail_fast` in .pre-commit-config.yaml changes
that -- the stages are separate git invocations.

When the message came from `git commit -m ...` (or `-F file`) it is already
sitting in git's argv while pre-commit runs, so we can fish it out of the
process tree and hand it to `cz check` immediately. Bad message => fail in
about a second instead of after the full suite.

If no message can be recovered (interactive editor, merge, rebase, ...) this
exits 0 and the real commit-msg hook does the checking as before.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

MAX_PARENT_DEPTH = 12
_SHORT_FLAG_MIN_LEN = 2


def _cmdline(pid: int) -> list[str] | None:
    try:
        raw = Path(f'/proc/{pid}/cmdline').read_bytes()
    except OSError:
        return None
    return [a for a in raw.decode('utf-8', 'replace').split('\0') if a]


def _ppid(pid: int) -> int | None:
    try:
        stat = Path(f'/proc/{pid}/stat').read_text()
    except OSError:
        return None
    # comm can contain spaces/parens, so parse after the final ')'.
    try:
        return int(stat[stat.rindex(')') + 1 :].split()[1])
    except (ValueError, IndexError):
        return None


def _find_git_commit_argv() -> list[str] | None:
    """Walk up process tree looking for the `git commit` that invoked us."""
    pid: int | None = os.getppid()
    for _ in range(MAX_PARENT_DEPTH):
        if pid is None or pid <= 1:
            return None
        argv = _cmdline(pid)
        if (
            argv
            and Path(argv[0]).name in {'git', 'git.exe'}
            and 'commit' in argv
        ):
            return argv
        pid = _ppid(pid)
    return None


def _parse_flag_value(
    arg: str, args: list[str], i: int
) -> tuple[str | None, bool, int]:
    """Parse a flag/value pair. Returns (value, is_file, next_index)."""
    value: str | None = None
    is_file = False
    if arg in {'-m', '--message'}:
        value = args[i + 1] if i + 1 < len(args) else None
        i += 1
    elif arg.startswith('--message='):
        value = arg.split('=', 1)[1]
    elif (
        arg.startswith('-m')
        and len(arg) > _SHORT_FLAG_MIN_LEN
        and not arg.startswith('--')
    ):
        value = arg[2:]
    elif arg in {'-F', '--file'}:
        value = args[i + 1] if i + 1 < len(args) else None
        is_file = True
        i += 1
    elif arg.startswith('--file='):
        value, is_file = arg.split('=', 1)[1], True
    elif (
        arg.startswith('-') and not arg.startswith('--') and arg.endswith('m')
    ):
        # Clustered short flags, e.g. `git commit -am "msg"`.
        value = args[i + 1] if i + 1 < len(args) else None
        i += 1
    return value, is_file, i


def _message_from_argv(argv: list[str]) -> str | None:
    """Extract the -m/-F message from a `git commit` argv, if there is one."""
    args = argv[argv.index('commit') + 1 :]
    parts: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == '--':
            break
        if arg in {'-e', '--edit'}:
            # Message will be edited after we run; let commit-msg judge it.
            return None
        value, is_file, i = _parse_flag_value(arg, args, i)
        if value is not None:
            if is_file:
                if value == '-':
                    return None
                try:
                    value = Path(value).read_text()
                except OSError:
                    return None
            parts.append(value)
        i += 1
    return '\n\n'.join(parts) if parts else None


def main() -> int:
    argv = _find_git_commit_argv()
    if argv is None:
        return 0
    message = _message_from_argv(argv)
    if message is None:
        return 0
    result = subprocess.run(  # noqa: S603
        ['uv', 'run', '--quiet', 'cz', 'check', '--message', message],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return 0
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    sys.stderr.write(
        '\nRejected before running the slow hooks. '
        'Fix the message and re-commit.\n'
    )
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
