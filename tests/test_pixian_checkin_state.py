import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from pixian_overlay import app as checkin
from pixian_overlay.app import (
	CheckInOutcome,
	execute_check_in,
	format_check_in_notification,
	generate_balance_hash,
	get_account_state_key,
	get_skipped_account_detail,
	get_user_info,
	legacy_account_state_matches,
	mark_checked_in_today,
	parse_check_in_response,
	parse_cookies,
	prepare_cookies,
	run_check_in_requests,
	should_send_notification,
)
from pixian_overlay.utils.config import AccountConfig, AppConfig, ProviderConfig


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


def test_code_zero_without_explicit_success_is_not_a_success():
	response = MagicMock(status_code=200)
	response.json.return_value = {'code': 0}

	outcome = parse_check_in_response(response)

	assert outcome == CheckInOutcome('failed', 'Ambiguous response: code=0 without an explicit success marker')


def test_already_checked_response_is_handled_without_being_new_success():
	response = MagicMock(status_code=200)
	response.json.return_value = {'code': 0, 'message': '今日已签到'}

	outcome = parse_check_in_response(response)

	assert outcome.status == 'already_checked'
	assert outcome.handled


def test_explicit_success_response_is_confirmed():
	response = MagicMock(status_code=200)
	response.json.return_value = {'code': 0, 'message': '签到成功，获得 $25'}

	outcome = parse_check_in_response(response)

	assert outcome.status == 'success'
	assert outcome.handled


def test_execute_check_in_does_not_accept_ambiguous_code_zero():
	response = MagicMock(status_code=200)
	response.json.return_value = {'code': 0}
	client = MagicMock()
	client.post.return_value = response
	provider = ProviderConfig(name='anyrouter', domain='https://anyrouter.top')

	outcome = execute_check_in(client, 'account', provider, {})

	assert outcome.status == 'failed'


def test_notification_shows_balance_change_from_previous_snapshot():
	content = format_check_in_notification(
		{
			'name': '85976',
			'success': True,
			'before_quota': 6951.3,
			'before_used': 48.7,
			'after_quota': 6951.3,
			'after_used': 48.7,
			'check_in_reward': 0,
			'usage_increase': 0,
			'balance_change': 0,
			'baseline_balance_change': 25,
		},
	)

	assert '相比上次记录余额变化: +$25.00' in content
	assert '今日已签到，无变化' not in content


def test_notification_formats_usage_counter_reset_without_negative_consumption():
	content = format_check_in_notification(
		{
			'name': 'account',
			'success': True,
			'before_quota': 100,
			'before_used': 50,
			'after_quota': 100,
			'after_used': 0,
			'check_in_reward': 0,
			'usage_increase': -50,
			'balance_change': 0,
		}
	)

	assert '累计消耗计数器已重置' in content
	assert '期间消耗: $-50.00' not in content


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


def test_daily_state_write_is_atomic_when_replace_fails(tmp_path, monkeypatch):
	state_file = tmp_path / 'daily_checkin_state.json'
	state_file.write_text('{"date":"old"}', encoding='utf-8')
	monkeypatch.setattr(checkin, 'DAILY_CHECK_IN_STATE_FILE', str(state_file))
	monkeypatch.setattr(checkin.os, 'replace', MagicMock(side_effect=OSError('disk failure')))

	with pytest.raises(OSError, match='disk failure'):
		checkin.save_daily_check_in_state({'date': 'new'})

	assert json.loads(state_file.read_text(encoding='utf-8')) == {'date': 'old'}
	assert list(tmp_path.glob('.daily_checkin_state.json.*.tmp')) == []


def test_balance_snapshot_write_is_atomic_when_replace_fails(tmp_path, monkeypatch):
	balance_file = tmp_path / 'balance_hash.txt'
	balance_file.write_text('{"account":{"quota":100,"used":10}}', encoding='utf-8')
	monkeypatch.setattr(checkin, 'BALANCE_HASH_FILE', str(balance_file))
	monkeypatch.setattr(checkin.os, 'replace', MagicMock(side_effect=OSError('disk failure')))

	with pytest.raises(OSError, match='disk failure'):
		checkin.save_balance_snapshot({'account': {'quota': 125, 'used': 10}})

	assert json.loads(balance_file.read_text(encoding='utf-8')) == {'account': {'quota': 100, 'used': 10}}
	assert list(tmp_path.glob('.balance_hash.txt.*.tmp')) == []


def test_balance_snapshot_preserves_used_for_cross_run_reward_proof(tmp_path, monkeypatch):
	balance_file = tmp_path / 'balance_hash.txt'
	monkeypatch.setattr(checkin, 'BALANCE_HASH_FILE', str(balance_file))

	checkin.save_balance_snapshot({'account': {'quota': 100, 'used': 35}})

	assert checkin.load_balance_snapshot() == {'account': {'quota': 100.0, 'used': 35.0}}


def test_balance_snapshot_preserves_automatic_reward_evidence(tmp_path, monkeypatch):
	balance_file = tmp_path / 'balance_hash.txt'
	monkeypatch.setattr(checkin, 'BALANCE_HASH_FILE', str(balance_file))

	checkin.save_balance_snapshot(
		{
			'account': {
				'quota': 75,
				'used': 25,
				'evidence_quota': 100,
				'evidence_used': 0,
			}
		}
	)

	assert checkin.load_balance_snapshot() == {
		'account': {'quota': 75.0, 'used': 25.0, 'evidence_quota': 100.0, 'evidence_used': 0.0}
	}


def test_corrupt_daily_state_is_rejected_instead_of_silently_rechecking(tmp_path, monkeypatch):
	state_file = tmp_path / 'daily_checkin_state.json'
	state_file.write_text('{broken', encoding='utf-8')
	monkeypatch.setattr(checkin, 'DAILY_CHECK_IN_STATE_FILE', str(state_file))

	with pytest.raises(checkin.StateFileError, match='daily check-in state'):
		checkin.load_daily_check_in_state()


def test_corrupt_balance_snapshot_is_rejected(tmp_path, monkeypatch):
	balance_file = tmp_path / 'balance_hash.txt'
	balance_file.write_text('{broken', encoding='utf-8')
	monkeypatch.setattr(checkin, 'BALANCE_HASH_FILE', str(balance_file))

	with pytest.raises(checkin.StateFileError, match='balance snapshot'):
		checkin.load_balance_snapshot()


def test_daily_state_rejects_invalid_nested_types(tmp_path, monkeypatch):
	state_file = tmp_path / 'daily_checkin_state.json'
	state_file.write_text('{"date":"2026-08-17","accounts_checked":{"account":1}}', encoding='utf-8')
	monkeypatch.setattr(checkin, 'DAILY_CHECK_IN_STATE_FILE', str(state_file))

	with pytest.raises(checkin.StateFileError, match='accounts_checked'):
		checkin.load_daily_check_in_state()


def test_parse_cookies_drops_empty_or_non_string_entries():
	assert parse_cookies({'session': 'valid', '': 'bad', 'empty': '', 'number': 123}) == {'session': 'valid'}
	assert parse_cookies('session=valid; =bad; empty=') == {'session': 'valid'}


def test_user_info_rejects_non_finite_quota():
	client = MagicMock()
	response = MagicMock(status_code=200)
	response.json.return_value = {
		'success': True,
		'data': {'quota': float('nan'), 'used_quota': 0},
	}
	client.get.return_value = response

	result = get_user_info(client, {}, 'https://example.test/api/user/self')

	assert result['success'] is False
	assert 'invalid quota values' in result['error']


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


async def test_prepare_cookies_retries_until_all_required_waf_cookies_are_available(monkeypatch):
	attempts = []

	async def fake_waf_cookies(*args, **kwargs):
		attempts.append(1)
		if len(attempts) == 1:
			return {'acw_tc': 'partial'}
		return {'acw_tc': 'fresh-waf', 'acw_sc__v2': 'fresh-challenge'}

	monkeypatch.setattr(checkin, 'get_waf_cookies_with_browser', fake_waf_cookies)
	provider = ProviderConfig(
		name='anyrouter',
		domain='https://anyrouter.top',
		bypass_method='waf_cookies',
		waf_cookie_names=['acw_tc', 'acw_sc__v2'],
	)

	cookies = await prepare_cookies('account', provider, {'session': 'current-session'})

	assert len(attempts) == 2
	assert cookies == {
		'session': 'current-session',
		'acw_tc': 'fresh-waf',
		'acw_sc__v2': 'fresh-challenge',
	}


async def test_missing_waf_cookies_abort_before_any_api_request(monkeypatch):
	async def missing_waf_cookies(*args, **kwargs):
		return {'acw_tc': 'partial'}

	def unexpected_api_request(*args, **kwargs):
		raise AssertionError('API requests must not run with incomplete WAF cookies')

	monkeypatch.setattr(checkin, 'get_waf_cookies_with_browser', missing_waf_cookies)
	monkeypatch.setattr(checkin, 'run_check_in_requests', unexpected_api_request)
	provider = ProviderConfig(
		name='anyrouter',
		domain='https://anyrouter.top',
		bypass_method='waf_cookies',
		waf_cookie_names=['acw_tc', 'acw_sc__v2'],
	)
	account = AccountConfig(cookies={'session': 'current-session'}, provider='anyrouter', name='account')

	result = await checkin.check_in_account(account, 0, AppConfig(providers={'anyrouter': provider}))

	assert result == (False, None, None)


@pytest.mark.parametrize(
	'payload, expected_status',
	[
		({'success': True, 'message': '签到成功'}, 'success'),
		({'code': 0, 'message': '今日已签到'}, 'already_checked'),
	],
)
def test_explicit_check_in_outcome_survives_post_balance_query_failure(monkeypatch, payload, expected_status):
	user_info_before = {
		'success': True,
		'quota': 100.0,
		'used_quota': 0.0,
		'display': 'quota=100',
	}
	user_info_after = {'success': False, 'error': 'temporary failure'}
	monkeypatch.setattr(checkin, 'get_user_info', MagicMock(side_effect=[user_info_before, user_info_after]))

	client = MagicMock()
	response = MagicMock(status_code=200)
	response.json.return_value = payload
	client.post.return_value = response
	client_context = MagicMock()
	client_context.__enter__.return_value = client
	monkeypatch.setattr(checkin.httpx, 'Client', MagicMock(return_value=client_context))
	provider = ProviderConfig(name='anyrouter', domain='https://anyrouter.top')
	account = AccountConfig(cookies={'session': 'current-session'}, provider='anyrouter')

	success, before, after = run_check_in_requests({'session': 'current-session'}, account, 'account', provider)

	assert success is True
	assert before == user_info_before
	assert after is not None
	assert after['success'] is True
	assert after['quota'] == 100.0
	assert after['_check_in_status'] == expected_status


def test_checked_cookie_account_refreshes_user_info_only_once(monkeypatch):
	user_info = {
		'success': True,
		'quota': 100.0,
		'used_quota': 0.0,
		'display': 'quota=100',
	}
	get_user_info = MagicMock(return_value=user_info)
	monkeypatch.setattr(checkin, 'get_user_info', get_user_info)
	client_context = MagicMock()
	client_context.__enter__.return_value = MagicMock()
	monkeypatch.setattr(checkin.httpx, 'Client', MagicMock(return_value=client_context))
	provider = ProviderConfig(name='anyrouter', domain='https://anyrouter.top')
	account = AccountConfig(cookies={'session': 'current-session'}, provider='anyrouter')

	success, before, after = run_check_in_requests(
		{'session': 'current-session'}, account, 'account', provider, skip_check_in=True
	)

	assert success is True
	assert before == user_info
	assert after is not None
	assert after['_check_in_status'] == 'already_checked'
	get_user_info.assert_called_once()
