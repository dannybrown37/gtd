"""Configuration management for GTD CLI."""

import json
from pathlib import Path


CONFIG_DIR = Path.home() / '.config' / 'gtd'
CONFIG_PATH = CONFIG_DIR / 'config.json'


def load_config() -> dict:
    """Load config from file, returning empty dict if missing."""
    if not CONFIG_PATH.exists():
        return {}
    return json.loads(CONFIG_PATH.read_text())


def save_config(config: dict) -> None:
    """Save config to file, creating directories as needed."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, indent=2) + '\n')


def get_config_value(key: str) -> str | None:
    """Get a single config value."""
    return load_config().get(key)
