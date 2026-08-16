from types import SimpleNamespace
from unittest.mock import MagicMock

from pixian_overlay import actual_checkin


def _user_info(quota: float, used: float = 0, *, status: str = 'success') -> dict:
	return {
		'success': True,
		'quota': quota,
		'used_quota': used,
		'_check_in_status': status,
	}


def _provider(*, manual: bool = True):
	return SimpleNamespace(needs_manual_check_in=lambda: manual)


def test_overlay_accepts_success_only_after_positive_reward(monkeypatch):
	monkeypatch.setattr(actual_checkin.time, 'sleep', MagicMock())
	original = MagicMock(return_value=(True, _user_info(100), _user_info(125)))
	runner = actual_checkin.build_verified_runner(original)

	result = runner({}, object(), 'account', _provider())

	assert result[0] is True
	assert result[2]['quota'] == 125
	original.assert_called_once()


def test_overlay_detects_reward_when_consumption_offsets_balance_gain(monkeypatch):
	monkeypatch.setattr(actual_checkin.time, 'sleep', MagicMock())
	before = _user_info(100, used=10)
	after = _user_info(100, used=35)
	original = MagicMock(return_value=(True, before, after))
	runner = actual_checkin.build_verified_runner(original)

	result = runner({}, object(), 'account', _provider())

	assert actual_checkin._reward_amount(before, after) == 25
	assert result[0] is True
	original.assert_called_once()


def test_overlay_does_not_treat_consumption_alone_as_reward(monkeypatch):
	monkeypatch.setattr(actual_checkin.time, 'sleep', MagicMock())
	before = _user_info(100, used=10)
	after = _user_info(75, used=35)
	original = MagicMock(return_value=(True, before, after))
	runner = actual_checkin.build_verified_runner(original)

	result = runner({}, object(), 'account', _provider())

	assert actual_checkin._reward_amount(before, after) == 0
	assert result[0] is False


def test_overlay_rejects_success_text_when_reward_never_arrives(monkeypatch):
	monkeypatch.setattr(actual_checkin.time, 'sleep', MagicMock())
	before = _user_info(100)
	unchanged = _user_info(100)
	original = MagicMock(return_value=(True, before, unchanged))
	runner = actual_checkin.build_verified_runner(original)

	result = runner({}, object(), 'account', _provider())

	assert result[0] is False
	assert result[2]['success'] is False
	assert result[2]['_check_in_status'] == 'failed'
	assert actual_checkin.UNVERIFIED_SUCCESS_ERROR in result[2]['error']
	assert original.call_count == 1 + actual_checkin.BALANCE_VERIFY_ATTEMPTS


def test_overlay_accepts_reward_observed_by_delayed_refresh(monkeypatch):
	monkeypatch.setattr(actual_checkin.time, 'sleep', MagicMock())
	before = _user_info(100)
	original = MagicMock(
		side_effect=[
			(True, before, _user_info(100)),
			(True, _user_info(100), _user_info(100)),
			(True, _user_info(100), _user_info(125, status='already_checked')),
		]
	)
	runner = actual_checkin.build_verified_runner(original)

	result = runner({}, object(), 'account', _provider())

	assert result[0] is True
	assert result[2]['quota'] == 125
	assert result[2]['_check_in_status'] == 'success'
	assert original.call_count == 3


def test_overlay_preserves_explicit_already_checked_response(monkeypatch):
	monkeypatch.setattr(actual_checkin.time, 'sleep', MagicMock())
	original = MagicMock(return_value=(True, _user_info(100), _user_info(100, status='already_checked')))
	runner = actual_checkin.build_verified_runner(original)

	result = runner({}, object(), 'account', _provider())

	assert result[0] is True
	original.assert_called_once()


def test_overlay_rejects_negative_or_zero_reward(monkeypatch):
	monkeypatch.setattr(actual_checkin.time, 'sleep', MagicMock())
	before = _user_info(100)
	original = MagicMock(return_value=(True, before, _user_info(99)))
	runner = actual_checkin.build_verified_runner(original)

	result = runner({}, object(), 'account', _provider())

	assert result[0] is False
	assert original.call_count == 1 + actual_checkin.BALANCE_VERIFY_ATTEMPTS


def test_overlay_rejects_manual_success_without_explicit_status(monkeypatch):
	monkeypatch.setattr(actual_checkin.time, 'sleep', MagicMock())
	before = _user_info(100)
	after = _user_info(125)
	after.pop('_check_in_status')
	original = MagicMock(return_value=(True, before, after))
	runner = actual_checkin.build_verified_runner(original)

	result = runner({}, object(), 'account', _provider())

	assert result[0] is False
	assert original.call_count == 1


def test_overlay_ignores_failed_delayed_balance_refresh(monkeypatch):
	monkeypatch.setattr(actual_checkin.time, 'sleep', MagicMock())
	before = _user_info(100)
	original = MagicMock(
		side_effect=[
			(True, before, _user_info(100)),
			(False, before, _user_info(125, status='already_checked')),
			(False, before, _user_info(125, status='already_checked')),
			(False, before, _user_info(125, status='already_checked')),
		]
	)
	runner = actual_checkin.build_verified_runner(original)

	result = runner({}, object(), 'account', _provider())

	assert result[0] is False


def test_overlay_does_not_change_automatic_provider_or_read_only_refresh(monkeypatch):
	monkeypatch.setattr(actual_checkin.time, 'sleep', MagicMock())
	result = (True, _user_info(100), _user_info(100))
	original = MagicMock(return_value=result)
	runner = actual_checkin.build_verified_runner(original)

	assert runner({}, object(), 'automatic', _provider(manual=False)) == result
	assert runner({}, object(), 'read-only', _provider(), skip_check_in=True) == result
	assert original.call_count == 2


def test_overlay_install_is_idempotent():
	module = SimpleNamespace(run_check_in_requests=MagicMock())

	actual_checkin.install(module)
	first = module.run_check_in_requests
	actual_checkin.install(module)

	assert module.run_check_in_requests is first
