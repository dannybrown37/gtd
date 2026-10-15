import subprocess
from unittest.mock import patch, MagicMock

import pytest

from gtd.ui import (
    PREVIEW_ENV_VAR,
    CancelAction,
    fzf_on_a_list,
    pause,
    prompt_input,
)


class TestPromptInputCtrlC:
    def test_raises_cancel_action_on_ctrl_c(self):
        with patch('builtins.input', side_effect=KeyboardInterrupt):
            try:
                prompt_input('test: ')
                raised = False
            except CancelAction:
                raised = True
            assert raised is True


class TestPauseCtrlC:
    def test_does_not_raise_on_ctrl_c(self):
        with patch('builtins.input', side_effect=KeyboardInterrupt):
            # Should not raise — just returns
            pause()


class TestFzfCtrlC:
    @patch('gtd.ui.subprocess.run')
    def test_raises_cancel_action_on_fzf_ctrl_c(self, mock_run):
        mock_run.return_value = MagicMock(returncode=130, stdout='')
        try:
            fzf_on_a_list(['a', 'b'], prompt='test')
            raised = False
        except CancelAction:
            raised = True
        assert raised is True

    @patch('gtd.ui.subprocess.run')
    def test_returns_none_on_empty_selection(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout='')
        result = fzf_on_a_list(['a', 'b'], prompt='test')
        assert result is None

    @patch('gtd.ui.subprocess.run')
    def test_returns_selection(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout='b\n')
        result = fzf_on_a_list(['a', 'b'], prompt='test')
        assert result == 'b'


HOSTILE_TEXT = [
    'Buy milk $(id -un) {fzf:prompt}',
    "it's `whoami` {} {q} {+}",
    "'; touch /tmp/pwned; echo '",
    'line one\nline two $HOME',
]


class TestFzfPreviewText:
    @pytest.mark.parametrize('text', HOSTILE_TEXT)
    @patch('gtd.ui.subprocess.run')
    def test_preview_text_never_enters_argv(self, mock_run, text: str):
        """Item text reaches the preview through the environment only.

        fzf expands placeholders in --preview before `sh -c`.
        """
        mock_run.return_value = MagicMock(returncode=0, stdout='a\n')
        fzf_on_a_list(['a'], prompt='p', preview_text=text)
        argv = mock_run.call_args.args[0]
        assert all(text not in arg for arg in argv)
        assert mock_run.call_args.kwargs['env'][PREVIEW_ENV_VAR] == text

    @pytest.mark.parametrize('text', HOSTILE_TEXT)
    def test_preview_command_prints_text_literally(self, text: str):
        with patch('gtd.ui.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout='a\n')
            fzf_on_a_list(['a'], prompt='p', preview_text=text)
            argv = mock_run.call_args.args[0]
        template = argv[argv.index('--preview') + 1]
        assert '{' not in template
        shown = subprocess.run(  # noqa: S603
            ['/bin/sh', '-c', template],
            env={PREVIEW_ENV_VAR: text},
            capture_output=True,
            text=True,
            check=True,
        )
        assert shown.stdout == f'{text}\n'
