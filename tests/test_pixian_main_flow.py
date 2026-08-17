import json
from unittest.mock import MagicMock

import pytest

from pixian_overlay import app as checkin
from pixian_overlay.utils.config import AccountConfig, AppConfig, ProviderConfig


def _account(api_user: str) -> AccountConfig:
	return AccountConfig(cookies={'session': f'session-{api_user}'}, api_user=api_user, provider='anyrouter')


def _email_account(name: str) -> AccountConfig:
	return AccountConfig(
		cookies=None,
		provider='agentrouter',
		name=name,
		email=f'{name}@example.com',
		password='secret',
	)


def _user_info(quota: float, used: float = 0) -> dict:
	return {
		'success': True,
		'quota': quota,
		'used_quota': used,
		'display': f'quota={quota}',
	}


def _configure_main(monkeypatch, tmp_path, accounts: list[AccountConfig]) -> MagicMock:
	state_file = tmp_path / 'daily_checkin_state.json'
	balance_file = tmp_path / 'balance_hash.txt'
	history_file = tmp_path / 'checkin_history.sqlite3'
	monkeypatch.setattr(checkin, 'DAILY_CHECK_IN_STATE_FILE', str(state_file))
	monkeypatch.setattr(checkin, 'BALANCE_HASH_FILE', str(balance_file))
	monkeypatch.setattr(checkin, 'CHECKIN_HISTORY_DATABASE_FILE', str(history_file))
	providers = {
		'anyrouter': ProviderConfig(name='anyrouter', domain='https://anyrouter.top'),
		'agentrouter': ProviderConfig(name='agentrouter', domain='https://agentrouter.org', sign_in_path=None),
	}
	monkeypatch.setattr(checkin.AppConfig, 'load_from_env', staticmethod(lambda: AppConfig(providers=providers)))
	monkeypatch.setattr(checkin, 'load_accounts_config', lambda: accounts)
	monkeypatch.setattr(checkin, 'is_debug_enabled', lambda: False)
	push_message = MagicMock()
	monkeypatch.setattr(checkin.notify, 'push_message', push_message)
	return push_message


async def test_main_skips_notification_when_all_accounts_were_checked_in(monkeypatch, tmp_path, capsys):
	accounts = [_account('one'), _account('two')]
	push_message = _configure_main(monkeypatch, tmp_path, accounts)
	today = checkin.datetime.now().strftime('%Y-%m-%d')
	checkin.save_daily_check_in_state(
		{
			'date': today,
			'accounts_checked': {'anyrouter:one': True, 'anyrouter:two': True},
			'details': {
				'anyrouter:one': {
					'name': 'one',
					'provider': 'anyrouter',
					'success': True,
					'after_quota': None,
					'after_used': None,
				},
				'anyrouter:two': {
					'name': 'two',
					'provider': 'anyrouter',
					'success': True,
					'after_quota': 200,
					'after_used': 0,
				},
			},
		}
	)
	checkin.save_balance_snapshot({'anyrouter:one': {'quota': 100}, 'anyrouter:two': {'quota': 200}})

	async def read_only_check_in(account, *args, **kwargs):
		assert kwargs.get('skip_check_in') is True
		quota = 100 if account.api_user == 'one' else 200
		return True, _user_info(quota), _user_info(quota)

	monkeypatch.setattr(checkin, 'check_in_account', read_only_check_in)

	with pytest.raises(SystemExit) as exc_info:
		await checkin.main()

	assert exc_info.value.code == 0
	push_message.assert_not_called()
	output = capsys.readouterr().out
	assert 'Balances unchanged and no check-in failures, notification skipped' in output
	assert 'Balance snapshot incomplete' not in output


async def test_main_notifies_when_read_only_refresh_finds_external_balance_change(monkeypatch, tmp_path):
	accounts = [_account('one'), _account('two')]
	push_message = _configure_main(monkeypatch, tmp_path, accounts)
	today = checkin.datetime.now().strftime('%Y-%m-%d')
	checkin.save_daily_check_in_state(
		{
			'date': today,
			'accounts_checked': {'anyrouter:one': True, 'anyrouter:two': True},
			'details': {
				'anyrouter:one': {'name': 'one', 'provider': 'anyrouter', 'success': True, 'after_quota': 100},
				'anyrouter:two': {'name': 'two', 'provider': 'anyrouter', 'success': True, 'after_quota': 200},
			},
		}
	)
	checkin.save_balance_snapshot({'anyrouter:one': {'quota': 100}, 'anyrouter:two': {'quota': 200}})

	async def read_only_check_in(account, *args, **kwargs):
		assert kwargs.get('skip_check_in') is True
		quota = 125 if account.api_user == 'one' else 200
		return True, _user_info(quota), _user_info(quota)

	monkeypatch.setattr(checkin, 'check_in_account', read_only_check_in)

	with pytest.raises(SystemExit) as exc_info:
		await checkin.main()

	assert exc_info.value.code == 0
	push_message.assert_called_once()
	_, content = push_message.call_args.args[:2]
	assert '相比上次记录余额变化: +$25.00' in content


async def test_main_does_not_access_network_for_checked_email_accounts(monkeypatch, tmp_path, capsys):
	accounts = [_email_account('one'), _email_account('two')]
	push_message = _configure_main(monkeypatch, tmp_path, accounts)
	today = checkin.datetime.now().strftime('%Y-%m-%d')
	account_keys = [checkin.get_account_state_key(account) for account in accounts]
	checkin.save_daily_check_in_state(
		{
			'date': today,
			'accounts_checked': {key: True for key in account_keys},
			'details': {
				account_keys[0]: {
					'name': 'one',
					'provider': 'agentrouter',
					'success': True,
					'after_quota': 475,
					'after_used': 0,
				},
				account_keys[1]: {
					'name': 'two',
					'provider': 'agentrouter',
					'success': True,
					'after_quota': 475,
					'after_used': 0,
				},
			},
		}
	)
	checkin.save_balance_snapshot({key: {'quota': 475} for key in account_keys})

	async def unexpected_check_in(*args, **kwargs):
		raise AssertionError('checked email accounts must not start browser login or access the network')

	monkeypatch.setattr(checkin, 'check_in_account', unexpected_check_in)

	with pytest.raises(SystemExit) as exc_info:
		await checkin.main()

	assert exc_info.value.code == 0
	push_message.assert_not_called()
	output = capsys.readouterr().out
	assert output.count('browser login skipped, retaining saved balance') == 2


async def test_main_sends_one_notification_when_one_balance_changes(monkeypatch, tmp_path):
	accounts = [_account('one'), _account('two')]
	push_message = _configure_main(monkeypatch, tmp_path, accounts)
	checkin.save_balance_snapshot({'anyrouter:one': {'quota': 100}, 'anyrouter:two': {'quota': 200}})

	async def fake_check_in(account, *args, **kwargs):
		if account.api_user == 'one':
			return True, _user_info(100), _user_info(125)
		return True, _user_info(200), _user_info(200)

	monkeypatch.setattr(checkin, 'check_in_account', fake_check_in)

	with pytest.raises(SystemExit) as exc_info:
		await checkin.main()

	assert exc_info.value.code == 0
	push_message.assert_called_once()
	title, content = push_message.call_args.args[:2]
	assert title == '✅ 签到全部成功 (2/2)'
	assert 'one' in content
	assert 'two' in content
	assert json.loads((tmp_path / 'balance_hash.txt').read_text(encoding='utf-8')) == {
		'anyrouter:one': {'quota': 125.0, 'used': 0.0},
		'anyrouter:two': {'quota': 200.0, 'used': 0.0},
	}


async def test_main_returns_failure_when_only_some_accounts_succeed(monkeypatch, tmp_path):
	accounts = [_account('one'), _account('two')]
	push_message = _configure_main(monkeypatch, tmp_path, accounts)

	async def fake_check_in(account, *args, **kwargs):
		if account.api_user == 'one':
			return True, _user_info(100), _user_info(100)
		return False, None, None

	monkeypatch.setattr(checkin, 'check_in_account', fake_check_in)

	with pytest.raises(SystemExit) as exc_info:
		await checkin.main()

	assert exc_info.value.code == 1
	push_message.assert_called_once()


async def test_main_does_not_mark_ambiguous_check_in_as_checked(monkeypatch, tmp_path):
	accounts = [_account('one')]
	push_message = _configure_main(monkeypatch, tmp_path, accounts)

	async def ambiguous_check_in(*args, **kwargs):
		return False, _user_info(100), {'success': False, 'error': 'Ambiguous response'}

	monkeypatch.setattr(checkin, 'check_in_account', ambiguous_check_in)

	with pytest.raises(SystemExit) as exc_info:
		await checkin.main()

	assert exc_info.value.code == 1
	assert not (tmp_path / 'daily_checkin_state.json').exists()
	push_message.assert_called_once()


async def test_failed_first_attempt_is_retried_and_only_confirmed_success_is_saved(monkeypatch, tmp_path):
	account = _account('one')
	_configure_main(monkeypatch, tmp_path, [account])
	attempts = 0

	async def retrying_check_in(*args, **kwargs):
		nonlocal attempts
		attempts += 1
		if attempts == 1:
			return False, _user_info(100), {'success': False, 'error': 'Reward not confirmed'}
		after = _user_info(125)
		after['_check_in_status'] = 'success'
		return True, _user_info(100), after

	monkeypatch.setattr(checkin, 'check_in_account', retrying_check_in)

	with pytest.raises(SystemExit) as first_exit:
		await checkin.main()
	assert first_exit.value.code == 1
	assert not checkin.has_checked_in_today(account_key='anyrouter:one')

	with pytest.raises(SystemExit) as second_exit:
		await checkin.main()
	assert second_exit.value.code == 0
	assert attempts == 2
	assert checkin.has_checked_in_today(account_key='anyrouter:one')


async def test_automatic_provider_requires_reward_evidence_from_previous_snapshot(monkeypatch, tmp_path):
	account = _email_account('one')
	push_message = _configure_main(monkeypatch, tmp_path, [account])
	account_key = checkin.get_account_state_key(account)
	checkin.save_balance_snapshot({account_key: {'quota': 500, 'used': 0}})

	async def unchanged_auto_check_in(*args, **kwargs):
		after = _user_info(500, 0)
		after['_check_in_status'] = 'success'
		return True, _user_info(500, 0), after

	monkeypatch.setattr(checkin, 'check_in_account', unchanged_auto_check_in)

	with pytest.raises(SystemExit) as exc_info:
		await checkin.main()

	assert exc_info.value.code == 1
	assert not checkin.has_checked_in_today(account_key=account_key)
	push_message.assert_called_once()
	snapshot = checkin.load_balance_snapshot()[account_key]
	assert snapshot['quota'] == 500
	assert snapshot['evidence_quota'] == 500
	assert snapshot['evidence_used'] == 0


async def test_automatic_provider_can_confirm_reward_on_later_retry(monkeypatch, tmp_path):
	account = _email_account('one')
	_configure_main(monkeypatch, tmp_path, [account])
	account_key = checkin.get_account_state_key(account)
	attempt = 0

	async def delayed_auto_reward(*args, **kwargs):
		nonlocal attempt
		attempt += 1
		after = _user_info(500, 0 if attempt == 1 else 25)
		after['_check_in_status'] = 'success'
		return True, after, after

	monkeypatch.setattr(checkin, 'check_in_account', delayed_auto_reward)

	with pytest.raises(SystemExit) as first_exit:
		await checkin.main()
	assert first_exit.value.code == 1
	assert not checkin.has_checked_in_today(account_key=account_key)

	with pytest.raises(SystemExit) as second_exit:
		await checkin.main()
	assert second_exit.value.code == 0
	assert checkin.has_checked_in_today(account_key=account_key)


async def test_automatic_provider_confirms_reward_when_consumption_offsets_quota(monkeypatch, tmp_path):
	account = _email_account('one')
	_configure_main(monkeypatch, tmp_path, [account])
	account_key = checkin.get_account_state_key(account)
	checkin.save_balance_snapshot({account_key: {'quota': 500, 'used': 0}})

	async def rewarded_auto_check_in(*args, **kwargs):
		after = _user_info(500, 25)
		after['_check_in_status'] = 'success'
		return True, after, after

	monkeypatch.setattr(checkin, 'check_in_account', rewarded_auto_check_in)

	with pytest.raises(SystemExit) as exc_info:
		await checkin.main()

	assert exc_info.value.code == 0
	assert checkin.has_checked_in_today(account_key=account_key)


async def test_automatic_provider_does_not_treat_consumption_alone_as_reward(monkeypatch, tmp_path):
	account = _email_account('one')
	_configure_main(monkeypatch, tmp_path, [account])
	account_key = checkin.get_account_state_key(account)
	checkin.save_balance_snapshot({account_key: {'quota': 500, 'used': 0}})

	async def consumption_only(*args, **kwargs):
		after = _user_info(475, 25)
		after['_check_in_status'] = 'success'
		return True, after, after

	monkeypatch.setattr(checkin, 'check_in_account', consumption_only)

	with pytest.raises(SystemExit) as exc_info:
		await checkin.main()

	assert exc_info.value.code == 1
	assert not checkin.has_checked_in_today(account_key=account_key)
	snapshot = checkin.load_balance_snapshot()[account_key]
	assert snapshot['quota'] == 475
	assert snapshot['used'] == 25
	assert snapshot['evidence_quota'] == 500
	assert snapshot['evidence_used'] == 0


async def test_duplicate_account_state_keys_abort_before_network(monkeypatch, tmp_path):
	accounts = [_account('same'), _account('same')]
	push_message = _configure_main(monkeypatch, tmp_path, accounts)
	check_in_account = MagicMock()
	monkeypatch.setattr(checkin, 'check_in_account', check_in_account)

	with pytest.raises(SystemExit) as exc_info:
		await checkin.main()

	assert exc_info.value.code == 1
	check_in_account.assert_not_called()
	push_message.assert_called_once()


async def test_unknown_provider_aborts_all_accounts_before_network(monkeypatch, tmp_path):
	accounts = [_account('valid'), AccountConfig(cookies={'session': 'x'}, api_user='bad', provider='missing')]
	push_message = _configure_main(monkeypatch, tmp_path, accounts)
	check_in_account = MagicMock()
	monkeypatch.setattr(checkin, 'check_in_account', check_in_account)

	with pytest.raises(SystemExit) as exc_info:
		await checkin.main()

	assert exc_info.value.code == 1
	check_in_account.assert_not_called()
	push_message.assert_called_once()


async def test_corrupt_state_aborts_before_network(monkeypatch, tmp_path):
	account = _account('one')
	push_message = _configure_main(monkeypatch, tmp_path, [account])
	(tmp_path / 'daily_checkin_state.json').write_text('{broken', encoding='utf-8')
	check_in_account = MagicMock()
	monkeypatch.setattr(checkin, 'check_in_account', check_in_account)

	with pytest.raises(SystemExit) as exc_info:
		await checkin.main()

	assert exc_info.value.code == 1
	check_in_account.assert_not_called()
	push_message.assert_called_once()


async def test_state_write_failure_keeps_old_balance_baseline_and_returns_failure(monkeypatch, tmp_path):
	account = _account('one')
	push_message = _configure_main(monkeypatch, tmp_path, [account])
	checkin.save_balance_snapshot({'anyrouter:one': {'quota': 100, 'used': 0}})

	async def successful_check_in(*args, **kwargs):
		after = _user_info(125, 0)
		after['_check_in_status'] = 'success'
		return True, _user_info(100, 0), after

	monkeypatch.setattr(checkin, 'check_in_account', successful_check_in)
	monkeypatch.setattr(checkin, 'save_daily_check_in_state', MagicMock(side_effect=OSError('disk full')))

	with pytest.raises(SystemExit) as exc_info:
		await checkin.main()

	assert exc_info.value.code == 1
	assert checkin.load_balance_snapshot() == {'anyrouter:one': {'quota': 100.0, 'used': 0.0}}
	push_message.assert_called_once()
	assert '状态保存失败' in push_message.call_args.args[1]


async def test_main_records_every_account_result_in_sqlite_history(monkeypatch, tmp_path):
	account = _account('one')
	_configure_main(monkeypatch, tmp_path, [account])

	async def successful_check_in(*args, **kwargs):
		after = _user_info(125, 10)
		after['_check_in_status'] = 'success'
		return True, _user_info(100, 5), after

	monkeypatch.setattr(checkin, 'check_in_account', successful_check_in)

	with pytest.raises(SystemExit) as exc_info:
		await checkin.main()

	assert exc_info.value.code == 0
	connection = checkin.sqlite3.connect(tmp_path / 'checkin_history.sqlite3')
	try:
		run = connection.execute(
			'SELECT execution_status, accounts_total, accounts_succeeded, has_failures FROM checkin_runs'
		).fetchone()
		account_row = connection.execute(
			"""SELECT account_key, name, provider, success, skipped, before_quota, before_used,
			   after_quota, after_used, check_in_reward, usage_increase, balance_change, check_in_status
			   FROM checkin_account_records"""
		).fetchone()
	finally:
		connection.close()

	assert run == ('completed', 1, 1, 0)
	assert account_row == (
		'anyrouter:one',
		'one',
		'anyrouter',
		1,
		0,
		100.0,
		5.0,
		125.0,
		10.0,
		30.0,
		5.0,
		25.0,
		'success',
	)


async def test_history_database_must_be_available_before_account_requests(monkeypatch, tmp_path):
	account = _account('one')
	push_message = _configure_main(monkeypatch, tmp_path, [account])
	check_in_account = MagicMock()
	monkeypatch.setattr(checkin, 'check_in_account', check_in_account)
	monkeypatch.setattr(
		checkin,
		'initialize_checkin_history_database',
		MagicMock(side_effect=checkin.CheckInHistoryError('database locked')),
	)

	with pytest.raises(SystemExit) as exc_info:
		await checkin.main()

	assert exc_info.value.code == 1
	check_in_account.assert_not_called()
	push_message.assert_called_once()
