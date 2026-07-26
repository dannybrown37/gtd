# Project Manager

A CLI for personal productivity built around [GTD (Getting Things Done)](https://gettingthingsdone.com/).

## What it does

- Capture items to an inbox and triage them into projects
- Track projects with contexts, next actions, and follow-up dates
- Log completions and auto-reschedule recurring items
- Defer, snooze, and review Someday/Maybe lists
- Filter by context for focused work sessions

## Requirements

- Python 3.12+
- [fzf](https://github.com/junegunn/fzf) (for interactive menus)
- A [Notion integration token](https://developers.notion.com/) (for `gtd`)

## Installation

```bash
cd gtd
uv sync
uv pip install -e .
```

This installs the `gtd` command.

## Usage

### GTD interactive mode

```bash
gtd
```

Opens the fzf-powered GTD menu:

<!-- BEGIN MENU -->
| Category | Action |
| --- | --- |
| Do | Today |
| Do | Log & Reschedule |
| Do | Snooze until tomorrow |
| Do | Capture new item |
| Do | Brain dump |
| Do | Triage inbox |
| Manage | Update project |
| Manage | Defer project until date |
| Manage | Waiting For |
| Manage | Mark done (delete) |
| Review | Weekly Review |
| Review | Review Someday/Maybe |
| View | View all projects |
| View | Filter by context |
<!-- END MENU -->

### GTD subcommands

```bash
gtd init             # Set up a new GTD Notion database
gtd init --upgrade   # Add missing properties/options to existing DB
gtd triage           # Process inbox items
gtd review           # Guided weekly review ritual
gtd dump             # Rapid-fire brain dump
gtd filter work      # Filter projects by context
gtd today            # Show today's actionable items
gtd capture          # Quick-capture to inbox
gtd done             # Mark a project as done
gtd update           # Update project fields
gtd defer            # Defer a project
```

## Updating this README

Menu options are extracted from source. After changing menu items in `gtd.py`:

```bash
python scripts/update_readme.py
```

## Data storage

- **GTD**: Notion database (configured via `gtd init` or `NOTION_NOTES_TOKEN` / `NOTION_PROJECTS_DB_ID` env vars)
- **Weekly review state, Areas of Focus, list categories**: JSON files in `~/.local/share/gtd/`

## Project structure

```
src/gtd/
├── gtd.py           # GTD interactive menu and CLI commands
├── gtd_tui.py       # Textual TUI
├── storage.py       # File I/O and path management
├── ui.py            # fzf helpers, prompts, formatting
└── notion/
    ├── client.py    # Notion API client
    ├── config.py    # Config file management (~/.config/gtd/)
    ├── schema.py    # Database schema definition
    ├── init.py      # Database creation and upgrades
    ├── models.py    # ProjectEntry dataclass
    ├── commands.py  # GTD command implementations
    ├── capture.py   # Inbox capture
    ├── triage.py    # Triage processing
    └── display.py   # Entry formatting
scripts/
└── update_readme.py # Auto-update README menu section
```
