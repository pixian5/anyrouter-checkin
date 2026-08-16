import shutil
import subprocess  # nosec B404
from pathlib import Path


def test_installer_renders_current_clone_path_without_fixed_server_path(tmp_path):
	project_root = tmp_path / 'server-clone'
	deploy_dir = project_root / 'deploy'
	shutil.copytree(Path(__file__).parent.parent / 'deploy', deploy_dir)
	output_dir = tmp_path / 'rendered'

	result = subprocess.run(  # nosec B603
		['bash', str(deploy_dir / 'install-local-systemd.sh'), '--render-only', str(output_dir)],
		check=True,
		capture_output=True,
		text=True,
	)

	service = (output_dir / 'anyrouter-checkin.service').read_text(encoding='utf-8')
	timer = (output_dir / 'anyrouter-checkin.timer').read_text(encoding='utf-8')
	assert f'WorkingDirectory={project_root}' in service
	assert f'EnvironmentFile={project_root}/.env' in service
	assert f'ExecStart={project_root}/.venv/bin/python -m pixian_overlay.runner' in service
	assert 'Environment=TZ=Asia/Singapore' in service
	assert '/opt/anyrouter-checkin' not in service
	assert 'Unit=anyrouter-checkin.service' in timer
	assert str(project_root) in result.stdout
