"""Tests for gtd.notion.init — ID parsing and schema upgrades.

`_upgrade_schema` rebuilds select options for an existing, live database.
If it ever overwrites instead of merging, `gtd init --upgrade` silently
deletes every custom option the user added in Notion, so the merge tests
below assert on what survives rather than only on what gets added.
"""

from unittest.mock import MagicMock, patch

import pytest

from gtd.notion.init import (
    _parse_notion_id,
    _resolve_token,
    _upgrade_schema,
)
from gtd.notion.schema import STATUSES


TOKEN = 'fake-test-token'  # noqa: S105
DB_ID = 'test-db-id'

RAW_ID = '87461ba78e4149c7b6a1295e8bbc298d'
DASHED_ID = '87461ba7-8e41-49c7-b6a1-295e8bbc298d'


def _ok_response(payload: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.is_success = True
    resp.json.return_value = payload if payload is not None else {}
    return resp


def _existing_schema(properties: dict) -> dict:
    return {'properties': properties}


def _select_prop(option_names: list[str]) -> dict:
    return {'select': {'options': [{'name': n} for n in option_names]}}


def _patched_props(patch_mock: MagicMock) -> dict:
    return patch_mock.call_args.kwargs['json']['properties']


# --- _parse_notion_id: four documented input shapes, all user-pasted ---


class TestParseNotionId:
    @pytest.mark.parametrize(
        ('raw', 'expected'),
        [
            (RAW_ID, RAW_ID),
            (DASHED_ID, RAW_ID),
            (f'Home-Base-{RAW_ID}', RAW_ID),
            (f'https://notion.so/Home-Base-{RAW_ID}', RAW_ID),
            (f'https://www.notion.so/workspace/Page-{DASHED_ID}', RAW_ID),
            (f'https://notion.so/Home-Base-{RAW_ID}/', RAW_ID),
            (f'https://notion.so/Home-Base-{RAW_ID}?v=deadbeef', RAW_ID),
            (f'  {RAW_ID}  ', RAW_ID),
            (RAW_ID.upper(), RAW_ID.upper()),
        ],
    )
    def test_extracts_id_from_supported_formats(self, raw: str, expected: str):
        assert _parse_notion_id(raw) == expected

    @pytest.mark.parametrize(
        'raw',
        [
            '',
            'Home-Base',
            'not-an-id-at-all',
            RAW_ID[:-1],
            f'{RAW_ID}zz',
            'https://notion.so/',
        ],
    )
    def test_returns_none_when_no_id_present(self, raw: str):
        assert _parse_notion_id(raw) is None

    def test_trailing_text_after_id_is_not_matched(self):
        """The regex is end-anchored — an ID mid-string isn't an ID."""
        assert _parse_notion_id(f'{RAW_ID}/subpage-name') is None


# --- _upgrade_schema: must merge options, never replace them ---


class TestUpgradeSchema:
    def _run(self, existing: dict) -> tuple[list[str], MagicMock]:
        with (
            patch(
                'gtd.notion.init._get_existing_schema', return_value=existing
            ),
            patch('httpx.patch', return_value=_ok_response()) as patch_mock,
        ):
            changes = _upgrade_schema(TOKEN, DB_ID)
        return changes, patch_mock

    def test_up_to_date_schema_makes_no_request(self):
        existing = _existing_schema(
            {
                'Header': {'title': {}},
                'Status': _select_prop(STATUSES),
                'Context': {'select': {}},
                'List Category': {'select': {}},
                'Next Actionable Step': {'rich_text': {}},
                'Success Condition': {'rich_text': {}},
                'Due Date': {'date': {}},
                'Follow-Up Date': {'date': {}},
                'Created Date': {'date': {}},
            },
        )
        changes, patch_mock = self._run(existing)

        assert changes == []
        patch_mock.assert_not_called()

    def test_missing_property_is_added(self):
        existing = _existing_schema(
            {
                'Header': {'title': {}},
                'Status': _select_prop(STATUSES),
                'Context': {'select': {}},
                'List Category': {'select': {}},
                'Next Actionable Step': {'rich_text': {}},
                'Success Condition': {'rich_text': {}},
                'Due Date': {'date': {}},
                'Created Date': {'date': {}},
            },
        )
        changes, patch_mock = self._run(existing)

        assert changes == ['Added property: Follow-Up Date']
        assert _patched_props(patch_mock) == {'Follow-Up Date': {'date': {}}}

    def test_missing_status_options_are_added(self):
        existing = _existing_schema(
            {'Status': _select_prop(['Triage', 'Current Project'])},
        )
        changes, patch_mock = self._run(existing)

        patched = _patched_props(patch_mock)
        names = [o['name'] for o in patched['Status']['select']['options']]
        assert set(STATUSES).issubset(names)
        assert any('Recurring' in c for c in changes)

    def test_user_added_options_survive_the_upgrade(self):
        """The data-loss guard: custom options must not be replaced."""
        existing = _existing_schema(
            {'Status': _select_prop(['Triage', 'MyCustomStatus'])},
        )
        _changes, patch_mock = self._run(existing)

        patched = _patched_props(patch_mock)
        names = [o['name'] for o in patched['Status']['select']['options']]
        assert 'MyCustomStatus' in names
        assert set(STATUSES).issubset(names)

    def test_existing_options_keep_their_notion_metadata(self):
        """Merging must reuse the original option dicts, not rebuild them.

        Notion options carry an `id` and `color`; dropping them on a PATCH
        re-creates the option and detaches it from tagged pages.
        """
        existing = _existing_schema(
            {
                'Status': {
                    'select': {
                        'options': [
                            {
                                'name': 'Triage',
                                'id': 'opt-1',
                                'color': 'purple',
                            },
                        ],
                    },
                },
            },
        )
        _changes, patch_mock = self._run(existing)

        patched = _patched_props(patch_mock)
        options = patched['Status']['select']['options']
        triage = next(o for o in options if o['name'] == 'Triage')
        assert triage['id'] == 'opt-1'
        assert triage['color'] == 'purple'

    def test_optionless_select_properties_are_left_alone(self):
        """Context/List Category are managed in Notion, not by DB_SCHEMA."""
        existing = _existing_schema(
            {
                'Context': _select_prop(['@home', '@work']),
                'List Category': _select_prop(['Books']),
            },
        )
        changes, patch_mock = self._run(existing)

        patched = _patched_props(patch_mock)
        assert 'Context' not in patched
        assert 'List Category' not in patched
        assert not any('Context' in c for c in changes)

    def test_empty_schema_adds_every_property(self):
        changes, patch_mock = self._run(_existing_schema({}))

        patched = _patched_props(patch_mock)
        assert set(patched) == {
            'Header',
            'Status',
            'Context',
            'List Category',
            'Next Actionable Step',
            'Success Condition',
            'Due Date',
            'Follow-Up Date',
            'Created Date',
        }
        assert len(changes) == len(patched)

    def test_change_list_names_the_added_options(self):
        existing = _existing_schema(
            {'Status': _select_prop([s for s in STATUSES if s != 'List'])},
        )
        changes, _patch_mock = self._run(existing)

        assert 'Added options to Status: List' in changes


# --- _resolve_token: config beats env beats prompt ---


class TestResolveToken:
    def test_config_token_wins(self):
        with patch.dict('os.environ', {'NOTION_NOTES_TOKEN': 'env-token'}):
            assert _resolve_token({'token': 'config-token'}) == 'config-token'

    def test_falls_back_to_env_var(self):
        with patch.dict('os.environ', {'NOTION_NOTES_TOKEN': 'env-token'}):
            assert _resolve_token({}) == 'env-token'

    def test_prompts_when_nothing_configured(self):
        with (
            patch.dict('os.environ', {}, clear=True),
            patch(
                'gtd.notion.init.prompt_input', return_value='typed-token'
            ) as prompt,
        ):
            assert _resolve_token({}) == 'typed-token'
        prompt.assert_called_once()

    def test_declined_prompt_returns_none(self):
        with (
            patch.dict('os.environ', {}, clear=True),
            patch('gtd.notion.init.prompt_input', return_value=None),
        ):
            assert _resolve_token({}) is None
