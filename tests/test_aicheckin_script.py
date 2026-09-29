import stat
from pathlib import Path

from scripts import aicheckin


def test_solve_acw_uses_private_temporary_files_and_explicit_node_path(monkeypatch):
	captured = {}

	def fake_run(command, **kwargs):
		captured['command'] = command
		captured['directory'] = Path(command[1]).parent
		challenge_path = Path(command[2])
		assert challenge_path.read_text(encoding='utf-8') == 'document.cookie="challenge"'
		assert 'process.argv[2]' in Path(command[1]).read_text(encoding='utf-8')
		assert stat.S_IMODE(captured['directory'].stat().st_mode) & 0o077 == 0
		assert kwargs['shell'] is False
		return aicheckin.subprocess.CompletedProcess(command, 0, '{"acw_sc__v2":"fresh-token"}\n', '')

	monkeypatch.setattr(aicheckin.shutil, 'which', lambda executable: '/usr/local/bin/node')
	monkeypatch.setattr(aicheckin.subprocess, 'run', fake_run)

	assert aicheckin.solve_acw('<script>document.cookie="challenge"</script>') == 'fresh-token'
	assert captured['command'][0] == '/usr/local/bin/node'
	assert len(captured['command']) == 3
	assert not captured['directory'].exists()


def test_solve_acw_skips_node_when_executable_is_missing(monkeypatch):
	def unexpected_run(*args, **kwargs):
		raise AssertionError('Node must not run when unavailable')

	monkeypatch.setattr(aicheckin.shutil, 'which', lambda executable: None)
	monkeypatch.setattr(aicheckin.subprocess, 'run', unexpected_run)

	assert aicheckin.solve_acw('<script>document.cookie="challenge"</script>') is None
