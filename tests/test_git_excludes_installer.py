import subprocess
from pathlib import Path


def test_local_git_excludes_are_installed_idempotently(tmp_path):
	repo_root = tmp_path / 'repo'
	repo_root.mkdir()
	subprocess.run(['git', 'init', '--quiet', str(repo_root)], check=True)
	installer = Path(__file__).resolve().parents[1] / 'deploy' / 'install-git-excludes.sh'

	for _ in range(2):
		subprocess.run(['bash', str(installer), str(repo_root)], check=True, capture_output=True, text=True)

	exclude_path = subprocess.run(
		['git', '-C', str(repo_root), 'rev-parse', '--path-format=absolute', '--git-path', 'info/exclude'],
		check=True,
		capture_output=True,
		text=True,
	).stdout.strip()
	exclude_text = Path(exclude_path).read_text(encoding='utf-8')
	assert exclude_text.count('# BEGIN aicheckin deployment-only excludes') == 1
	assert exclude_text.count('# END aicheckin deployment-only excludes') == 1

	ignored_paths = [
		'checkin_daemon.log',
		'.checkin_daemon.lock',
		'daily_checkin_state.json',
		'checkin_history.sqlite3',
		'scripts/aicheckin_test_agentrouter.py',
	]
	for relative_path in ignored_paths:
		file_path = repo_root / relative_path
		file_path.parent.mkdir(parents=True, exist_ok=True)
		file_path.write_text('test only', encoding='utf-8')
		result = subprocess.run(
			['git', 'check-ignore', '--quiet', '--', relative_path],
			cwd=repo_root,
			check=False,
		)
		assert result.returncode == 0, relative_path
