from pathlib import Path

import pytest
from click.testing import CliRunner

from gtd import storage
from gtd.cli import cli


@pytest.fixture(autouse=True)
def _isolated_areas(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(storage, 'AREAS_PATH', tmp_path / 'areas.json')


def invoke(*args: str) -> object:
    return CliRunner().invoke(cli, ['areas', *args])


class TestAreasList:
    def test_no_areas_shows_message(self) -> None:
        result = invoke()
        assert 'No horizons defined' in result.output

    def test_lists_areas_with_name(self) -> None:
        invoke('add', 'Health')
        result = invoke()
        assert 'Health' in result.output

    def test_lists_notes_when_present(self) -> None:
        invoke('add', 'Health', '--notes', 'exercise daily')
        result = invoke()
        assert 'exercise daily' in result.output


class TestAreasAdd:
    def test_add_creates_area(self) -> None:
        result = invoke('add', 'Health')
        assert result.exit_code == 0
        assert 'Added' in result.output

    def test_add_duplicate_shows_error(self) -> None:
        invoke('add', 'Health')
        result = invoke('add', 'Health')
        assert 'already exists' in result.output

    def test_add_duplicate_does_not_create_second_entry(self) -> None:
        invoke('add', 'Health')
        invoke('add', 'Health')
        areas = storage.load_areas()
        assert len([a for a in areas if a['name'] == 'Health']) == 1

    def test_add_is_case_insensitive_duplicate_check(self) -> None:
        invoke('add', 'Health')
        result = invoke('add', 'health')
        assert 'already exists' in result.output


class TestAreasRemove:
    def test_remove_deletes_area(self) -> None:
        invoke('add', 'Health')
        result = invoke('remove', 'Health')
        assert 'Removed' in result.output
        assert storage.load_areas() == []

    def test_remove_missing_shows_error(self) -> None:
        result = invoke('remove', 'Missing')
        assert 'not found' in result.output

    def test_remove_is_case_insensitive(self) -> None:
        invoke('add', 'Health')
        result = invoke('remove', 'health')
        assert 'Removed' in result.output


class TestAreasNotes:
    def test_notes_updates_field(self) -> None:
        invoke('add', 'Health')
        result = invoke('notes', 'Health', 'some notes')
        assert result.exit_code == 0
        areas = storage.load_areas()
        assert areas[0]['notes'] == 'some notes'

    def test_notes_missing_area_shows_error(self) -> None:
        result = invoke('notes', 'Health', 'some notes')
        assert 'not found' in result.output
