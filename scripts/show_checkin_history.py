#!/usr/bin/env python3
"""只读显示 pixian 外挂写入的 SQLite 签到历史。"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from urllib.parse import quote


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(description='查看签到历史数据库')
	parser.add_argument('--database', default='checkin_history.sqlite3', help='SQLite 数据库路径')
	parser.add_argument('--limit', type=int, default=20, help='显示最近多少次运行（默认 20）')
	parser.add_argument('--date', help='仅显示指定日期，例如 2026-08-17')
	parser.add_argument('--json', action='store_true', help='输出 JSON')
	return parser.parse_args()


def _database_connection(database_path: Path) -> sqlite3.Connection:
	if not database_path.is_file():
		raise FileNotFoundError(f'签到历史数据库不存在: {database_path}')
	uri = f'file:{quote(str(database_path.resolve()))}?mode=ro'
	return sqlite3.connect(uri, uri=True)


def load_history(database_path: Path, limit: int, date: str | None) -> list[dict]:
	if limit < 1:
		raise ValueError('--limit 必须大于 0')
	connection = _database_connection(database_path)
	connection.row_factory = sqlite3.Row
	try:
		if date:
			runs = connection.execute(
				"""SELECT id, run_time, execution_status, accounts_total, accounts_succeeded,
					balance_changed, has_failures, message
					FROM checkin_runs WHERE run_time LIKE ? ORDER BY id DESC LIMIT ?""",
				(f'{date}%', limit),
			).fetchall()
		else:
			runs = connection.execute(
				"""SELECT id, run_time, execution_status, accounts_total, accounts_succeeded,
					balance_changed, has_failures, message
					FROM checkin_runs ORDER BY id DESC LIMIT ?""",
				(limit,),
			).fetchall()
		result = []
		for run in runs:
			accounts = connection.execute(
				"""SELECT account_key, name, provider, success, skipped, before_quota, before_used,
					after_quota, after_used, check_in_reward, usage_increase, balance_change,
					baseline_balance_change, check_in_status, error
					FROM checkin_account_records WHERE run_id = ? ORDER BY id""",
				(run['id'],),
			).fetchall()
			record = dict(run)
			record.pop('id')
			record['balance_changed'] = bool(record['balance_changed'])
			record['has_failures'] = bool(record['has_failures'])
			record['accounts'] = [dict(account) for account in accounts]
			for account in record['accounts']:
				account['success'] = bool(account['success'])
				account['skipped'] = bool(account['skipped'])
			result.append(record)
		return result
	finally:
		connection.close()


def _money(value: object) -> str:
	if not isinstance(value, (int, float)) or isinstance(value, bool):
		return '--'
	return f'${float(value):.2f}'


def print_readable(history: list[dict]) -> None:
	if not history:
		print('没有符合条件的签到记录。')
		return
	for run in history:
		print(
			f'{run["run_time"]} | {run["execution_status"]} | '
			f'{run["accounts_succeeded"]}/{run["accounts_total"]} 成功 | '
			f'余额变化={"是" if run["balance_changed"] else "否"} | '
			f'失败={"是" if run["has_failures"] else "否"}'
		)
		if run['message']:
			print(f'  运行备注: {run["message"]}')
		for account in run['accounts']:
			status = 'SKIP' if account['skipped'] else ('OK' if account['success'] else 'FAIL')
			print(
				f'  [{status}] {account["name"]} ({account["provider"]}) | '
				f'余额 {_money(account["before_quota"])} → {_money(account["after_quota"])} | '
				f'消耗 {_money(account["before_used"])} → {_money(account["after_used"])} | '
				f'奖励 {_money(account["check_in_reward"])}'
			)
			if account['error']:
				print(f'    错误: {account["error"]}')


def main() -> int:
	args = parse_args()
	try:
		history = load_history(Path(args.database), args.limit, args.date)
	except (FileNotFoundError, ValueError, sqlite3.Error) as error:
		print(f'错误: {error}', file=sys.stderr)
		return 1
	if args.json:
		print(json.dumps(history, ensure_ascii=False, indent=2))
	else:
		print_readable(history)
	return 0


if __name__ == '__main__':
	raise SystemExit(main())
