"""Guards on the webapp's Weekly Review.

The review was TUI-only, tracked as eight deferred entries in
`test_webapp_parity.py`'s `TUI_ONLY`. Those are gone now, so the parity test
covers the *names*; these cover the parts a declared capability can't prove —
that the flow reads its step list from the server rather than hard-coding one,
that each drill-down can be left both ways, and that the habit row behaves the
way the TUI's does.

Text assertions over the shipped JS/CSS: the webapp has no JS test harness.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from gtd import storage

WEBAPP = Path(__file__).parent.parent / 'src' / 'gtd' / 'webapp'


@pytest.fixture(scope='module')
def app_js() -> str:
    return (WEBAPP / 'app.js').read_text()


@pytest.fixture(scope='module')
def styles() -> str:
    return (WEBAPP / 'styles.css').read_text()


def test_review_is_a_view_with_its_own_loader(app_js: str) -> None:
    assert "kind: 'review'" in app_js
    assert 'async function loadReview()' in app_js
    assert "if (view.kind === 'review') return loadReview();" in app_js


class TestStepListIsNotDuplicated:
    """The step list lives in `storage.REVIEW_STEPS` and is served, not copied.

    A second copy hand-written in `app.js` is the same drift `notion/views.py`
    exists to prevent — the phone would show a different review from the
    terminal, and nothing would fail.
    """

    def test_steps_come_from_the_endpoint(self, app_js: str) -> None:
        assert "apiFetch('/review')" in app_js
        assert 'state.review.steps' in app_js

    @pytest.mark.parametrize(
        'label',
        [label for label, _ in storage.REVIEW_STEPS],
    )
    def test_no_step_label_is_hard_coded(
        self,
        app_js: str,
        label: str,
    ) -> None:
        assert label not in app_js


class TestStepActionsAreAllHandled:
    """Every `action` in REVIEW_STEPS must reach a renderer.

    An unhandled one would fall through to the entries renderer and query a
    status of `undefined`.
    """

    @pytest.mark.parametrize(
        'action',
        sorted({action for _, action in storage.REVIEW_STEPS}),
    )
    def test_action_is_reachable(self, app_js: str, action: str) -> None:
        """Either branched on by name, or a key in REVIEW_STEP_STATUS."""
        status_block = app_js.split('const REVIEW_STEP_STATUS = {')[1].split(
            '};', maxsplit=1
        )[0]
        handled = f"step.action === '{action}'" in app_js or bool(
            re.search(rf'^\s*{action}:', status_block, re.MULTILINE)
        )
        assert handled, f'review step action {action!r} is not handled'

    def test_entry_backed_steps_name_a_real_status(self, app_js: str) -> None:
        block = app_js.split('const REVIEW_STEP_STATUS = {')[1].split(
            '};', maxsplit=1
        )[0]
        for status in ('Current Project', 'Waiting For', 'Someday/Maybe'):
            assert status in block

    def test_manual_steps_tick_without_a_drill_down(self, app_js: str) -> None:
        assert "step.action === 'manual'" in app_js


class TestStepScoping:
    """Leaving a step and finishing a step are different, as in the TUI.

    The TUI splits its browse-screen footer into `this item` and `this step`
    for exactly this reason; a single control that both exits and ticks makes
    backing out of a step you only wanted to look at impossible.
    """

    def test_back_does_not_tick(self, app_js: str) -> None:
        body = app_js.split('function backToChecklist()')[1].split(
            '}', maxsplit=1
        )[0]
        assert 'setReviewStep' not in body

    def test_finish_ticks(self, app_js: str) -> None:
        body = app_js.split('async function finishReviewStep(step)')[1]
        assert 'setReviewStep(step.index, true)' in body.split('}')[0]

    def test_both_controls_are_rendered(self, app_js: str) -> None:
        assert 'function appendReviewStepHeader(' in app_js
        assert 'function appendReviewStepDone(' in app_js
        assert 'Done reviewing' in app_js


class TestChangesApplyImmediately:
    """The webapp must not batch review changes the way the TUI does.

    A Textual modal has a dismissal to flush on; a webapp screen does not, and
    a backgrounded phone would lose the batch.
    """

    def test_drill_downs_reuse_the_action_sheet(self, app_js: str) -> None:
        assert 'entryRow(entry, openActionSheet)' in app_js
        assert 'entryRow(entry, openTriageModal)' in app_js

    def test_no_pending_change_buffers(self, app_js: str) -> None:
        for name in ('_to_someday', '_to_drop', '_status_changes'):
            assert name not in app_js


class TestReviewCompletion:
    def test_completing_every_step_marks_the_habit(self, app_js: str) -> None:
        body = app_js.split('async function maybeCompleteReview()')[1]
        assert "apiFetch('/review/complete'" in body.split('\n}')[0]

    def test_completion_is_gated_on_all_steps(self, app_js: str) -> None:
        assert 'state.review.steps.every((s) => s.done)' in app_js

    def test_reset_is_confirmed_first(self, app_js: str) -> None:
        """Mirrors the TUI's `X` → ConfirmModal; it discards the week."""
        assert 'function confirmResetReview()' in app_js
        body = app_js.split('function confirmResetReview()')[1]
        assert 'Reset review progress?' in body
        assert "apiFetch('/review/reset'" in body


class TestHabitRow:
    """The Next Steps row that makes the review impossible to forget."""

    def test_row_is_prepended_to_next_steps(self, app_js: str) -> None:
        body = app_js.split('async function loadNextSteps()')[1]
        assert 'prependReviewHabitRow' in body.split('\n}')[0]

    def test_row_failure_cannot_blank_the_list(self, app_js: str) -> None:
        """It is chrome — /review going down must not lose Next Steps."""
        body = app_js.split('prependReviewHabitRow(await')[1]
        assert 'catch' in body.split('\n}')[0]

    def test_row_is_hidden_once_done(self, app_js: str) -> None:
        """The webapp has a dedicated review view, so it only needs the nag."""
        body = app_js.split('function prependReviewHabitRow(review)')[1]
        head = body.split('\n}')[0]
        assert 'if (review.done_this_week) return;' in head
        assert 'not done this week' in head

    def test_row_opens_the_review(self, app_js: str) -> None:
        body = app_js.split('function prependReviewHabitRow(review)')[1]
        assert "switchView('review')" in body.split('\n}')[0]

    def test_dot_has_both_states_styled(self, styles: str) -> None:
        assert '.habit-dot {' in styles
        assert '.habit-dot.done {' in styles


class TestCompleteStep:
    """`complete_step` is the TUI's `X` on an entry, not a review action.

    It sat in `TUI_ONLY` labelled 'Weekly Review not yet ported', which was
    simply wrong — it advances a numbered next-step list. Porting it is what
    let that entry be deleted rather than relabelled.
    """

    def test_action_sheet_offers_it(self, app_js: str) -> None:
        assert 'data-act="complete-step"' in app_js
        assert 'completeCurrentStep(entry)' in app_js

    def test_only_offered_when_there_is_a_step(self, app_js: str) -> None:
        assert 'entry.next_step ?' in app_js

    def test_renumbering_stays_server_side(self, app_js: str) -> None:
        """`advance_steps` must not be reimplemented in JS."""
        body = app_js.split('async function completeCurrentStep(entry)')[1]
        head = body.split('\n}')[0]
        assert '/complete-step' in head
        assert 'split' not in head
