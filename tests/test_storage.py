import json
from datetime import datetime
from pathlib import Path

import pytest

from gtd import storage
from gtd.storage import (
    get_weekly_habit_date,
    load_areas,
    load_review_state,
    reset_review_state,
    save_areas,
    save_review_state,
    set_weekly_habit_date,
)


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect all storage paths to a temp directory."""
    monkeypatch.setattr(storage, 'OUTPUT_PATH', tmp_path)
    monkeypatch.setattr(storage, 'CONFIG_PATH', tmp_path / 'config.json')
    monkeypatch.setattr(
        storage, 'HABITS_PATH', tmp_path / 'weekly_habits.json'
    )
    monkeypatch.setattr(storage, 'AREAS_PATH', tmp_path / 'areas.json')
    (tmp_path / 'archive').mkdir()


class TestWeeklyHabitDate:
    def test_returns_none_when_file_missing(self) -> None:
        assert get_weekly_habit_date('weekly_review') is None

    def test_returns_none_for_unknown_key(self, tmp_path: Path) -> None:
        (tmp_path / 'weekly_habits.json').write_text(
            '{"other_habit": "2026-01-01"}'
        )
        assert get_weekly_habit_date('weekly_review') is None

    def test_set_writes_today(self) -> None:
        set_weekly_habit_date('weekly_review')
        result = get_weekly_habit_date('weekly_review')
        assert result == datetime.now().date().isoformat()

    def test_set_preserves_other_keys(self) -> None:
        set_weekly_habit_date('other_habit')
        set_weekly_habit_date('weekly_review')
        assert (
            get_weekly_habit_date('other_habit')
            == datetime.now().date().isoformat()
        )
        assert (
            get_weekly_habit_date('weekly_review')
            == datetime.now().date().isoformat()
        )

    def test_set_updates_existing_key(self) -> None:
        storage.HABITS_PATH.write_text(
            json.dumps({'weekly_review': '2020-01-01'}) + '\n'
        )
        set_weekly_habit_date('weekly_review')
        assert (
            get_weekly_habit_date('weekly_review')
            == datetime.now().date().isoformat()
        )


# ── load_areas / save_areas ──────────────────────────────────────────────────


class TestLoadAndSaveAreas:
    def test_returns_empty_list_when_file_missing(self) -> None:
        assert load_areas() == []

    def test_returns_areas_from_file(self, tmp_path: Path) -> None:
        data = [{'name': 'Health', 'notes': ''}]
        storage.AREAS_PATH.write_text(json.dumps(data) + '\n')
        assert load_areas() == data

    def test_save_writes_json_to_areas_path(self, tmp_path: Path) -> None:
        areas = [{'name': 'Work', 'notes': 'Day job'}]
        save_areas(areas)
        assert storage.AREAS_PATH.exists()
        assert json.loads(storage.AREAS_PATH.read_text()) == areas

    def test_roundtrip_preserves_name_and_notes(self) -> None:
        areas = [
            {'name': 'Health', 'notes': 'exercise and sleep'},
            {'name': 'Family', 'notes': ''},
        ]
        save_areas(areas)
        loaded = load_areas()
        assert loaded[0]['name'] == 'Health'
        assert loaded[0]['notes'] == 'exercise and sleep'
        assert loaded[1]['name'] == 'Family'


# ── load_review_state / save_review_state / reset_review_state ───────────────


class TestReviewState:
    def test_returns_all_false_when_file_missing(self) -> None:
        assert load_review_state(3) == [False, False, False]

    def test_returns_all_false_when_week_start_differs(self) -> None:
        state = {'week_start': '2020-01-06', 'steps_done': [True, True, True]}
        storage.HABITS_PATH.write_text(
            json.dumps({'review_state': state}) + '\n'
        )
        assert load_review_state(3) == [False, False, False]

    def test_returns_saved_state_when_week_matches(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            storage, '_current_week_start', lambda: '2026-07-06'
        )
        state = {'week_start': '2026-07-06', 'steps_done': [True, False, True]}
        storage.HABITS_PATH.write_text(
            json.dumps({'review_state': state}) + '\n'
        )
        assert load_review_state(3) == [True, False, True]

    def test_roundtrip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            storage, '_current_week_start', lambda: '2026-07-06'
        )
        save_review_state([True, True, False])
        assert load_review_state(3) == [True, True, False]

    def test_returns_all_false_when_step_count_differs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            storage, '_current_week_start', lambda: '2026-07-06'
        )
        save_review_state([True, True])
        assert load_review_state(4) == [False, False, False, False]

    def test_reset_removes_review_state_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            storage, '_current_week_start', lambda: '2026-07-06'
        )
        save_review_state([True, False])
        reset_review_state()
        data = json.loads(storage.HABITS_PATH.read_text())
        assert 'review_state' not in data

    def test_reset_is_noop_when_file_missing(self) -> None:
        reset_review_state()  # should not raise
