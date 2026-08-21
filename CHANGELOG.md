## v0.11.0 (2026-08-21)

### Feat

- **tui**: add Y shortcut to copy the title and notes for an entry

### Fix

- web app reloads schema after schema changes

## v0.10.10 (2026-08-21)

### Fix

- on web app (where there is a dedicated view), don't show weekly review on next steps page when it's not due

## v0.10.9 (2026-08-17)

### Fix

- udpate past change logs, explicitly ignore some patterns in gitignore

## v0.10.8 (2026-08-16)

### Fix

- selection on context filter

## v0.10.7 (2026-08-16)

### Fix

- add context selection on webapp projects view

## v0.10.6 (2026-08-15)

### Fix

- PWA needs to handle recurring items 'done'ness

## v0.10.5 (2026-08-15)

### Fix

- add 'next Monday' as snooze option on web app

## v0.10.4 (2026-08-15)

### Fix

- better nav on web app, use thumb panel switcher instead of hamburger

## v0.10.3 (2026-08-15)

### Fix

- mypy pre-commit and fixes
- add mypy pre-commit

## v0.10.2 (2026-08-14)

### Fix

- add conftest that blocks network calls in tests explicitly

## v0.10.1 (2026-08-14)

### Fix

- issue with marking weekly review steps done

## v0.10.0 (2026-08-13)

### Feat

- weekly review in the webapp

## v0.9.0 (2026-08-13)

### Feat

- add weekly review to the API

### Fix

- remove 12wy remnant of 'log and reschedule' from CLI, rescheudle only now

## v0.8.7 (2026-08-12)

### Fix

- notion query was wrong

## v0.8.6 (2026-08-12)

### Fix

- remove eastern time zone on API, just use system time
- Waiting For items *always* need a follow-up date lest they get lost; default to 1 week

## v0.8.5 (2026-08-11)

### Fix

- add version display in web app and TUI

## v0.8.4 (2026-08-11)

### Fix

- many bugs and edge cases, large session here
- surface due dates over follow-up dates

## v0.8.3 (2026-08-11)

### Fix

- update how list work on web app (more controls)

## v0.8.2 (2026-08-11)

### Fix

- make webapp handle Someday / Maybe + Areas special casing

## v0.8.1 (2026-08-11)

### Fix

- CI needs --refresh-package flag

## v0.8.0 (2026-08-11)

### Feat

- add a reload button to the webapp

## v0.7.4 (2026-08-11)

### Fix

- someday/maybe triage asks for area and nothing else

## v0.7.3 (2026-08-07)

### Fix

- attempt to git bitwarden key selection working

## v0.7.2 (2026-08-07)

### Fix

- offer password manager on key entry

## v0.7.1 (2026-08-07)

### Fix

- documentation of tailnet

## v0.7.0 (2026-08-06)

### Feat

- really build out the @Person abstractions -- very useful but maybe too obscure, time will tell

### Fix

- web/phone layout improvements
- remove dead code; simplify process of repeating recurring items

## v0.6.0 (2026-08-05)

### Feat

- add terraform for deploying the tailscaled webapp through Oracle Cloud free tier

## v0.5.1 (2026-08-05)

### Fix

- add --version flag, bundle web app files with gtd[api] install

## v0.5.0 (2026-08-01)

### Feat

- introduce an @Person agenda system
- first attempt to add a webapp

## v0.3.1 (2026-07-30)

### Fix

- update docs for factual information

## v0.3.0 (2026-07-30)

### Feat

- add script to seed a demo database and seed with  sample data + another to automate taking screenshots of TUI

### Fix

- redo screenshots / docs given latest change
- Today tab elimated, was too duplicative with Next Steps

## v0.2.2 (2026-07-30)

### Fix

- /contexts needs to return active recurring tasks; DRY up some code

## v0.2.1 (2026-07-29)

### Fix

- next steps query bug

## v0.2.0 (2026-07-29)

### Feat

- Someday/Maybe gets Areas rather than Context; store areas in Notion rather than locally
- add fun celebration modals for weekly review, keep review visible after complete with when

### Fix

- failing test from CI
- remove shebang that made CI complain (todo maybe: why didn't local complain with same check?)
- continue improving weekly review flow
- keep weekly review visible with last date completed even after completion; remove Today tab divider line
- visual bug where when reduced down to one item in a list (usually inbox), it won't visually select that item

## v0.1.1 (2026-07-28)

### Fix

- make sure pyproject.toml version number gets updated
- auto-document file tree and available commands in README.md

## v0.1.0 (2026-07-28)

### Feat

- first pypi publish
- debug bump ci action
- add commitizen/pre-commit commit message enforcement/pypi publishing

### Fix

- revert premature 0.1.0 bump for clean first release
- use BUMP_TOKEN instead of GITHUB_TOKEN for publish step
- add default_stage
- use uv to bump with cz
- debug ci
