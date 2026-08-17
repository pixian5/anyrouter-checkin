import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from pixian_overlay import app as checkin


def _history_details() -> dict:
	return {
		'anyrouter:85976': {
			'name': '85976',
			'provider': 'anyrouter',
			'success': True,
			'skipped': False,
			'check_in_status': 'success',
			'before_quota': 6976.3,
			'before_used': 48.7,
			'after_quota': 7001.3,
			'after_used': 48.7,
			'check_in_reward': 25,
			'usage_increase': 0,
			'balance_change': 25,
			'baseline_balance_change': 25,
			'display': 'must not be saved',
		}
	}


def test_history_database_appends_runs_and_saves_only_audit_fields(tmp_path, monkeypatch):
	database_file = tmp_path / 'checkin_history.sqlite3'
	monkeypatch.setattr(checkin, 'CHECKIN_HISTORY_DATABASE_FILE', str(database_file))

	checkin.initialize_checkin_history_database()
	checkin.record_checkin_history(
		'2026-08-17 08:00:00',
		_history_details(),
		accounts_total=1,
		accounts_succeeded=1,
		balance_changed=True,
		has_failures=False,
		execution_status='completed',
	)
	checkin.record_checkin_history(
		'2026-08-17 14:00:00',
		{},
		accounts_total=0,
		accounts_succeeded=0,
		balance_changed=False,
		has_failures=True,
		execution_status='configuration_failed',
		message='unknown provider',
	)

	connection = sqlite3.connect(database_file)
	try:
		runs = connection.execute(
			'SELECT run_time, execution_status, accounts_total, accounts_succeeded, balance_changed, has_failures, message FROM checkin_runs ORDER BY id'
		).fetchall()
		account_rows = connection.execute(
			"""SELECT account_key, name, provider, success, skipped, before_quota, before_used,
			   after_quota, after_used, check_in_reward, usage_increase, balance_change,
			   baseline_balance_change, check_in_status, error
			   FROM checkin_account_records"""
		).fetchall()
	finally:
		connection.close()

	assert runs == [
		('2026-08-17 08:00:00', 'completed', 1, 1, 1, 0, ''),
		('2026-08-17 14:00:00', 'configuration_failed', 0, 0, 0, 1, 'unknown provider'),
	]
	assert account_rows == [
		(
			'anyrouter:85976',
			'85976',
			'anyrouter',
			1,
			0,
			6976.3,
			48.7,
			7001.3,
			48.7,
			25.0,
			0.0,
			25.0,
			25.0,
			'success',
			'',
		)
	]
	assert database_file.stat().st_mode & 0o777 == 0o600


def test_history_query_script_prints_human_readable_and_json_output(tmp_path, monkeypatch):
	database_file = tmp_path / 'checkin_history.sqlite3'
	monkeypatch.setattr(checkin, 'CHECKIN_HISTORY_DATABASE_FILE', str(database_file))
	checkin.record_checkin_history(
		'2026-08-17 08:00:00',
		_history_details(),
		accounts_total=1,
		accounts_succeeded=1,
		balance_changed=True,
		has_failures=False,
		execution_status='completed',
	)

	script = Path(__file__).parent.parent / 'scripts' / 'show_checkin_history.py'
	readable = subprocess.run(
		[sys.executable, str(script), '--database', str(database_file), '--limit', '1'],
		check=True,
		capture_output=True,
		text=True,
	)
	structured = subprocess.run(
		[sys.executable, str(script), '--database', str(database_file), '--limit', '1', '--json'],
		check=True,
		capture_output=True,
		text=True,
	)

	assert '85976' in readable.stdout
	assert '6976.30 → $7001.30' in readable.stdout
	data = json.loads(structured.stdout)
	assert data[0]['accounts'][0]['check_in_reward'] == 25.0


def test_history_database_parent_directory_failure_is_wrapped(monkeypatch, tmp_path):
	database_file = tmp_path / 'nested' / 'checkin_history.sqlite3'
	monkeypatch.setattr(checkin, 'CHECKIN_HISTORY_DATABASE_FILE', str(database_file))
	monkeypatch.setattr(checkin.os, 'makedirs', lambda *args, **kwargs: (_ for _ in ()).throw(OSError('read-only')))

	with pytest.raises(checkin.CheckInHistoryError, match='read-only'):
		checkin.initialize_checkin_history_database()
