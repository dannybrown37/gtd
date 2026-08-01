"""Tests for gtd.notion.capture — inbox capture and @Name context detection."""

from unittest.mock import MagicMock, patch

from gtd.notion.capture import _create_page, extract_agenda_context


def _ok_response(payload: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.is_success = True
    resp.json.return_value = payload if payload is not None else {'id': 'p1'}
    return resp


class TestExtractAgendaContext:
    def test_no_mention_returns_none(self) -> None:
        with patch('gtd.notion.capture.get_contexts', return_value=[]):
            assert extract_agenda_context('buy milk') is None

    def test_new_mention_is_capitalized(self) -> None:
        with patch('gtd.notion.capture.get_contexts', return_value=[]):
            assert extract_agenda_context('@chris budget review') == '@Chris'

    def test_reuses_existing_casing(self) -> None:
        with patch('gtd.notion.capture.get_contexts', return_value=['@chris']):
            assert extract_agenda_context('@Chris budget review') == '@chris'

    def test_only_first_mention_is_used(self) -> None:
        with patch('gtd.notion.capture.get_contexts', return_value=[]):
            assert extract_agenda_context('@chris and @sara') == '@Chris'


class TestCreatePage:
    def test_plain_header_has_no_context(self) -> None:
        with (
            patch('gtd.notion.capture.get_token', return_value='tok'),
            patch('gtd.notion.capture.get_projects_db_id', return_value='db1'),
            patch('httpx.post', return_value=_ok_response()) as post_mock,
        ):
            _create_page('buy milk')
        props = post_mock.call_args.kwargs['json']['properties']
        assert 'Context' not in props

    def test_agenda_mention_sets_and_creates_context(self) -> None:
        with (
            patch('gtd.notion.capture.get_token', return_value='tok'),
            patch('gtd.notion.capture.get_projects_db_id', return_value='db1'),
            patch('gtd.notion.capture.get_contexts', return_value=[]),
            patch('gtd.notion.capture.add_context') as add_context_mock,
            patch('httpx.post', return_value=_ok_response()) as post_mock,
        ):
            _create_page('@chris budget review')
        props = post_mock.call_args.kwargs['json']['properties']
        assert props['Context'] == {'select': {'name': '@Chris'}}
        add_context_mock.assert_called_once_with('@Chris')

    def test_agenda_mention_is_auto_classified_as_current_project(
        self,
    ) -> None:
        with (
            patch('gtd.notion.capture.get_token', return_value='tok'),
            patch('gtd.notion.capture.get_projects_db_id', return_value='db1'),
            patch('gtd.notion.capture.get_contexts', return_value=[]),
            patch('gtd.notion.capture.add_context'),
            patch('httpx.post', return_value=_ok_response()) as post_mock,
        ):
            _create_page('@chris budget review')
        props = post_mock.call_args.kwargs['json']['properties']
        assert props['Status'] == {'select': {'name': 'Current Project'}}
        assert props['Success Condition'] == {
            'rich_text': [{'text': {'content': 'Discussed with Chris'}}]
        }
        assert props['Next Actionable Step'] == {
            'rich_text': [{'text': {'content': 'Discuss with Chris'}}]
        }

    def test_plain_header_stays_in_triage_with_no_success_condition(
        self,
    ) -> None:
        with (
            patch('gtd.notion.capture.get_token', return_value='tok'),
            patch('gtd.notion.capture.get_projects_db_id', return_value='db1'),
            patch('httpx.post', return_value=_ok_response()) as post_mock,
        ):
            _create_page('buy milk')
        props = post_mock.call_args.kwargs['json']['properties']
        assert props['Status'] == {'select': {'name': 'Triage'}}
        assert 'Success Condition' not in props
        assert 'Next Actionable Step' not in props

    def test_existing_agenda_context_is_not_recreated(self) -> None:
        with (
            patch('gtd.notion.capture.get_token', return_value='tok'),
            patch('gtd.notion.capture.get_projects_db_id', return_value='db1'),
            patch('gtd.notion.capture.get_contexts', return_value=['@Chris']),
            patch('gtd.notion.capture.add_context') as add_context_mock,
            patch('httpx.post', return_value=_ok_response()),
        ):
            _create_page('@chris budget review')
        add_context_mock.assert_not_called()
