"""加载上游程序、安装 pixian 外挂后运行。"""

import fcntl
import hashlib
import os
import tempfile

from pixian_overlay import app
from pixian_overlay.actual_checkin import install


def run_main() -> None:
	project_key = hashlib.sha256(os.path.realpath(os.getcwd()).encode('utf-8')).hexdigest()[:16]
	lock_path = os.path.join(tempfile.gettempdir(), f'pixian-anyrouter-checkin-{project_key}.lock')
	with open(lock_path, 'a+', encoding='utf-8') as lock_file:
		try:
			fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
		except BlockingIOError:
			print('[INFO] another check-in process is already running; overlapping run skipped')
			return
		install(app)
		app.run_main()


if __name__ == '__main__':
	run_main()
