"""加载上游程序、安装 pixian 外挂后运行。"""

from pixian_overlay import app
from pixian_overlay.actual_checkin import install


def run_main() -> None:
	install(app)
	app.run_main()


if __name__ == '__main__':
	run_main()
