#!/usr/bin/env python3
"""
AnyRouter.top 自动签到脚本
"""

import asyncio
import hashlib
import json
import math
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, cast

if hasattr(sys.stdout, 'reconfigure'):
	sys.stdout.reconfigure(line_buffering=True)
if hasattr(sys.stderr, 'reconfigure'):
	sys.stderr.reconfigure(line_buffering=True)

import httpx
from dotenv import load_dotenv

from pixian_overlay.actual_checkin import _reward_amount
from pixian_overlay.utils.browser import (
	BrowserLoginResult,
	has_session_cookie,
	is_logged_in,
	launch_login_context,
	load_browser_login_settings,
	login_with_email_form,
	navigate_login_page,
	prepare_browser_page,
	save_login_screenshot,
	verify_browser_login,
	wait_for_waf_ready,
)
from pixian_overlay.utils.config import AccountConfig, AppConfig, load_accounts_config
from pixian_overlay.utils.debug import debug_print, is_debug_enabled
from pixian_overlay.utils.notify import notify

load_dotenv()

BALANCE_HASH_FILE = 'balance_hash.txt'
DAILY_CHECK_IN_STATE_FILE = 'daily_checkin_state.json'
WAF_COOKIE_FETCH_ATTEMPTS = 2


class StateFileError(RuntimeError):
	"""持久化状态不可安全读取时终止签到，避免把损坏文件当作未签到。"""


def _reject_non_finite_json(value: str):
	raise ValueError(f'non-finite JSON value: {value}')


def _atomic_write_json(path: str, data: object) -> None:
	"""在目标文件同目录完成 flush、fsync 与原子替换。"""
	target = os.path.abspath(path)
	directory = os.path.dirname(target) or os.curdir
	os.makedirs(directory, exist_ok=True)
	fd, temporary_path = tempfile.mkstemp(prefix=f'.{os.path.basename(target)}.', suffix='.tmp', dir=directory)
	try:
		if os.path.exists(target):
			os.fchmod(fd, os.stat(target).st_mode & 0o777)
		with os.fdopen(fd, 'w', encoding='utf-8') as file_obj:
			fd = -1
			json.dump(data, file_obj, ensure_ascii=False, indent=2, allow_nan=False)
			file_obj.flush()
			os.fsync(file_obj.fileno())
		os.replace(temporary_path, target)
	except Exception:
		if fd >= 0:
			os.close(fd)
		try:
			os.unlink(temporary_path)
		except FileNotFoundError:
			pass
		raise


@dataclass(frozen=True)
class CheckInOutcome:
	"""签到接口的明确结果，避免把模糊响应写入成功状态。"""

	status: Literal['success', 'already_checked', 'failed']
	message: str = ''

	@property
	def handled(self) -> bool:
		return self.status != 'failed'


_ALREADY_CHECKED_KEYWORDS = ('已经签到', '已签到', '重复签到', 'already checked', 'already signed')
_SUCCESS_MESSAGE_KEYWORDS = (
	'签到成功',
	'签到完成',
	'check-in successful',
	'successfully checked in',
	'signed in successfully',
)


def load_daily_check_in_state():
	"""加载每日签到状态"""
	if not os.path.exists(DAILY_CHECK_IN_STATE_FILE):
		return {}
	try:
		with open(DAILY_CHECK_IN_STATE_FILE, 'r', encoding='utf-8') as f:
			data = json.load(f, parse_constant=_reject_non_finite_json)
		if not isinstance(data, dict):
			raise ValueError('root value is not an object')
		for field in ('accounts_checked', 'providers_checked', 'details'):
			if field in data and not isinstance(data[field], dict):
				raise ValueError(f'{field} is not an object')
		date = data.get('date')
		if date is not None and (not isinstance(date, str) or re.fullmatch(r'\d{4}-\d{2}-\d{2}', date) is None):
			raise ValueError('date is not YYYY-MM-DD')
		for field in ('accounts_checked', 'providers_checked'):
			for key, value in data.get(field, {}).items():
				if not isinstance(key, str) or not isinstance(value, bool):
					raise ValueError(f'{field} must map string keys to booleans')
		for key, value in data.get('details', {}).items():
			if not isinstance(key, str) or not isinstance(value, dict):
				raise ValueError('details must map string keys to objects')
		return data
	except (OSError, ValueError, TypeError, json.JSONDecodeError) as e:
		raise StateFileError(f'Cannot safely load daily check-in state: {e}') from e


def save_daily_check_in_state(state):
	"""保存每日签到状态"""
	_atomic_write_json(DAILY_CHECK_IN_STATE_FILE, state)


def state_has_checked_in_today(state: dict, *, provider: str | None = None, account_key: str | None = None):
	"""在已加载的状态上检查账号，避免主流程反复读取同一文件。"""
	today = datetime.now().strftime('%Y-%m-%d')
	if state.get('date') != today:
		return False
	if account_key:
		accounts_checked = state.get('accounts_checked', {})
		return isinstance(accounts_checked, dict) and accounts_checked.get(account_key) is True
	if provider:
		providers_checked = state.get('providers_checked', {})
		return isinstance(providers_checked, dict) and providers_checked.get(provider) is True
	return state.get('checked_in') is True or state.get('balance_increased') is True


def has_checked_in_today(provider: str | None = None, account_key: str | None = None):
	"""判断今天是否已经成功处理过签到

	Args:
		provider: 可选，如果指定则检查特定 provider 的签到状态
		account_key: 可选，如果指定则检查特定账号的签到状态
	"""
	return state_has_checked_in_today(load_daily_check_in_state(), provider=provider, account_key=account_key)


def mark_checked_in_today(details, run_time: str, provider: str | None = None, account_keys: list | None = None):
	"""记录今天已经成功处理的账号，并在跨日时清空旧标记。"""
	account_keys_by_provider = {provider: list(account_keys or [])} if provider else {}
	mark_accounts_checked_in_today(details, run_time, account_keys_by_provider)


def mark_accounts_checked_in_today(
	details: dict, run_time: str, account_keys_by_provider: dict[str, list[str]]
) -> None:
	"""一次原子写入本轮全部成功账号，避免 provider 间留下半份状态。"""
	state = load_daily_check_in_state()
	today = datetime.now().strftime('%Y-%m-%d')
	if state.get('date') != today:
		state = {}
	state['date'] = today
	state['run_time'] = run_time
	accounts_checked = state.get('accounts_checked', {})
	providers_checked = state.get('providers_checked', {})
	all_account_keys: list[str] = []
	for provider, account_keys in account_keys_by_provider.items():
		if not account_keys:
			continue
		providers_checked[provider] = True
		for account_key in account_keys:
			accounts_checked[account_key] = True
			all_account_keys.append(account_key)
	state['accounts_checked'] = accounts_checked
	state['providers_checked'] = providers_checked
	state['checked_in'] = True
	stored_details = state.get('details', {})
	if not isinstance(stored_details, dict):
		stored_details = {}
	for account_key in all_account_keys:
		if account_key in details:
			stored_details[account_key] = details[account_key]
	state['details'] = stored_details
	save_daily_check_in_state(state)


def load_balance_snapshot() -> dict[str, dict[str, float]]:
	"""加载按稳定账号键保存的余额快照，兼容旧版哈希文件。"""
	if not os.path.exists(BALANCE_HASH_FILE):
		return {}
	try:
		with open(BALANCE_HASH_FILE, 'r', encoding='utf-8') as f:
			raw = f.read()
		try:
			data = json.loads(raw, parse_constant=_reject_non_finite_json)
		except json.JSONDecodeError:
			if re.fullmatch(r'[0-9a-fA-F]{16}', raw.strip()):
				print('[WARN] Legacy balance hash cannot restore per-account balances; waiting for a complete snapshot')
				return {}
			raise
		if not isinstance(data, dict):
			raise ValueError('root value is not an object')
		result: dict[str, dict[str, float]] = {}
		for key, value in data.items():
			if not isinstance(value, dict):
				raise ValueError(f'account {key!s} is not an object')
			quota = value.get('quota')
			if not _is_finite_amount(quota):
				raise ValueError(f'account {key!s} has invalid quota')
			quota = cast(int | float, quota)
			entry = {'quota': float(quota)}
			if 'used' in value:
				used = value.get('used')
				if not _is_finite_amount(used):
					raise ValueError(f'account {key!s} has invalid used amount')
				used = cast(int | float, used)
				entry['used'] = float(used)
			evidence_fields = ('evidence_quota', 'evidence_used')
			if any(field in value for field in evidence_fields):
				if not all(field in value and _is_finite_amount(value.get(field)) for field in evidence_fields):
					raise ValueError(f'account {key!s} has incomplete reward evidence')
				for field in evidence_fields:
					amount = cast(int | float, value[field])
					entry[field] = float(amount)
			result[str(key)] = entry
		return result
	except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as e:
		raise StateFileError(f'Cannot safely load balance snapshot: {e}') from e


def save_balance_snapshot(balances: dict[str, dict[str, float]]) -> None:
	"""保存 quota 与 used；通知哈希仍只比较 quota。"""
	snapshot: dict[str, dict[str, float]] = {}
	for key, value in sorted(balances.items()):
		quota = value.get('quota')
		if not _is_finite_amount(quota):
			raise ValueError(f'Invalid quota for {key}')
		quota = cast(int | float, quota)
		entry = {'quota': float(quota)}
		if 'used' in value:
			used = value.get('used')
			if not _is_finite_amount(used):
				raise ValueError(f'Invalid used amount for {key}')
			used = cast(int | float, used)
			entry['used'] = float(used)
		evidence_fields = ('evidence_quota', 'evidence_used')
		if any(field in value for field in evidence_fields):
			if not all(field in value and _is_finite_amount(value.get(field)) for field in evidence_fields):
				raise ValueError(f'Invalid reward evidence for {key}')
			for field in evidence_fields:
				amount = cast(int | float, value[field])
				entry[field] = float(amount)
		snapshot[key] = entry
	_atomic_write_json(BALANCE_HASH_FILE, snapshot)


def _is_finite_amount(value: object) -> bool:
	return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _reward_evidence_from_snapshot(snapshot: dict | None) -> dict | None:
	"""优先返回未被失败运行覆盖的自动签到证据基线。"""
	if not isinstance(snapshot, dict):
		return None
	quota = snapshot.get('evidence_quota', snapshot.get('quota'))
	used = snapshot.get('evidence_used', snapshot.get('used'))
	if not _is_finite_amount(quota) or not _is_finite_amount(used):
		return None
	return {'success': True, 'quota': quota, 'used_quota': used}


def _add_automatic_reward_evidence(entry: dict[str, float], previous: dict | None, *, confirmed: bool) -> None:
	"""成功时前移证据；未确认时保留旧证据，首次运行则建立待跨日比较的基线。"""
	source = (
		{'success': True, 'quota': entry.get('quota'), 'used_quota': entry.get('used')}
		if confirmed
		else _reward_evidence_from_snapshot(previous)
	)
	if source is None:
		source = {'success': True, 'quota': entry.get('quota'), 'used_quota': entry.get('used')}
	if _is_finite_amount(source.get('quota')) and _is_finite_amount(source.get('used_quota')):
		entry['evidence_quota'] = float(cast(int | float, source['quota']))
		entry['evidence_used'] = float(cast(int | float, source['used_quota']))


def generate_balance_hash(balances):
	"""生成余额数据的hash"""
	simple_balances = (
		{
			key: {'quota': float(value['quota']) if isinstance(value.get('quota'), (int, float)) else None}
			for key, value in balances.items()
		}
		if balances
		else {}
	)
	balance_json = json.dumps(simple_balances, sort_keys=True, separators=(',', ':'))
	return hashlib.sha256(balance_json.encode('utf-8')).hexdigest()[:16]


def should_send_notification(*, balance_changed: bool, has_failures: bool) -> bool:
	"""仅在余额变化或签到失败时发送通知。"""
	return balance_changed or has_failures


def parse_cookies(cookies_data):
	"""解析 cookies 数据"""
	if isinstance(cookies_data, dict):
		return {
			str(key).strip(): str(value)
			for key, value in cookies_data.items()
			if isinstance(key, str) and key.strip() and isinstance(value, str) and value
		}

	if isinstance(cookies_data, str):
		cookies_dict = {}
		for cookie in cookies_data.split(';'):
			if '=' in cookie:
				key, value = cookie.strip().split('=', 1)
				if key and value:
					cookies_dict[key] = value
		return cookies_dict
	return {}


def get_account_state_key(account: AccountConfig) -> str:
	"""返回与账号顺序无关的稳定状态键。"""
	identity = account.email or account.api_user or account.name
	if not identity:
		cookies = parse_cookies(account.cookies)
		if cookies:
			identity = hashlib.sha256(
				json.dumps(cookies, sort_keys=True, separators=(',', ':')).encode('utf-8')
			).hexdigest()[:16]
		else:
			identity = 'unnamed'
	return f'{account.provider}:{identity}'


def legacy_account_state_matches(state: dict, legacy_account_key: str, account_name: str, provider: str) -> bool:
	"""仅在旧状态详情仍明确对应当前账号时兼容 account_N 键。"""
	today = datetime.now().strftime('%Y-%m-%d')
	if state.get('date') != today:
		return False
	accounts_checked = state.get('accounts_checked', {})
	if not isinstance(accounts_checked, dict) or accounts_checked.get(legacy_account_key) is not True:
		return False
	details = state.get('details', {})
	detail = details.get(legacy_account_key) if isinstance(details, dict) else None
	return bool(
		isinstance(detail, dict)
		and detail.get('name') == account_name
		and (detail.get('provider') or 'anyrouter') == provider
	)


def get_skipped_account_detail(
	state: dict, account_key: str, legacy_account_key: str, account_name: str, provider: str
) -> dict:
	"""从当天状态构建跳过账号详情，绝不把旧奖励再次当作本次余额变化。"""
	details = state.get('details', {})
	cached = details.get(account_key) or details.get(legacy_account_key) if isinstance(details, dict) else None
	detail = dict(cached) if isinstance(cached, dict) else {}
	detail.update(
		{
			'name': account_name,
			'provider': provider,
			'success': True,
			'skipped': True,
			'balance_change': 0,
			'check_in_reward': 0,
			'usage_increase': 0,
		}
	)
	detail.pop('error', None)
	return detail


async def get_waf_cookies_with_browser(
	account_name: str,
	login_url: str,
	required_cookies: list[str],
):
	"""使用浏览器获取 WAF cookies（带 httpx fallback）"""
	print(f'[PROCESSING] {account_name}: Starting browser to get WAF cookies...')

	browser = None

	try:
		from cloakbrowser import launch_async

		browser = await launch_async(headless=True)
		page = await browser.new_page()
		await prepare_browser_page(page)
		print(f'[PROCESSING] {account_name}: Access login page to get initial cookies...')

		await page.goto(login_url, wait_until='domcontentloaded', timeout=60000)
		await wait_for_waf_ready(page)

		cookies = await page.context.cookies()

		waf_cookies = {}
		for cookie in cookies:
			cookie_name = cookie.get('name')
			cookie_value = cookie.get('value')
			if cookie_name in required_cookies and cookie_value is not None:
				waf_cookies[cookie_name] = cookie_value

		print(f'[INFO] {account_name}: Got {len(waf_cookies)} WAF cookies from browser')

		if not waf_cookies:
			print(f'[WARN] {account_name}: No WAF cookies from browser, trying httpx fallback...')
			return await _get_waf_cookies_via_httpx(login_url, required_cookies)

		missing_cookies = [c for c in required_cookies if c not in waf_cookies]
		if missing_cookies:
			print(f'[WARN] {account_name}: Missing {missing_cookies} from browser, trying httpx fallback...')
			httpx_cookies = await _get_waf_cookies_via_httpx(login_url, required_cookies)
			if httpx_cookies:
				waf_cookies = {**waf_cookies, **httpx_cookies}
		missing_cookies = [c for c in required_cookies if not waf_cookies.get(c)]
		if missing_cookies:
			print(f'[FAILED] {account_name}: WAF cookies still incomplete: {missing_cookies}')
			return None

		print(f'[SUCCESS] {account_name}: Got WAF cookies: {list(waf_cookies.keys())}')
		return waf_cookies

	except Exception as e:
		print(f'[WARN] {account_name}: Browser WAF fetch failed ({type(e).__name__}), trying httpx fallback...')
		try:
			result = await _get_waf_cookies_via_httpx(login_url, required_cookies)
			if result:
				return result
		except Exception as e2:
			print(f'[WARN] {account_name}: httpx fallback also failed: {e2}')
		print(f'[FAILED] {account_name}: Error occurred while getting WAF cookies: {e}')
		return None
	finally:
		if browser:
			try:
				await browser.close()
			except Exception:  # nosec B110
				pass


async def _get_waf_cookies_via_httpx(login_url: str, required_cookies: list[str]) -> dict | None:
	"""通过 httpx 访问登录页获取基础 WAF cookies（acw_tc, cdn_sec_tc 等）"""
	import httpx as _httpx

	try:
		async with _httpx.AsyncClient(http2=True, timeout=20.0, follow_redirects=True) as client:
			resp = await client.get(
				login_url,
				headers={
					'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36',
					'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
				},
			)
			waf_cookies = {}
			for name, cookie_jar in resp.cookies.items():
				if name in required_cookies:
					waf_cookies[name] = cookie_jar
			if all(waf_cookies.get(name) for name in required_cookies):
				print(f'[INFO] httpx WAF fetch: got {len(waf_cookies)} cookies: {list(waf_cookies.keys())}')
				return waf_cookies
			if waf_cookies:
				print(
					f'[WARN] httpx WAF fetch incomplete: missing {[name for name in required_cookies if not waf_cookies.get(name)]}'
				)
			return None
	except Exception as e:
		print(f'[WARN] httpx WAF fetch failed: {e}')
		return None


async def login_with_credentials(
	account_name: str,
	provider_config,
	provider_name: str,
	email: str,
	password: str,
) -> BrowserLoginResult | None:
	"""使用邮箱密码通过浏览器登录，返回 cookies 与拦截到的 api user id。"""
	print(f'[PROCESSING] {account_name}: Logging in with email/password...')

	login_url = f'{provider_config.domain}{provider_config.login_path}'
	settings = load_browser_login_settings(
		account_name,
		provider_name,
		persist_profile=provider_config.persist_profile,
	)
	timeout_ms = settings.wait_timeout_ms

	debug_print(
		f'[INFO] {account_name}: Browser profile={settings.profile_dir}, '
		f'persist={settings.persist_profile}, headless={settings.headless}, '
		f'humanize={settings.humanize}, timeout={timeout_ms}ms'
	)

	try:
		context = await launch_login_context(settings)
	except Exception as e:
		print(f'[FAILED] {account_name}: Browser launch failed: {e}')
		return None

	page = None
	try:
		page = await context.new_page()
		await prepare_browser_page(page)
		await navigate_login_page(
			page,
			login_url,
			timeout_ms,
			provider=provider_name,
			account_name=account_name,
		)

		if not await is_logged_in(page, provider_config.console_path):
			if await has_session_cookie(page):
				print(f'[WARN] {account_name}: Stale session cookie on login page, forcing email login')
			await save_login_screenshot(page, provider_name, account_name, 'before-email-login')
			await login_with_email_form(
				page,
				email,
				password,
				timeout_ms,
				provider=provider_name,
				account_name=account_name,
			)
		else:
			print(f'[INFO] {account_name}: Browser profile already logged in')

		console_url = f'{provider_config.domain}{provider_config.console_path}'
		user_profile = await verify_browser_login(
			page, console_url, timeout_ms, user_info_path=provider_config.user_info_path
		)
		if not user_profile:
			cookies = await context.cookies()
			cookie_names = [c.get('name') for c in cookies if c.get('name')]
			print(f'[FAILED] {account_name}: Login failed - {provider_config.user_info_path} not verified')
			debug_print(f'[INFO] {account_name}: Current URL: {page.url}')
			debug_print(f'[INFO] {account_name}: Got cookies: {cookie_names}')
			await save_login_screenshot(page, provider_name, account_name, 'not-authenticated')
			await context.close()
			return None

		cookies = await context.cookies()
		all_cookies: dict[str, str] = {}
		for cookie in cookies:
			cookie_name = cookie.get('name')
			cookie_value = cookie.get('value')
			if isinstance(cookie_name, str) and isinstance(cookie_value, str) and cookie_name and cookie_value:
				all_cookies[cookie_name] = cookie_value
		api_user = str(user_profile['id']) if user_profile.get('id') is not None else None

		success_msg = f'[SUCCESS] {account_name}: Login successful, got {len(all_cookies)} cookies'
		if is_debug_enabled() and api_user:
			success_msg += f', api_user={api_user}'
		print(success_msg)
		await context.close()
		return BrowserLoginResult(cookies=all_cookies, api_user=api_user)

	except Exception as e:
		print(f'[FAILED] {account_name}: Error during login: {e}')
		if page is not None:
			await save_login_screenshot(page, provider_name, account_name, 'login-error')
		await context.close()
		return None


def get_user_info(client, headers, user_info_url: str):
	"""获取用户信息"""
	try:
		response = client.get(user_info_url, headers=headers, timeout=30)

		if response.status_code == 200:
			data = response.json()
			if isinstance(data, dict) and data.get('success'):
				user_data = data.get('data', {})
				if not isinstance(user_data, dict):
					return {'success': False, 'error': 'Failed to get user info: invalid data object'}
				raw_quota = user_data.get('quota')
				raw_used_quota = user_data.get('used_quota')
				if not _is_finite_amount(raw_quota) or not _is_finite_amount(raw_used_quota):
					return {'success': False, 'error': 'Failed to get user info: invalid quota values'}
				raw_quota = cast(int | float, raw_quota)
				raw_used_quota = cast(int | float, raw_used_quota)
				quota = round(float(raw_quota) / 500000, 2)
				used_quota = round(float(raw_used_quota) / 500000, 2)
				return {
					'success': True,
					'quota': quota,
					'used_quota': used_quota,
					'display': f':money: Current balance: ${quota}, Used: ${used_quota}',
				}
		return {'success': False, 'error': f'Failed to get user info: HTTP {response.status_code}'}
	except Exception as e:
		return {'success': False, 'error': f'Failed to get user info: {str(e)[:50]}...'}


async def prepare_cookies(account_name: str, provider_config, user_cookies: dict) -> dict | None:
	"""准备请求所需的 cookies（可能包含 WAF cookies）"""
	waf_cookies = {}

	if provider_config.needs_waf_cookies():
		login_url = f'{provider_config.domain}{provider_config.login_path}'
		required_cookies = provider_config.waf_cookie_names or []
		for attempt in range(1, WAF_COOKIE_FETCH_ATTEMPTS + 1):
			fetched_cookies = await get_waf_cookies_with_browser(account_name, login_url, required_cookies)
			if fetched_cookies and all(fetched_cookies.get(name) for name in required_cookies):
				waf_cookies = fetched_cookies
				break
			missing_cookies = [name for name in required_cookies if not (fetched_cookies or {}).get(name)]
			print(
				f'[WARN] {account_name}: WAF cookie acquisition attempt '
				f'{attempt}/{WAF_COOKIE_FETCH_ATTEMPTS} incomplete; missing {missing_cookies}'
			)
		if not waf_cookies:
			print(f'[FAILED] {account_name}: Required WAF cookies unavailable; API requests aborted')
			return None
	else:
		print(f'[INFO] {account_name}: Bypass WAF not required, using user cookies directly')

	# Session 等登录凭据来自用户配置；同名 WAF cookie 必须使用本次浏览器新值。
	return {**user_cookies, **waf_cookies}


def _check_in_response_text(payload: object) -> str:
	"""提取签到响应中的可判定文本，不记录完整响应避免泄露无关数据。"""
	if isinstance(payload, str):
		return payload
	if isinstance(payload, dict):
		parts = []
		for key in ('msg', 'message', 'detail', 'info', 'error', 'data'):
			value = payload.get(key)
			if isinstance(value, (str, dict, list)):
				parts.append(_check_in_response_text(value))
		return ' '.join(part for part in parts if part)
	if isinstance(payload, list):
		return ' '.join(_check_in_response_text(item) for item in payload)
	return ''


def parse_check_in_response(response) -> CheckInOutcome:
	"""严格解析签到接口响应。

	`code=0` 仅表示请求被接口接受，不能单独证明签到成功；必须有明确成功标记、
	明确的成功消息，或明确的“已签到”消息。
	"""
	if response.status_code != 200:
		return CheckInOutcome('failed', f'HTTP {response.status_code}')

	try:
		payload = response.json()
	except json.JSONDecodeError:
		body = response.text.strip().lower()
		if body in {'success', 'ok'} or any(keyword.lower() in body for keyword in _SUCCESS_MESSAGE_KEYWORDS):
			return CheckInOutcome('success', response.text.strip()[:120])
		return CheckInOutcome('failed', 'Invalid or ambiguous response format')

	if not isinstance(payload, dict):
		return CheckInOutcome('failed', 'Invalid response format')

	message = _check_in_response_text(payload).strip()
	message_lower = message.lower()
	if any(keyword.lower() in message_lower for keyword in _ALREADY_CHECKED_KEYWORDS):
		return CheckInOutcome('already_checked', message[:120])
	if payload.get('success') is True or payload.get('ret') == 1:
		return CheckInOutcome('success', message[:120])
	if payload.get('code') == 0 and any(keyword.lower() in message_lower for keyword in _SUCCESS_MESSAGE_KEYWORDS):
		return CheckInOutcome('success', message[:120])
	if message:
		return CheckInOutcome('failed', message[:120])
	return CheckInOutcome('failed', 'Ambiguous response: code=0 without an explicit success marker')


def _annotate_check_in_status(user_info: dict | None, status: str) -> dict | None:
	"""在用户信息结果中携带内部签到状态，避免扩展公开返回值结构。"""
	if not user_info:
		return user_info
	annotated = dict(user_info)
	annotated['_check_in_status'] = status
	return annotated


def execute_check_in(client, account_name: str, provider_config, headers: dict) -> CheckInOutcome:
	"""执行签到请求并返回明确的三态结果。"""
	print(f'[NETWORK] {account_name}: Executing check-in')

	checkin_headers = headers.copy()
	checkin_headers.update({'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest'})

	sign_in_url = f'{provider_config.domain}{provider_config.sign_in_path}'
	response = client.post(sign_in_url, headers=checkin_headers, timeout=30)

	print(f'[RESPONSE] {account_name}: Response status code {response.status_code}')
	outcome = parse_check_in_response(response)
	if outcome.status == 'success':
		print(f'[SUCCESS] {account_name}: Check-in response confirmed success')
	elif outcome.status == 'already_checked':
		print(f'[INFO] {account_name}: Already checked in today')
	else:
		print(f'[FAILED] {account_name}: Check-in failed - {outcome.message}')
	return outcome


def format_check_in_notification(detail: dict, check_in_time: str | None = None) -> str:
	"""格式化签到通知消息

	Args:
		detail: 包含签到详情的字典
		check_in_time: 签到时间（可选）

	Returns:
		格式化后的通知消息
	"""
	account_name = detail['name']
	time_str = f' @ {check_in_time}' if check_in_time else ''
	success = detail.get('success', False)
	skipped = detail.get('skipped', False)

	if not success:
		error_msg = detail.get('error', 'Unknown error')
		return (
			f'{account_name}\n'
			f'[FAIL]{time_str}\n'
			f'  ━━━━━━━━━━━━━━━━━━━━\n'
			f'  ❌ 签到失败\n'
			f'  📝 错误: {error_msg}\n'
			f'  ━━━━━━━━━━━━━━━━━━━━'
		)

	before_quota = detail.get('before_quota')
	before_used = detail.get('before_used')
	after_quota = detail.get('after_quota')
	after_used = detail.get('after_used')

	# Determine the tag: [CHECK-IN] for new check-in, [SKIP] for skipped (already checked)
	tag = '[SKIP]' if skipped else '[CHECK-IN]'

	if before_quota is not None and before_used is not None:
		lines = [
			f'{account_name}',
			f'{tag}{time_str}',
			'  ━━━━━━━━━━━━━━━━━━━━',
			f'  📍 签到前 💵 余额: ${before_quota:.2f}  |  📊 累计消耗: ${before_used:.2f}',
			f'  📍 签到后 💵 余额: ${after_quota:.2f}  |  📊 累计消耗: ${after_used:.2f}',
		]

		check_in_reward = detail.get('check_in_reward') or 0
		usage_increase = detail.get('usage_increase') or 0
		balance_change = detail.get('balance_change') or 0
		baseline_balance_change = detail.get('baseline_balance_change') or 0

		has_reward = check_in_reward > 0
		has_usage = usage_increase > 0
		usage_counter_reset = usage_increase < 0
		has_baseline_change = baseline_balance_change != 0

		if has_reward or has_usage or usage_counter_reset or has_baseline_change:
			lines.append('  ━━━━━━━━━━━━━━━━━━━━')

			if not has_reward and has_usage:
				lines.append('  ℹ️ 今日已签到（期间有使用）')

			if has_reward:
				lines.append(f'  🎁 签到获得: +${check_in_reward:.2f}')

			if has_usage:
				lines.append(f'  📉 期间消耗: ${usage_increase:.2f}')
			elif usage_counter_reset:
				lines.append('  ℹ️ 累计消耗计数器已重置')

			if balance_change != 0:
				change_symbol = '+' if balance_change > 0 else ''
				lines.append(f'  💹 余额变化: {change_symbol}${balance_change:.2f}')
			elif has_baseline_change:
				change_symbol = '+' if baseline_balance_change > 0 else ''
				lines.append(f'  📈 相比上次记录余额变化: {change_symbol}${baseline_balance_change:.2f}')
		else:
			lines.extend(['  ━━━━━━━━━━━━━━━━━━━━', '  ℹ️ 今日已签到，无变化'])

		return '\n'.join(lines)
	else:
		# Partial data - show what we have
		lines = [
			f'{account_name}',
			f'{tag}{time_str}',
			'  ━━━━━━━━━━━━━━━━━━━━',
		]
		if after_quota is not None:
			lines.append(f'  📍 当前 💵 余额: ${after_quota:.2f}')
		lines.extend(['  ━━━━━━━━━━━━━━━━━━━━', '  ℹ️ 今日已签到'])
		return '\n'.join(lines)


async def check_in_account(
	account: AccountConfig, account_index: int, app_config: AppConfig, *, skip_check_in: bool = False
):
	"""为单个账号执行签到操作"""
	account_name = account.get_display_name(account_index)
	print(f'\n[PROCESSING] Starting to process {account_name}')

	provider_config = app_config.get_provider(account.provider)
	if not provider_config:
		print(f'[FAILED] {account_name}: Provider "{account.provider}" not found in configuration')
		return False, None, None

	print(f'[INFO] {account_name}: Using provider "{account.provider}" ({provider_config.domain})')

	# 邮箱密码优先
	all_cookies = None
	resolved_api_user: str | None = None
	auth_method = None
	if account.has_login_credentials():
		print(f'[INFO] {account_name}: Attempting email/password login (priority)...')
		if account.email is None or account.password is None:
			print(f'[FAILED] {account_name}: Incomplete email/password configuration')
			return False, None, None
		login_result = await login_with_credentials(
			account_name,
			provider_config,
			account.provider,
			account.email,
			account.password,
		)
		if login_result:
			all_cookies = login_result.cookies
			resolved_api_user = login_result.api_user
			auth_method = 'email/password'
		else:
			print(f'[FAILED] {account_name}: Email/password login failed, will not use stale session cookies')
			return False, None, None
	else:
		user_cookies = parse_cookies(account.cookies)
		if not user_cookies:
			print(f'[FAILED] {account_name}: Invalid configuration format')
			return False, None, None
		all_cookies = await prepare_cookies(account_name, provider_config, user_cookies)
		auth_method = 'session cookies'

	if not all_cookies:
		return False, None, None

	print(f'[AUTH] {account_name}: Using auth method -> {auth_method}')

	return run_check_in_requests(
		all_cookies,
		account,
		account_name,
		provider_config,
		api_user_override=resolved_api_user,
		skip_check_in=skip_check_in,
	)


def run_check_in_requests(
	all_cookies: dict,
	account: AccountConfig,
	account_name: str,
	provider_config,
	*,
	api_user_override: str | None = None,
	skip_check_in: bool = False,
) -> tuple[bool, dict | None, dict | None]:
	"""执行 HTTP 签到请求（同步，避免在 async 上下文中使用阻塞 httpx）。"""
	try:
		with httpx.Client(http2=True, timeout=30.0) as client:
			client.cookies.update(all_cookies)

			headers = {
				'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36',
				'Accept': 'application/json, text/plain, */*',
				'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
				'Accept-Encoding': 'gzip, deflate, br, zstd',
				'Referer': provider_config.domain,
				'Origin': provider_config.domain,
				'Connection': 'keep-alive',
				'Sec-Fetch-Dest': 'empty',
				'Sec-Fetch-Mode': 'cors',
				'Sec-Fetch-Site': 'same-origin',
			}

			api_user = api_user_override or account.api_user
			if api_user:
				headers[provider_config.api_user_key] = api_user

			user_info_url = f'{provider_config.domain}{provider_config.user_info_path}'
			user_info_before = get_user_info(client, headers, user_info_url)
			if user_info_before and user_info_before.get('success'):
				print(user_info_before['display'])
			elif user_info_before:
				print(user_info_before.get('error', 'Unknown error'))

			if provider_config.needs_manual_check_in() and not skip_check_in:
				check_in_outcome = execute_check_in(client, account_name, provider_config, headers)
				user_info_after = get_user_info(client, headers, user_info_url)
				if check_in_outcome.handled and not (user_info_after and user_info_after.get('success')):
					print(f'[WARN] {account_name}: Check-in confirmed but post-check-in balance query failed')
					balance_fallback = (
						user_info_before if user_info_before and user_info_before.get('success') else user_info_after
					)
					return True, user_info_before, _annotate_check_in_status(balance_fallback, check_in_outcome.status)
				return (
					check_in_outcome.handled,
					user_info_before,
					_annotate_check_in_status(user_info_after, check_in_outcome.status),
				)
			if skip_check_in:
				return (
					bool(user_info_before and user_info_before.get('success')),
					user_info_before,
					_annotate_check_in_status(user_info_before, 'already_checked'),
				)

			user_info_after = get_user_info(client, headers, user_info_url)
			if user_info_after and user_info_after.get('success'):
				print(f'[INFO] {account_name}: Check-in completed automatically (triggered by user info request)')
				return True, user_info_before, _annotate_check_in_status(user_info_after, 'success')
			error = user_info_after.get('error', 'Unknown error') if user_info_after else 'Unknown error'
			print(f'[FAILED] {account_name}: Auto check-in failed - {error}')
			return False, user_info_before, user_info_after

	except Exception as e:
		print(f'[FAILED] {account_name}: Error occurred during check-in process - {str(e)[:50]}...')
		return False, None, None


async def main():
	"""主函数"""
	current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

	if is_debug_enabled():
		print('[INFO] DEBUG_MODE enabled')
	else:
		print('[INFO] Debug mode disabled (set DEBUG_MODE=true to enable screenshots and verbose logs)')

	print('[SYSTEM] Multi-account auto check-in script started')
	print(f'[TIME] Execution time: {current_time}')

	app_config = AppConfig.load_from_env()
	print(f'[INFO] Loaded {len(app_config.providers)} provider configuration(s)')
	accounts = load_accounts_config()
	if not accounts:
		error_msg = '[FAILED] Unable to load account configuration, program exits'
		print(error_msg)
		notify.push_message('Check-in Alert', error_msg, msg_type='text')
		sys.exit(1)

	print(f'[INFO] Found {len(accounts)} account configurations')

	account_keys = [get_account_state_key(account) for account in accounts]
	unknown_providers = sorted(
		{account.provider for account in accounts if app_config.get_provider(account.provider) is None}
	)
	if unknown_providers:
		error_msg = '[FAILED] Unknown account provider(s); no account requests were sent: ' + ', '.join(
			unknown_providers
		)
		print(error_msg)
		notify.push_message('Check-in Alert', error_msg, msg_type='text')
		sys.exit(1)
	duplicate_keys = sorted({key for key in account_keys if account_keys.count(key) > 1})
	if duplicate_keys:
		error_msg = '[FAILED] Duplicate account identities would overwrite check-in state: ' + ', '.join(duplicate_keys)
		print(error_msg)
		notify.push_message('Check-in Alert', error_msg, msg_type='text')
		sys.exit(1)

	try:
		last_balance_snapshot = load_balance_snapshot()
		daily_check_in_state = load_daily_check_in_state()
	except StateFileError as e:
		error_msg = f'[FAILED] State file safety check failed; no account requests were sent: {e}'
		print(error_msg)
		notify.push_message('Check-in Alert', error_msg, msg_type='text')
		sys.exit(1)

	success_count = 0
	total_count = len(accounts)
	notification_content = []
	current_balances = {}
	account_check_in_details = {}  # 存储每个账号的签到详情
	has_failures = False
	balance_changed = False  # 余额是否有变化

	for i, account in enumerate(accounts):
		account_key = account_keys[i]
		legacy_account_key = f'account_{i + 1}'
		account_name = account.get_display_name(i)
		legacy_state_matches = legacy_account_state_matches(
			daily_check_in_state, legacy_account_key, account_name, account.provider
		)
		skip_check_in = (
			state_has_checked_in_today(daily_check_in_state, account_key=account_key) or legacy_state_matches
		)

		# Cookie 账号做只读余额复核，捕获浏览器或其他客户端产生的奖励；邮箱账号避免重复浏览器登录。
		if skip_check_in:
			detail = get_skipped_account_detail(
				daily_check_in_state,
				account_key,
				legacy_account_key if legacy_state_matches else '',
				account_name,
				account.provider,
			)
			refresh_success = False
			refresh_after = None
			if account.has_login_credentials():
				print(f'[INFO] {account_name} already checked in today; browser login skipped, retaining saved balance')
			else:
				print(f'[INFO] {account_name} already checked in today, refreshing balance without check-in request')
				try:
					refresh_success, _, refresh_after = await check_in_account(
						account, i, app_config, skip_check_in=True
					)
				except Exception as e:
					print(f'[WARN] {account_name}: Read-only balance refresh failed ({str(e)[:80]})')

			if refresh_success and refresh_after and refresh_after.get('success'):
				refreshed_quota = refresh_after['quota']
				refreshed_used = refresh_after['used_quota']
				detail.update(
					{
						'before_quota': refreshed_quota,
						'before_used': refreshed_used,
						'after_quota': refreshed_quota,
						'after_used': refreshed_used,
						'check_in_reward': 0,
						'usage_increase': 0,
						'balance_change': 0,
						'check_in_status': 'already_checked',
					}
				)
				print(f'[INFO] {account_name}: Read-only balance refresh succeeded')
			elif not account.has_login_credentials():
				print(f'[WARN] {account_name}: Read-only balance refresh unavailable; retaining saved balance')

			fallback_balance = last_balance_snapshot.get(account_key)
			if fallback_balance is None and legacy_state_matches:
				fallback_balance = last_balance_snapshot.get(legacy_account_key)
			if not isinstance(detail.get('after_quota'), (int, float)) and isinstance(fallback_balance, dict):
				fallback_quota = fallback_balance.get('quota')
				if isinstance(fallback_quota, (int, float)):
					detail['before_quota'] = fallback_quota
					detail['after_quota'] = fallback_quota
			account_check_in_details[account_key] = detail
			if isinstance(detail.get('after_quota'), (int, float)):
				current_balance = {'quota': detail['after_quota']}
				if _is_finite_amount(detail.get('after_used')):
					current_balance['used'] = detail['after_used']
				provider_config = app_config.get_provider(account.provider)
				if provider_config is not None and not provider_config.needs_manual_check_in():
					_add_automatic_reward_evidence(
						current_balance, last_balance_snapshot.get(account_key), confirmed=True
					)
				current_balances[account_key] = current_balance
			success_count += 1
			continue

		try:
			success, user_info_before, user_info_after = await check_in_account(
				account, i, app_config, skip_check_in=skip_check_in
			)
			check_in_status = (
				user_info_after.get('_check_in_status', 'success' if success else 'failed')
				if user_info_after
				else ('success' if success else 'failed')
			)
			verification_error = ''
			provider_config = app_config.get_provider(account.provider)
			if (
				success
				and check_in_status == 'success'
				and provider_config is not None
				and not provider_config.needs_manual_check_in()
			):
				previous_balance = last_balance_snapshot.get(account_key)
				baseline_info = _reward_evidence_from_snapshot(previous_balance)
				reward = _reward_amount(baseline_info, user_info_after)
				if reward is None or reward <= 0:
					success = False
					check_in_status = 'failed'
					verification_error = (
						'自动签到资料请求成功，但与上次完整余额/消耗快照相比没有正向奖励证据；本次不记录为已签到'
					)
					print(f'[FAILED] {account_name}: {verification_error}')
				elif baseline_info is not None:
					user_info_before = baseline_info
					print(f'[SUCCESS] {account_name}: Automatic reward confirmed: +${reward:.2f}')
			if success:
				success_count += 1

			should_notify_this_account = False

			if not success:
				should_notify_this_account = True
				has_failures = True
				account_name = account.get_display_name(i)
				print(f'[NOTIFY] {account_name} failed, will send notification')

			# Always add account to details (even failed ones) for notification grouping
			if user_info_after and user_info_after.get('success'):
				after_quota = user_info_after['quota']
				after_used = user_info_after['used_quota']
				current_balance = {'quota': after_quota, 'used': after_used}
				if provider_config is not None and not provider_config.needs_manual_check_in():
					_add_automatic_reward_evidence(
						current_balance, last_balance_snapshot.get(account_key), confirmed=success
					)
				current_balances[account_key] = current_balance
				if user_info_before and user_info_before.get('success'):
					before_quota = user_info_before['quota']
					before_used = user_info_before['used_quota']
					balance_change = after_quota - before_quota
					check_in_reward = _reward_amount(user_info_before, user_info_after)
					account_check_in_details[account_key] = {
						'name': account_name,
						'provider': account.provider,
						'before_quota': before_quota,
						'before_used': before_used,
						'after_quota': after_quota,
						'after_used': after_used,
						'check_in_reward': check_in_reward,
						'usage_increase': after_used - before_used,
						'balance_change': balance_change,
						'success': success,
						'skipped': skip_check_in or check_in_status == 'already_checked',
						'check_in_status': check_in_status,
						'error': verification_error,
					}
				else:
					account_check_in_details[account_key] = {
						'name': account_name,
						'provider': account.provider,
						'before_quota': None,
						'before_used': None,
						'after_quota': after_quota,
						'after_used': after_used,
						'check_in_reward': None,
						'usage_increase': None,
						'balance_change': None,
						'success': success,
						'skipped': skip_check_in or check_in_status == 'already_checked',
						'check_in_status': check_in_status,
						'error': verification_error or (user_info_before.get('error') if user_info_before else ''),
					}
			else:
				error_msg = user_info_after.get('error', 'Login failed') if user_info_after else 'Login failed'
				account_check_in_details[account_key] = {
					'name': account_name,
					'provider': account.provider,
					'before_quota': None,
					'before_used': None,
					'after_quota': None,
					'after_used': None,
					'check_in_reward': None,
					'usage_increase': None,
					'balance_change': None,
					'success': success,
					'error': error_msg,
				}

			if should_notify_this_account:
				status = '[SUCCESS]' if success else '[FAIL]'
				account_result = f'{status} {account_name}'
				if user_info_after and user_info_after.get('success'):
					account_result += f'\n{user_info_after["display"]}'
				elif user_info_after:
					account_result += f'\n{user_info_after.get("error", "Unknown error")}'
				notification_content.append(account_result)

		except Exception as e:
			account_name = account.get_display_name(i)
			print(f'[FAILED] {account_name} processing exception: {e}')
			has_failures = True
			account_check_in_details[account_key] = {
				'name': account_name,
				'provider': account.provider,
				'before_quota': None,
				'before_used': None,
				'after_quota': None,
				'after_used': None,
				'check_in_reward': None,
				'usage_increase': None,
				'balance_change': None,
				'success': False,
				'error': f'Processing exception: {str(e)[:120]}',
			}
			notification_content.append(f'[FAIL] {account_name} exception: {str(e)[:50]}...')

	balances_complete = len(current_balances) == total_count
	if last_balance_snapshot:
		for account_key, current_balance in current_balances.items():
			previous_balance = last_balance_snapshot.get(account_key)
			snapshot_detail = account_check_in_details.get(account_key)
			if not isinstance(snapshot_detail, dict) or not isinstance(previous_balance, dict):
				continue
			current_quota = current_balance.get('quota')
			previous_quota = previous_balance.get('quota')
			if isinstance(current_quota, (int, float)) and isinstance(previous_quota, (int, float)):
				snapshot_detail['baseline_balance_change'] = current_quota - previous_quota
	# A new deployment has no prior snapshot.  Do not treat creating that baseline
	# as a balance change, but preserve notifications for an actual change observed
	# between the before/after requests in this run.
	balance_changed = any(detail.get('balance_change') not in (None, 0) for detail in account_check_in_details.values())
	if balances_complete:
		if not last_balance_snapshot:
			print('[INFO] First complete balance snapshot stored')
		elif generate_balance_hash(current_balances) != generate_balance_hash(last_balance_snapshot):
			balance_changed = True
			print('[NOTIFY] Balance changes detected, will send notification')
		else:
			print('[INFO] No balance changes detected')
	else:
		print(f'[WARN] Balance snapshot incomplete ({len(current_balances)}/{total_count}); baseline unchanged')

	if balance_changed:
		for i, account in enumerate(accounts):
			account_key = get_account_state_key(account)
			if account_key in account_check_in_details:
				detail = account_check_in_details[account_key]
				account_name = detail['name']
				account_result = format_check_in_notification(detail)
				if not any(account_name in item for item in notification_content):
					notification_content.append(account_result)

	# 保存所有成功签到的账号状态（不仅仅是余额增长的）
	successful_account_keys: list[str] = []
	persistence_error = ''
	if account_check_in_details:
		for account_key, detail in account_check_in_details.items():
			if detail.get('success', False):
				successful_account_keys.append(account_key)
	try:
		if successful_account_keys:
			account_keys_by_provider: dict[str, list[str]] = {}
			for account_key in successful_account_keys:
				provider = account_check_in_details[account_key].get('provider') or 'anyrouter'
				account_keys_by_provider.setdefault(provider, []).append(account_key)
			mark_accounts_checked_in_today(account_check_in_details, current_time, account_keys_by_provider)
		# 成功状态优先持久化。若余额基线写入失败，已到账账号不会被下一轮重复签到。
		if balances_complete:
			save_balance_snapshot(current_balances)
	except (OSError, ValueError, TypeError, StateFileError) as e:
		persistence_error = f'状态保存失败: {str(e)[:160]}'
		has_failures = True
		print(f'[FAILED] {persistence_error}')

	if (
		should_send_notification(balance_changed=balance_changed, has_failures=has_failures)
		and account_check_in_details
	):
		# 按 provider 分组账号详情
		provider_groups: dict[str, list[dict]] = {}
		for account_key, detail in account_check_in_details.items():
			pname = detail.get('provider') or 'anyrouter'
			if pname not in provider_groups:
				provider_groups[pname] = []
			provider_groups[pname].append(detail)

		# 收集所有 provider 的内容合并到一条推送
		all_sections = []
		for provider_name, provider_details in provider_groups.items():
			provider_total = len(provider_details)
			# 计算真实成功数（不包含跳过的账号）
			provider_success = sum(
				1 for d in provider_details if d.get('success', False) and not d.get('skipped', False)
			)
			provider_skipped = sum(1 for d in provider_details if d.get('skipped', False))

			# 计算有效的成功数（成功或跳过的都算已处理）
			provider_handled = provider_success + provider_skipped

			if provider_success == provider_total:
				provider_title = f'✅ {provider_name}签到全部成功 ({provider_success}/{provider_total})'
			elif provider_skipped == provider_total:
				provider_title = f'ℹ️ {provider_name}今日已签到，跳过 ({provider_total}/{provider_total})'
			elif provider_handled == provider_total:
				# 部分成功 + 部分跳过
				provider_title = (
					f'⚠️ {provider_name}签到完成（部分跳过）({provider_success}+{provider_skipped}/{provider_total})'
				)
			elif provider_success > 0:
				provider_title = f'⚠️ {provider_name}签到部分成功 ({provider_success}/{provider_total})'
			else:
				provider_title = f'❌ {provider_name}签到失败 ({provider_success}/{provider_total})'

			notify_items = []
			for detail in provider_details:
				notify_items.append(format_check_in_notification(detail, current_time))

			section = f'{provider_title}\n\n' + '\n\n'.join(notify_items)
			all_sections.append(section)
		if persistence_error:
			all_sections.append(f'❌ 本地状态持久化失败\n\n{persistence_error}')

		# 总体标题
		if success_count == total_count and persistence_error:
			notify_title = f'⚠️ 签到完成但状态保存失败 ({success_count}/{total_count})'
		elif success_count == total_count:
			notify_title = f'✅ 签到全部成功 ({success_count}/{total_count})'
		elif success_count > 0:
			notify_title = f'⚠️ 签到部分成功 ({success_count}/{total_count})'
		else:
			notify_title = f'❌ 签到失败 ({success_count}/{total_count})'

		# 合并所有内容
		notify_content = '\n\n'.join(all_sections)

		# 在输出中显示标题和内容
		print(notify_title)
		print('')
		print(notify_content)
		if notify.push_message(notify_title, notify_content, msg_type='text'):
			print('[NOTIFY] Combined notification delivered through at least one channel')
		else:
			print('[WARN] Every configured notification delivery attempt failed')

	else:
		print('[INFO] Balances unchanged and no check-in failures, notification skipped')

	all_accounts_handled = success_count == total_count and not has_failures
	sys.exit(0 if all_accounts_handled else 1)


def run_main():
	"""运行主函数的包装函数"""
	try:
		asyncio.run(main())
	except KeyboardInterrupt:
		print('\n[WARNING] Program interrupted by user')
		sys.exit(1)
	except Exception as e:
		print(f'\n[FAILED] Error occurred during program execution: {e}')
		sys.exit(1)


if __name__ == '__main__':
	run_main()
