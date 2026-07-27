"""Tests for gtd.notion.client query and schema-mutation paths.

These functions PATCH the live Notion schema by rebuilding the full select
options array. A bug here silently drops categories or contexts from every
existing item, so the assertions below focus on what survives the rebuild.
"""

from unittest.mock import MagicMock, patch

import pytest

from gtd.notion.client import (
    add_context,
    add_list_category,
    get_contexts,
    get_list_categories,
    get_select_options,
    query_database,
    remove_context,
    remove_list_category,
    rename_context,
    rename_list_category,
)


DB_ID = 'test-db-id'


def _ok_response(payload: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.is_success = True
    resp.json.return_value = payload if payload is not None else {}
    return resp


def _schema(prop_name: str, option_names: list[str]) -> dict:
    return {
        'properties': {
            prop_name: {
                'select': {'options': [{'name': n} for n in option_names]},
            },
        },
    }


def _patched_options(patch_mock: MagicMock, prop_name: str) -> list[str]:
    """Pull the option names out of the PATCH payload that was sent."""
    payload = patch_mock.call_args.kwargs['json']
    opts = payload['properties'][prop_name]['select']['options']
    return [o['name'] for o in opts]


# --- query_database: the cursor loop every read path goes through ---


class TestQueryDatabase:
    def test_single_page_returns_results(self):
        resp = _ok_response({'results': [{'id': '1'}], 'has_more': False})
        with (
            patch('gtd.notion.client._post', return_value=resp) as post,
            patch('gtd.notion.client.get_projects_db_id', return_value=DB_ID),
        ):
            results = query_database()

        assert results == [{'id': '1'}]
        assert post.call_count == 1

    def test_follows_cursor_until_has_more_is_false(self):
        responses = [
            _ok_response(
                {
                    'results': [{'id': '1'}],
                    'has_more': True,
                    'next_cursor': 'cursor-a',
                },
            ),
            _ok_response(
                {
                    'results': [{'id': '2'}],
                    'has_more': True,
                    'next_cursor': 'cursor-b',
                },
            ),
            _ok_response({'results': [{'id': '3'}], 'has_more': False}),
        ]
        # query_database reuses one payload dict, so call_args_list entries
        # all alias it — the cursor has to be read as each call happens.
        cursors: list[str | None] = []

        def record_cursor(*_args: object, **kwargs) -> MagicMock:
            cursors.append(kwargs['json'].get('start_cursor'))
            return responses[len(cursors) - 1]

        with (
            patch('gtd.notion.client._post', side_effect=record_cursor),
            patch('gtd.notion.client.get_projects_db_id', return_value=DB_ID),
        ):
            results = query_database()

        assert [r['id'] for r in results] == ['1', '2', '3']
        assert cursors == [None, 'cursor-a', 'cursor-b']

    def test_missing_has_more_terminates_loop(self):
        resp = _ok_response({'results': [{'id': '1'}]})
        with (
            patch('gtd.notion.client._post', return_value=resp) as post,
            patch('gtd.notion.client.get_projects_db_id', return_value=DB_ID),
        ):
            results = query_database()

        assert results == [{'id': '1'}]
        assert post.call_count == 1

    def test_filter_is_forwarded_in_payload(self):
        resp = _ok_response({'results': [], 'has_more': False})
        filter_obj = {'property': 'Status', 'select': {'equals': 'Inbox'}}
        with (
            patch('gtd.notion.client._post', return_value=resp) as post,
            patch('gtd.notion.client.get_projects_db_id', return_value=DB_ID),
        ):
            query_database(filter_obj=filter_obj)

        assert post.call_args.kwargs['json']['filter'] == filter_obj

    def test_explicit_database_id_skips_config_lookup(self):
        resp = _ok_response({'results': [], 'has_more': False})
        with (
            patch('gtd.notion.client._post', return_value=resp) as post,
            patch('gtd.notion.client.get_projects_db_id') as get_db,
        ):
            query_database(database_id='explicit-id')

        get_db.assert_not_called()
        assert 'explicit-id' in post.call_args.args[0]


# --- get_select_options: tolerates a schema missing the property ---


class TestGetSelectOptions:
    @pytest.mark.parametrize(
        ('schema', 'expected'),
        [
            (_schema('Context', ['@home', '@work']), ['@home', '@work']),
            (_schema('Context', []), []),
            ({'properties': {}}, []),
            ({'properties': {'Context': {}}}, []),
            ({'properties': {'Context': {'select': {}}}}, []),
        ],
    )
    def test_returns_option_names(self, schema: dict, expected: list[str]):
        with patch(
            'gtd.notion.client.get_database_schema', return_value=schema
        ):
            assert get_select_options('Context') == expected

    @pytest.mark.parametrize(
        ('func', 'prop_name'),
        [
            (get_list_categories, 'List Category'),
            (get_contexts, 'Context'),
        ],
    )
    def test_wrappers_read_the_right_property(self, func, prop_name: str):
        schema = _schema(prop_name, ['alpha'])
        with patch(
            'gtd.notion.client.get_database_schema', return_value=schema
        ):
            assert func() == ['alpha']


# --- add/remove: must preserve every option they aren't targeting ---


ADD_CASES = [
    (add_list_category, 'List Category'),
    (add_context, 'Context'),
]
REMOVE_CASES = [
    (remove_list_category, 'List Category'),
    (remove_context, 'Context'),
]


class TestAddOption:
    @pytest.mark.parametrize(('func', 'prop_name'), ADD_CASES)
    def test_appends_without_dropping_existing(self, func, prop_name: str):
        schema = _schema(prop_name, ['alpha', 'beta'])
        with (
            patch(
                'gtd.notion.client.get_database_schema', return_value=schema
            ),
            patch('gtd.notion.client.get_projects_db_id', return_value=DB_ID),
            patch(
                'gtd.notion.client._patch', return_value=_ok_response()
            ) as patch_mock,
        ):
            func('gamma')

        assert _patched_options(patch_mock, prop_name) == [
            'alpha',
            'beta',
            'gamma',
        ]

    @pytest.mark.parametrize(('func', 'prop_name'), ADD_CASES)
    def test_duplicate_is_a_no_op(self, func, prop_name: str):
        schema = _schema(prop_name, ['alpha', 'beta'])
        with (
            patch(
                'gtd.notion.client.get_database_schema', return_value=schema
            ),
            patch('gtd.notion.client.get_projects_db_id', return_value=DB_ID),
            patch('gtd.notion.client._patch') as patch_mock,
        ):
            func('alpha')

        patch_mock.assert_not_called()


class TestRemoveOption:
    @pytest.mark.parametrize(('func', 'prop_name'), REMOVE_CASES)
    def test_removes_only_the_target(self, func, prop_name: str):
        schema = _schema(prop_name, ['alpha', 'beta', 'gamma'])
        with (
            patch(
                'gtd.notion.client.get_database_schema', return_value=schema
            ),
            patch('gtd.notion.client.get_projects_db_id', return_value=DB_ID),
            patch(
                'gtd.notion.client._patch', return_value=_ok_response()
            ) as patch_mock,
        ):
            func('beta')

        assert _patched_options(patch_mock, prop_name) == ['alpha', 'gamma']

    @pytest.mark.parametrize(('func', 'prop_name'), REMOVE_CASES)
    def test_unknown_option_is_a_no_op(self, func, prop_name: str):
        schema = _schema(prop_name, ['alpha'])
        with (
            patch(
                'gtd.notion.client.get_database_schema', return_value=schema
            ),
            patch('gtd.notion.client.get_projects_db_id', return_value=DB_ID),
            patch('gtd.notion.client._patch') as patch_mock,
        ):
            func('nonexistent')

        patch_mock.assert_not_called()


# --- rename: rewrites one option in place, leaves ordering intact ---


class TestRenameListCategory:
    def test_renames_target_and_preserves_others(self):
        schema = _schema('List Category', ['alpha', 'beta', 'gamma'])
        with (
            patch(
                'gtd.notion.client.get_database_schema', return_value=schema
            ),
            patch('gtd.notion.client.get_projects_db_id', return_value=DB_ID),
            patch(
                'gtd.notion.client._patch', return_value=_ok_response()
            ) as patch_mock,
        ):
            rename_list_category('beta', 'delta')

        assert _patched_options(patch_mock, 'List Category') == [
            'alpha',
            'delta',
            'gamma',
        ]

    def test_unknown_option_leaves_options_unchanged(self):
        schema = _schema('List Category', ['alpha', 'beta'])
        with (
            patch(
                'gtd.notion.client.get_database_schema', return_value=schema
            ),
            patch('gtd.notion.client.get_projects_db_id', return_value=DB_ID),
            patch(
                'gtd.notion.client._patch', return_value=_ok_response()
            ) as patch_mock,
        ):
            rename_list_category('nonexistent', 'delta')

        assert _patched_options(patch_mock, 'List Category') == [
            'alpha',
            'beta',
        ]


class TestRenameContext:
    def test_renames_option_and_migrates_tagged_pages(self):
        schema = _schema('Context', ['@home', '@work'])
        pages = [{'id': 'page-1'}, {'id': 'page-2'}]
        with (
            patch(
                'gtd.notion.client.query_database', return_value=pages
            ) as query,
            patch(
                'gtd.notion.client.get_database_schema', return_value=schema
            ),
            patch('gtd.notion.client.get_projects_db_id', return_value=DB_ID),
            patch(
                'gtd.notion.client._patch', return_value=_ok_response()
            ) as patch_mock,
            patch('gtd.notion.client.update_page') as update_page,
        ):
            rename_context('@work', '@office')

        assert _patched_options(patch_mock, 'Context') == ['@home', '@office']
        assert query.call_args.kwargs['filter_obj'] == {
            'property': 'Context',
            'select': {'equals': '@work'},
        }
        assert [c.args[0] for c in update_page.call_args_list] == [
            'page-1',
            'page-2',
        ]

    def test_pages_are_fetched_before_the_schema_is_patched(self):
        """The old option must still exist when the query runs.

        Patching the schema first would make the filter match nothing and
        silently orphan every item that had the old context.
        """
        calls: list[str] = []
        schema = _schema('Context', ['@work'])

        def record_query(**_kwargs) -> list[dict]:
            calls.append('query')
            return []

        def record_patch(*_args: object, **_kwargs: object) -> MagicMock:
            calls.append('patch')
            return _ok_response()

        with (
            patch(
                'gtd.notion.client.query_database', side_effect=record_query
            ),
            patch(
                'gtd.notion.client.get_database_schema', return_value=schema
            ),
            patch('gtd.notion.client.get_projects_db_id', return_value=DB_ID),
            patch('gtd.notion.client._patch', side_effect=record_patch),
            patch('gtd.notion.client.update_page'),
        ):
            rename_context('@work', '@office')

        assert calls == ['query', 'patch']

    def test_no_tagged_pages_still_renames_the_option(self):
        schema = _schema('Context', ['@home', '@work'])
        with (
            patch('gtd.notion.client.query_database', return_value=[]),
            patch(
                'gtd.notion.client.get_database_schema', return_value=schema
            ),
            patch('gtd.notion.client.get_projects_db_id', return_value=DB_ID),
            patch(
                'gtd.notion.client._patch', return_value=_ok_response()
            ) as patch_mock,
            patch('gtd.notion.client.update_page') as update_page,
        ):
            rename_context('@work', '@office')

        assert _patched_options(patch_mock, 'Context') == ['@home', '@office']
        update_page.assert_not_called()
