from unittest.mock import patch, MagicMock

from gtd.ui import CancelAction, fzf_on_a_list, prompt_input, pause


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
