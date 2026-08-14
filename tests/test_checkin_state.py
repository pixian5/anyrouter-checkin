import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import checkin
from checkin import (
	generate_balance_hash,
	get_account_state_key,
	mark_checked_in_today,
	should_send_notification,
)
from utils.config import AccountConfig


def test_balance_hash_changes_when_quota_changes():
	before = {'account_1': {'quota': 100.0, 'used': 20.0}}
	after = {'account_1': {'quota': 125.0, 'used': 20.0}}

	assert generate_balance_hash(before) != generate_balance_hash(after)


def test_balance_hash_ignores_used_quota_changes():
	before = {'account_1': {'quota': 100.0, 'used': 20.0}}
	after = {'account_1': {'quota': 100.0, 'used': 21.0}}

	assert generate_balance_hash(before) == generate_balance_hash(after)


def test_balance_hash_is_stable_for_equivalent_balances():
	left = {
		'account_2': {'quota': 50.0, 'used': 1.0},
		'account_1': {'quota': 100.0, 'used': 20.0},
	}
	right = {
		'account_1': {'used': 20.0, 'quota': 100.0},
		'account_2': {'used': 1.0, 'quota': 50.0},
	}

	assert generate_balance_hash(left) == generate_balance_hash(right)


def test_notification_is_skipped_when_all_accounts_are_unchanged():
	assert not should_send_notification(balance_changed=False, has_failures=False)


def test_notification_is_sent_when_only_some_account_balances_change():
	assert should_send_notification(balance_changed=True, has_failures=False)


def test_notification_is_sent_for_a_check_in_failure():
	assert should_send_notification(balance_changed=False, has_failures=True)


def test_first_snapshot_without_a_balance_change_does_not_notify():
	assert not should_send_notification(balance_changed=False, has_failures=False)


def test_account_state_key_is_stable_when_account_order_changes():
	account = AccountConfig(cookies={'session': 'token'}, api_user='123', provider='anyrouter', name='primary')
	assert get_account_state_key(account) == 'anyrouter:123'


def test_mark_checked_in_resets_stale_account_flags_from_previous_day(tmp_path, monkeypatch):
	state_file = tmp_path / 'daily_checkin_state.json'
	state_file.write_text(
		'{"date":"2000-01-01","accounts_checked":{"old-account":true},"providers_checked":{"old":true}}',
		encoding='utf-8',
	)
	monkeypatch.setattr(checkin, 'DAILY_CHECK_IN_STATE_FILE', str(state_file))

	mark_checked_in_today({}, 'now', provider='anyrouter', account_keys=['current-account'])

	state = checkin.load_daily_check_in_state()
	assert state['accounts_checked'] == {'current-account': True}
	assert state['providers_checked'] == {'anyrouter': True}
