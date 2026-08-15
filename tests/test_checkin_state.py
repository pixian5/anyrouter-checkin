import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import checkin
from checkin import (
	generate_balance_hash,
	get_account_state_key,
	get_skipped_account_detail,
	legacy_account_state_matches,
	mark_checked_in_today,
	prepare_cookies,
	should_send_notification,
)
from utils.config import AccountConfig, ProviderConfig


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


def test_balance_hash_treats_integer_and_float_quotas_as_equal():
	assert generate_balance_hash({'account': {'quota': 100}}) == generate_balance_hash({'account': {'quota': 100.0}})


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


def test_account_state_key_prefers_name_over_rotating_cookies():
	first = AccountConfig(cookies={'session': 'first'}, provider='anyrouter', name='primary')
	second = AccountConfig(cookies={'session': 'second'}, provider='anyrouter', name='primary')
	assert get_account_state_key(first) == get_account_state_key(second) == 'anyrouter:primary'


def test_legacy_account_state_requires_matching_identity():
	today = checkin.datetime.now().strftime('%Y-%m-%d')
	state = {
		'date': today,
		'accounts_checked': {'account_1': True},
		'details': {'account_1': {'name': 'first', 'provider': 'anyrouter'}},
	}

	assert legacy_account_state_matches(state, 'account_1', 'first', 'anyrouter')
	assert not legacy_account_state_matches(state, 'account_1', 'second', 'anyrouter')


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


def test_mark_checked_in_keeps_other_provider_details(tmp_path, monkeypatch):
	state_file = tmp_path / 'daily_checkin_state.json'
	monkeypatch.setattr(checkin, 'DAILY_CHECK_IN_STATE_FILE', str(state_file))
	checkin.save_daily_check_in_state(
		{
			'date': checkin.datetime.now().strftime('%Y-%m-%d'),
			'details': {'agentrouter:one': {'success': True, 'after_quota': 450}},
		}
	)

	mark_checked_in_today(
		{'anyrouter:two': {'success': True, 'after_quota': 6926.3}},
		'now',
		provider='anyrouter',
		account_keys=['anyrouter:two'],
	)

	assert checkin.load_daily_check_in_state()['details'] == {
		'agentrouter:one': {'success': True, 'after_quota': 450},
		'anyrouter:two': {'success': True, 'after_quota': 6926.3},
	}


def test_skipped_account_detail_uses_saved_balance_without_repeating_reward():
	detail = get_skipped_account_detail(
		{
			'details': {
				'anyrouter:85976': {
					'success': True,
					'before_quota': 6901.3,
					'after_quota': 6926.3,
					'before_used': 48.7,
					'after_used': 48.7,
					'balance_change': 25,
					'check_in_reward': 25,
				}
			}
		},
		'anyrouter:85976',
		'account_3',
		'85976',
		'anyrouter',
	)

	assert detail['success'] is True
	assert detail['skipped'] is True
	assert detail['after_quota'] == 6926.3
	assert detail['balance_change'] == 0
	assert detail['check_in_reward'] == 0


async def test_prepare_cookies_keeps_session_and_prefers_fresh_waf_cookies(monkeypatch):
	async def fake_waf_cookies(*args, **kwargs):
		return {'acw_tc': 'fresh-waf', 'cdn_sec_tc': 'fresh-cdn'}

	monkeypatch.setattr(checkin, 'get_waf_cookies_with_browser', fake_waf_cookies)
	provider = ProviderConfig(
		name='anyrouter',
		domain='https://anyrouter.top',
		bypass_method='waf_cookies',
		waf_cookie_names=['acw_tc', 'cdn_sec_tc'],
	)

	cookies = await prepare_cookies(
		'account',
		provider,
		{'session': 'current-session', 'acw_tc': 'stale-waf', 'cdn_sec_tc': 'stale-cdn'},
	)

	assert cookies == {
		'session': 'current-session',
		'acw_tc': 'fresh-waf',
		'cdn_sec_tc': 'fresh-cdn',
	}
