import json
from unittest.mock import MagicMock

import pytest

from pixian_overlay import app as checkin
from pixian_overlay.utils.config import AccountConfig, AppConfig


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
	monkeypatch.setattr(checkin, 'DAILY_CHECK_IN_STATE_FILE', str(state_file))
	monkeypatch.setattr(checkin, 'BALANCE_HASH_FILE', str(balance_file))
	monkeypatch.setattr(checkin.AppConfig, 'load_from_env', staticmethod(lambda: AppConfig(providers={})))
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
		'anyrouter:one': {'quota': 125},
		'anyrouter:two': {'quota': 200},
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
