from unittest.mock import MagicMock

from pixian_overlay import runner


def test_runner_skips_overlapping_process(monkeypatch, capsys):
	monkeypatch.setattr(runner.fcntl, 'flock', MagicMock(side_effect=BlockingIOError()))
	install = MagicMock()
	run_main = MagicMock()
	monkeypatch.setattr(runner, 'install', install)
	monkeypatch.setattr(runner.app, 'run_main', run_main)

	runner.run_main()

	install.assert_not_called()
	run_main.assert_not_called()
	assert 'another check-in process is already running' in capsys.readouterr().out
