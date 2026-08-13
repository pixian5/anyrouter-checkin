import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from checkin import generate_balance_hash, get_account_state_key, should_send_notification
from utils.config import AccountConfig
from utils.proxy import get_proxy_server


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


def test_proxy_prefers_checkin_proxy_url(monkeypatch: pytest.MonkeyPatch):
	monkeypatch.setenv('CHECKIN_PROXY_URL', 'http://new-proxy:20808')
	monkeypatch.setenv('ANYROUTER_PROXY', 'http://legacy-proxy:20808')
	assert get_proxy_server() == 'http://new-proxy:20808'


def test_proxy_supports_legacy_anyrouter_proxy(monkeypatch: pytest.MonkeyPatch):
	monkeypatch.delenv('CHECKIN_PROXY_URL', raising=False)
	monkeypatch.setenv('ANYROUTER_PROXY', 'http://legacy-proxy:20808')
	assert get_proxy_server() == 'http://legacy-proxy:20808'


def test_account_state_key_is_stable_when_account_order_changes():
	account = AccountConfig(cookies={'session': 'token'}, api_user='123', provider='anyrouter', name='primary')
	assert get_account_state_key(account) == 'anyrouter:123'
