from unittest.mock import patch

from click.testing import CliRunner

from gtd.cli import cli


def invoke(*args: str) -> object:
    return CliRunner().invoke(cli, ['areas', *args])


class TestAreasList:
    def test_no_areas_shows_message(self) -> None:
        with patch('gtd.notion.client.get_areas', return_value=[]):
            result = invoke()
        assert 'No horizons defined' in result.output

    def test_lists_areas_with_name(self) -> None:
        with patch('gtd.notion.client.get_areas', return_value=['Health']):
            result = invoke()
        assert 'Health' in result.output


class TestAreasAdd:
    def test_add_creates_area(self) -> None:
        with (
            patch('gtd.notion.client.get_areas', return_value=[]),
            patch('gtd.notion.client.add_area') as add_mock,
        ):
            result = invoke('add', 'Health')
        add_mock.assert_called_once_with('Health')
        assert result.exit_code == 0
        assert 'Added' in result.output

    def test_add_duplicate_shows_error(self) -> None:
        with (
            patch('gtd.notion.client.get_areas', return_value=['Health']),
            patch('gtd.notion.client.add_area') as add_mock,
        ):
            result = invoke('add', 'Health')
        add_mock.assert_not_called()
        assert 'already exists' in result.output

    def test_add_is_case_insensitive_duplicate_check(self) -> None:
        with (
            patch('gtd.notion.client.get_areas', return_value=['Health']),
            patch('gtd.notion.client.add_area') as add_mock,
        ):
            result = invoke('add', 'health')
        add_mock.assert_not_called()
        assert 'already exists' in result.output


class TestAreasRemove:
    def test_remove_deletes_area(self) -> None:
        with (
            patch('gtd.notion.client.get_areas', return_value=['Health']),
            patch('gtd.notion.client.remove_area') as remove_mock,
        ):
            result = invoke('remove', 'Health')
        remove_mock.assert_called_once_with('Health')
        assert 'Removed' in result.output

    def test_remove_missing_shows_error(self) -> None:
        with (
            patch('gtd.notion.client.get_areas', return_value=[]),
            patch('gtd.notion.client.remove_area') as remove_mock,
        ):
            result = invoke('remove', 'Missing')
        remove_mock.assert_not_called()
        assert 'not found' in result.output

    def test_remove_is_case_insensitive(self) -> None:
        with (
            patch('gtd.notion.client.get_areas', return_value=['Health']),
            patch('gtd.notion.client.remove_area') as remove_mock,
        ):
            result = invoke('remove', 'health')
        remove_mock.assert_called_once_with('Health')
        assert 'Removed' in result.output
