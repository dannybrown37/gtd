"""Single source of the running version, read from installed package metadata.

Every surface (CLI `--version`, TUI header, webapp nav) reads this, so there
is no second copy of the number to forget to bump — `pyproject.toml` is
authoritative and commitizen bumps it.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as metadata_version

PACKAGE_NAME = 'gtd-tui'


def get_version() -> str:
    try:
        return metadata_version(PACKAGE_NAME)
    except PackageNotFoundError:
        return 'dev'
