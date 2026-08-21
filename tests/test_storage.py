import json
from datetime import datetime
from pathlib import Path

import pytest

from gtd import storage
from gtd.storage import (
    REVIEW_STEPS,
    current_week_start,
    get_weekly_habit_date,
    habit_done_this_week,
    load_review_state,
    reset_review_state,
    save_review_state,
    set_review_step,
    set_weekly_habit_date,
)


@pytest.fixture(autouse=True)
def _isolated_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect all storage paths to a temp directory."""
    monkeypatch.setattr(storage, 'OUTPUT_PATH', tmp_path)
    monkeypatch.setattr(
        storage, 'HABITS_PATH', tmp_path / 'weekly_habits.json'
    )


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
            storage, 'current_week_start', lambda: '2026-07-06'
        )
        state = {'week_start': '2026-07-06', 'steps_done': [True, False, True]}
        storage.HABITS_PATH.write_text(
            json.dumps({'review_state': state}) + '\n'
        )
        assert load_review_state(3) == [True, False, True]

    def test_roundtrip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            storage, 'current_week_start', lambda: '2026-07-06'
        )
        save_review_state([True, True, False])
        assert load_review_state(3) == [True, True, False]

    def test_returns_all_false_when_step_count_differs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            storage, 'current_week_start', lambda: '2026-07-06'
        )
        save_review_state([True, True])
        assert load_review_state(4) == [False, False, False, False]

    def test_reset_removes_review_state_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            storage, 'current_week_start', lambda: '2026-07-06'
        )
        save_review_state([True, False])
        reset_review_state()
        data = json.loads(storage.HABITS_PATH.read_text())
        assert 'review_state' not in data

    def test_reset_is_noop_when_file_missing(self) -> None:
        reset_review_state()  # should not raise

    def test_save_preserves_habit_completion_dates(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Review state and habit dates share one file — neither wins.

        Both live in weekly_habits.json. If save_review_state stopped
        merging, finishing a review step would wipe the record of every
        habit already completed this week.
        """
        monkeypatch.setattr(
            storage, 'current_week_start', lambda: '2026-07-06'
        )
        set_weekly_habit_date('weekly_review')
        save_review_state([True, False])

        assert (
            get_weekly_habit_date('weekly_review')
            == datetime.now().date().isoformat()
        )
        assert load_review_state(2) == [True, False]

    def test_set_habit_date_preserves_review_state(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The same merge contract in the other direction."""
        monkeypatch.setattr(
            storage, 'current_week_start', lambda: '2026-07-06'
        )
        save_review_state([True, True])
        set_weekly_habit_date('weekly_review')

        assert load_review_state(2) == [True, True]

    def test_reset_clears_the_weekly_review_marker(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            storage, 'current_week_start', lambda: '2026-07-06'
        )
        set_weekly_habit_date('weekly_review')
        save_review_state([True, False])
        reset_review_state()

        data = json.loads(storage.HABITS_PATH.read_text())
        assert 'review_state' not in data
        assert 'weekly_review' not in data

    def test_reset_leaves_unrelated_habits_alone(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            storage, 'current_week_start', lambda: '2026-07-06'
        )
        set_weekly_habit_date('other_habit')
        save_review_state([True])
        reset_review_state()

        assert (
            get_weekly_habit_date('other_habit')
            == datetime.now().date().isoformat()
        )


# ── the data directory may not exist yet ────────────────────────────────────


class TestMissingDataDirectory:
    """Nothing else creates `~/.local/share/gtd`, so the writers must.

    Every read here degrades to a default when the file is absent, so a host
    that has never run the TUI serves `GET /review` perfectly and then 500s on
    the first tick — which is how this shipped. The autouse fixture above
    points at `tmp_path`, which always exists; these point one level deeper.
    """

    @pytest.fixture(autouse=True)
    def _unwritten_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fresh = tmp_path / 'never-created' / 'gtd'
        monkeypatch.setattr(storage, 'OUTPUT_PATH', fresh)
        monkeypatch.setattr(
            storage, 'HABITS_PATH', fresh / 'weekly_habits.json'
        )

    def test_set_review_step_creates_it(self) -> None:
        assert set_review_step(0, done=True)[0] is True

    def test_save_review_state_creates_it(self) -> None:
        save_review_state([True, False])

        assert load_review_state(2) == [True, False]

    def test_set_weekly_habit_date_creates_it(self) -> None:
        set_weekly_habit_date('weekly_review')

        assert get_weekly_habit_date('weekly_review') is not None


# ── current_week_start: drives the Monday reset ─────────────────────────────


class TestCurrentWeekStart:
    @pytest.mark.parametrize(
        ('today', 'expected_monday'),
        [
            ('2026-07-27', '2026-07-27'),  # Monday
            ('2026-07-28', '2026-07-27'),  # Tuesday
            ('2026-07-31', '2026-07-27'),  # Friday
            ('2026-08-01', '2026-07-27'),  # Saturday
            ('2026-08-02', '2026-07-27'),  # Sunday
            ('2026-08-03', '2026-08-03'),  # next Monday
        ],
    )
    def test_resolves_to_the_containing_monday(
        self,
        monkeypatch: pytest.MonkeyPatch,
        today: str,
        expected_monday: str,
    ) -> None:
        """Sunday must still belong to the week that began Monday.

        Getting this off by a day would reset a half-finished weekly
        review on Sunday instead of Monday.
        """
        fixed = datetime.fromisoformat(today)

        class _FixedDatetime(datetime):
            @classmethod
            def now(cls, tz: object = None) -> datetime:  # noqa: ARG003
                return fixed

        monkeypatch.setattr(storage, 'datetime', _FixedDatetime)

        assert current_week_start() == expected_monday


# ── REVIEW_STEPS / set_review_step / habit_done_this_week ────────────────────


class TestReviewSteps:
    def test_every_step_is_a_label_and_an_action(self) -> None:
        assert REVIEW_STEPS
        for label, action in REVIEW_STEPS:
            assert label.strip()
            assert action.strip()

    def test_the_tui_reads_this_list_rather_than_its_own(self) -> None:
        """The API serves these to the webapp; a TUI copy would drift."""
        from gtd import gtd_tui

        assert gtd_tui._GTD_REVIEW_STEPS is REVIEW_STEPS  # noqa: SLF001


class TestSetReviewStep:
    def test_checks_one_step_and_returns_the_week(self) -> None:
        steps = set_review_step(1, done=True)

        assert steps[1] is True
        assert steps == load_review_state(len(REVIEW_STEPS))

    def test_unchecks_a_checked_step(self) -> None:
        set_review_step(1, done=True)

        assert set_review_step(1, done=False)[1] is False

    def test_leaves_the_other_steps_alone(self) -> None:
        set_review_step(0, done=True)
        steps = set_review_step(2, done=True)

        assert steps[0] is True
        assert steps[1] is False
        assert steps[2] is True

    @pytest.mark.parametrize('index', [-1, len(REVIEW_STEPS)])
    def test_rejects_an_index_outside_the_checklist(self, index: int) -> None:
        with pytest.raises(IndexError):
            set_review_step(index, done=True)


class TestHabitDoneThisWeek:
    def test_false_when_never_done(self) -> None:
        assert habit_done_this_week('weekly_review') is False

    def test_true_when_done_today(self) -> None:
        set_weekly_habit_date('weekly_review')

        assert habit_done_this_week('weekly_review') is True

    def test_false_when_done_before_this_week(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        storage.HABITS_PATH.write_text(
            json.dumps({'weekly_review': '2026-07-05'}) + '\n'
        )
        monkeypatch.setattr(
            storage, 'current_week_start', lambda: '2026-07-06'
        )

        assert habit_done_this_week('weekly_review') is False

    def test_true_on_the_week_start_itself(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Monday's review counts for the week that starts that Monday."""
        storage.HABITS_PATH.write_text(
            json.dumps({'weekly_review': '2026-07-06'}) + '\n'
        )
        monkeypatch.setattr(
            storage, 'current_week_start', lambda: '2026-07-06'
        )

        assert habit_done_this_week('weekly_review') is True


class TestRemoteReviewBackend:
    """When an API server is configured, review state comes from it."""

    @pytest.fixture(autouse=True)
    def _configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv('GTD_API_URL', 'https://gtd.example.com')
        monkeypatch.setenv('GTD_API_KEY', 'secret')

    @staticmethod
    def _payload(done: list[bool], last: str | None = None) -> dict:
        return {
            'week_start': current_week_start(),
            'steps': [
                {'index': i, 'label': label, 'action': action, 'done': done[i]}
                for i, (label, action) in enumerate(REVIEW_STEPS)
            ],
            'last_done': last,
            'done_this_week': False,
        }

    def _stub(
        self,
        monkeypatch: pytest.MonkeyPatch,
        payload: dict,
    ) -> list[tuple[str, str, dict | None]]:
        calls: list[tuple[str, str, dict | None]] = []

        def fake(
            method: str, path: str, json_body: dict | None = None
        ) -> dict:
            calls.append((method, path, json_body))
            return payload

        monkeypatch.setattr(storage, '_remote_request', fake)
        return calls

    def test_load_reads_from_api(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        done = [True] + [False] * (len(REVIEW_STEPS) - 1)
        calls = self._stub(monkeypatch, self._payload(done))
        assert load_review_state(len(REVIEW_STEPS)) == done
        assert calls == [('GET', '/review', None)]

    def test_set_step_posts_to_api(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        done = [False] * len(REVIEW_STEPS)
        done[2] = True
        calls = self._stub(monkeypatch, self._payload(done))
        assert set_review_step(2, done=True)[2] is True
        assert calls == [('POST', '/review/step/2', {'done': True})]

    def test_save_state_posts_only_changed_steps(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        remote = [False] * len(REVIEW_STEPS)
        calls = self._stub(monkeypatch, self._payload(remote))
        wanted = list(remote)
        wanted[1] = True
        save_review_state(wanted)
        assert calls == [
            ('GET', '/review', None),
            ('POST', '/review/step/1', {'done': True}),
        ]

    def test_reset_posts_to_api(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = self._stub(
            monkeypatch, self._payload([False] * len(REVIEW_STEPS))
        )
        reset_review_state()
        assert calls == [('POST', '/review/reset', None)]

    def test_weekly_review_habit_reads_from_api(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        payload = self._payload([False] * len(REVIEW_STEPS), '2026-08-19')
        self._stub(monkeypatch, payload)
        assert get_weekly_habit_date('weekly_review') == '2026-08-19'

    def test_other_habits_stay_local(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        self._stub(monkeypatch, self._payload([False] * len(REVIEW_STEPS)))
        set_weekly_habit_date('other_habit')
        data = json.loads((tmp_path / 'weekly_habits.json').read_text())
        assert data['other_habit'] == datetime.now().date().isoformat()

    def test_marking_review_done_posts_complete(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = self._stub(
            monkeypatch, self._payload([False] * len(REVIEW_STEPS))
        )
        set_weekly_habit_date('weekly_review')
        assert calls == [('POST', '/review/complete', None)]

    def test_falls_back_to_local_when_api_unreachable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(*_args: object, **_kwargs: object) -> dict:
            msg = 'no network'
            raise OSError(msg)

        monkeypatch.setattr(storage, '_remote_request', boom)
        save_review_state([True] + [False] * (len(REVIEW_STEPS) - 1))
        assert load_review_state(len(REVIEW_STEPS))[0] is True


class TestRemoteNotConfigured:
    def test_url_without_key_stays_local(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv('GTD_API_URL', 'https://gtd.example.com')
        monkeypatch.delenv('GTD_API_KEY', raising=False)
        assert storage._remote_base() is None  # noqa: SLF001

    def test_key_without_url_stays_local(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv('GTD_API_URL', raising=False)
        monkeypatch.setenv('GTD_API_KEY', 'secret')
        assert storage._remote_base() is None  # noqa: SLF001
