"""验证手动签到是否真正产生奖励的运行时补丁。"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from typing import Any, cast

BALANCE_VERIFY_ATTEMPTS = 3
BALANCE_VERIFY_INTERVAL_SECONDS = 2
UNVERIFIED_SUCCESS_ERROR = '签到接口返回成功，但余额和累计消耗未证明奖励到账；本次不记录为已签到'


def _reward_amount(before: dict | None, after: dict | None) -> float | None:
	"""计算签到奖励；累计消耗重置时只采信正向余额增量。"""
	if not before or not after or not before.get('success') or not after.get('success'):
		return None
	before_quota = before.get('quota')
	before_used = before.get('used_quota')
	after_quota = after.get('quota')
	after_used = after.get('used_quota')
	values = (before_quota, before_used, after_quota, after_used)
	if not all(
		isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))
		for value in values
	):
		return None
	before_quota = cast(int | float, before_quota)
	before_used = cast(int | float, before_used)
	after_quota = cast(int | float, after_quota)
	after_used = cast(int | float, after_used)
	quota_delta = float(after_quota - before_quota)
	used_delta = float(after_used - before_used)
	if used_delta < 0:
		# 累计消耗可能按月或后台策略归零。负向 used 不能抵消已观察到的余额增加，
		# 但余额没有增加时也不能仅凭计数器重置确认签到。
		return max(quota_delta, 0.0)
	return quota_delta + used_delta


def _confirmed_result(
	result: tuple[bool, dict | None, dict | None],
	provider_config: Any,
	*,
	skip_check_in: bool,
) -> bool:
	"""只对本次主动手动签到的 success 响应要求奖励证据。"""
	success, before, after = result
	if not success or skip_check_in or not provider_config.needs_manual_check_in():
		return True
	status = after.get('_check_in_status') if after else None
	if status == 'already_checked':
		return True
	if status != 'success':
		return False
	reward = _reward_amount(before, after)
	return reward is not None and reward > 0


def _failed_unverified_result(
	result: tuple[bool, dict | None, dict | None], account_name: str
) -> tuple[bool, dict | None, dict | None]:
	_, before, after = result
	failed_after = dict(after or {})
	failed_after.update(
		{
			'success': False,
			'error': UNVERIFIED_SUCCESS_ERROR,
			'_check_in_status': 'failed',
		}
	)
	print(f'[FAILED] {account_name}: {UNVERIFIED_SUCCESS_ERROR}')
	return False, before, failed_after


def build_verified_runner(original: Callable[..., tuple[bool, dict | None, dict | None]]) -> Callable[..., Any]:
	"""包装上游请求函数；不复制或修改上游实现。"""
	if getattr(original, '_pixian_actual_checkin_overlay', False) is True:
		return original

	def verified_runner(*args: Any, **kwargs: Any) -> tuple[bool, dict | None, dict | None]:
		result = original(*args, **kwargs)
		provider_config = args[3] if len(args) > 3 else kwargs['provider_config']
		account_name = args[2] if len(args) > 2 else kwargs.get('account_name', 'account')
		skip_check_in = bool(kwargs.get('skip_check_in', False))
		if _confirmed_result(result, provider_config, skip_check_in=skip_check_in):
			return result

		_, before, after = result
		status = after.get('_check_in_status') if after else None
		if status != 'success':
			return _failed_unverified_result(result, str(account_name))

		refresh_kwargs = dict(kwargs)
		refresh_kwargs['skip_check_in'] = True
		for attempt in range(1, BALANCE_VERIFY_ATTEMPTS + 1):
			time.sleep(BALANCE_VERIFY_INTERVAL_SECONDS)
			refresh_success, _, refreshed_after = original(*args, **refresh_kwargs)
			reward = _reward_amount(before, refreshed_after)
			if refresh_success and reward is not None and reward > 0:
				confirmed_after = dict(refreshed_after or {})
				confirmed_after['_check_in_status'] = 'success'
				print(f'[SUCCESS] {account_name}: Reward confirmed by delayed balance query (attempt {attempt})')
				return True, before, confirmed_after

		return _failed_unverified_result(result, str(account_name))

	verified_runner._pixian_actual_checkin_overlay = True  # type: ignore[attr-defined]
	return verified_runner


def install(checkin_module: Any) -> None:
	"""把外挂安装到已导入的上游 checkin 模块。"""
	checkin_module.run_check_in_requests = build_verified_runner(checkin_module.run_check_in_requests)
