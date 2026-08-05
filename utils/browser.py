"""浏览器登录辅助函数"""

from __future__ import annotations

import asyncio
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from utils.debug import debug_print, is_debug_enabled
from utils.popups import dismiss_popups, setup_popup_guard
from utils.proxy import get_playwright_proxy

if TYPE_CHECKING:
	from playwright.async_api import BrowserContext, Locator, Page

EMAIL_LOGIN_BUTTON_NAMES = (
	re.compile(r'邮箱或用户名'),
	re.compile(r'邮箱.*?登录'),
	re.compile(r'用户名.*?登录'),
	re.compile(r'使用.*邮箱'),
	re.compile(r'Email or Username', re.I),
	re.compile(r'Sign in with Email', re.I),
	re.compile(r'Sign in with Email or Username', re.I),
	re.compile(r'With Email', re.I),
	re.compile(r'Login.*Email', re.I),
)
EMAIL_LOGIN_ENTRY_SELECTORS = (
	'.semi-card button:has(.semi-icon-mail):not(form.semi-form button)',
	'.semi-card button:has([aria-label="mail"]):not(form.semi-form button)',
	'.semi-card button.semi-button-primary:has(.semi-icon-mail)',
	'button:has(.semi-icon-mail):not(form.semi-form button)',
	# 通用新 UI：蓝色主按钮或含图标的邮箱登录按钮，不在 form 内
	'button[type="button"][class*="primary"]:not(form button)',
	'button[class*="Primary"]:not(form button)',
	'button[class*="Button"]:not(form button):has(svg)',
	'[role="button"]:not(form [role="button"])',
)
LOGIN_PAGE_READY_SELECTORS = (
	'.semi-card button:has(.semi-icon-mail)',
	'.semi-card',
	'button:has(.semi-icon-mail)',
	'button:has(svg)',
	'button',
)
LOGIN_FORM_SELECTOR = 'form.semi-form'
USERNAME_SELECTORS = (
	'#username',
	'input[name="username"]',
	'input[name="email"]',
	'input[type="email"]',
	'input[autocomplete="username"]',
	'input[autocomplete="email"]',
	'form input[type="text"]',
	'form input:not([type="password"]):not([type="hidden"]):not([type="submit"]):not([type="checkbox"]):not([type="radio"])',
)
PASSWORD_SELECTORS = (  # nosec B105
	'#password',
	'input[name="password"]',
	'input[type="password"]',
	'input[autocomplete="current-password"]',
	'form input[type="password"]',
)
SUBMIT_SELECTORS = (
	f'{LOGIN_FORM_SELECTOR} button[type="submit"]',
	'form button[type="submit"]',
	'button[type="submit"]',
	'form button:last-child',
	'form button[class*="primary" i]',
	'form button[class*="Primary"]',
	'form button:has-text("登录")',
	'form button:has-text("登 录")',
	'form button:has-text("Login")',
	'form button:has-text("继续")',
	'form button:has-text("Continue")',
	'button:has-text("继续")',
	'button:has-text("登录")',
	'button:has-text("Continue")',
	'button:has-text("Next")',
	'button:has-text("下一步")',
	'button[class*="primary" i]:not([disabled])',
	'button[class*="Primary"]:not([disabled])',
	'button:has(.semi-icon-arrow-right)',
)
SESSION_COOKIE_NAME = 'session'
USER_SELF_API_SUFFIX = '/api/user/self'
CONSOLE_PATH = '/console'
DEFAULT_SCREENSHOT_DIR = 'checkin_screenshots'
DEFAULT_TIMEOUT_MS = 60_000
_pending_notify_screenshots: list[Path] = []
FORM_ACTION_TIMEOUT_MS = 15_000
EMAIL_TAB_TIMEOUT_MS = 8_000
WAF_READY_TIMEOUT_MS = 30_000
SESSION_WAIT_TIMEOUT_MS = 45_000

_VISIBLE_CHECK_JS = """
	const isVisible = (el) => {
		if (!el || !el.isConnected) return false;
		const style = window.getComputedStyle(el);
		if (style.display === 'none' || style.visibility === 'hidden' || parseFloat(style.opacity) === 0) {
			return false;
		}
		const rect = el.getBoundingClientRect();
		return rect.width > 0 && rect.height > 0;
	};
	const countVisible = (selector) => [...document.querySelectorAll(selector)].filter(isVisible).length;
"""

_SITE_READY_JS = f"""() => {{
{_VISIBLE_CHECK_JS}
	const text = document.body?.innerText || '';
	const blocked = /请进行验证|为了更好的访问体验|访问受限|Access denied|verify you are human/i.test(text);
	if (blocked) return false;
	const wafBlockers = document.querySelector(
		'iframe[src*="captcha"], iframe[src*="verify"], iframe[src*="slide"], .nc-container, #nocaptcha'
	);
	if (wafBlockers) {{
		const rect = wafBlockers.getBoundingClientRect?.();
		if (rect && rect.width > 0 && rect.height > 0) return false;
	}}
	if (/\\/login/.test(location.pathname)) {{
		return countVisible('.semi-card') > 0
			|| countVisible('#username') > 0
			|| countVisible('input[type="password"]') > 0
			|| countVisible('button') >= 2;
	}}
	return countVisible('a') > 0 || countVisible('button') > 0;
}}"""

_LOGIN_SHELL_READY_JS = f"""() => {{
{_VISIBLE_CHECK_JS}
	const text = document.body?.innerText || '';
	const blocked = /请进行验证|为了更好的访问体验|访问受限|Access denied|verify you are human/i.test(text);
	if (blocked) return false;
	return countVisible('.semi-card') > 0
		|| countVisible('#username') > 0
		|| countVisible('input[type="password"]') > 0
		|| countVisible('button') >= 2;
}}"""

_OPEN_EMAIL_FORM_JS = """() => {
	const isVisible = (el) => {
		if (!el || !el.isConnected) return false;
		const style = window.getComputedStyle(el);
		if (style.display === 'none' || style.visibility === 'hidden' || parseFloat(style.opacity) === 0) {
			return false;
		}
		const rect = el.getBoundingClientRect();
		return rect.width > 0 && rect.height > 0;
	};

	const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();

	const inDialog = (el) => !!el?.closest('[role="dialog"][aria-modal="true"], .semi-modal-content[role="dialog"]');

	const usernameSelectors = [
		'#username', 'input[name="username"]', 'input[name="email"]',
		'input[type="email"]', 'input[autocomplete="username"]',
		'input[autocomplete="email"]', 'form input[type="password"]',
		'form input:not([type="password"]):not([type="hidden"]):not([type="submit"]):not([type="checkbox"]):not([type="radio"])',
	];
	const findUsername = () => {
		for (const selector of usernameSelectors) {
			const el = document.querySelector(selector);
			if (el && isVisible(el)) return el;
		}
		return null;
	};

	if (findUsername()) return true;

	// 1) 语义匹配按钮文本：包含 邮箱/用户名/Email
	const buttons = [...document.querySelectorAll('button, [role="button"]')]
		.filter(b => isVisible(b) && !inDialog(b) && !b.closest('form'));
	const textRx = /邮箱|用户名|Email|Username|使用.*?登录|Sign.*?in.*?with/i;
	for (const btn of buttons) {
		const txt = norm(btn.innerText || btn.textContent);
		if (!textRx.test(txt)) continue;
		btn.click();
		if (findUsername()) return true;
	}

	// 2) Semi Design 旧选择器
	const entrySelectors = [
		'.semi-card button:has(.semi-icon-mail)',
		'.semi-card button:has([aria-label="mail"])',
	];
	for (const selector of entrySelectors) {
		for (const btn of document.querySelectorAll(selector)) {
			if (!isVisible(btn) || inDialog(btn) || btn.closest('form.semi-form')) continue;
			btn.click();
			if (findUsername()) return true;
		}
	}

	// 3) 按颜色/主按钮样式猜（primary），最后再试
	const primaries = [...document.querySelectorAll('button[class*="primary" i], button[class*="Primary"]')]
		.filter(b => isVisible(b) && !inDialog(b) && !b.closest('form'));
	for (const btn of primaries) {
		const txt = norm(btn.innerText || btn.textContent);
		// 排除 GitHub/LinuxDO 等第三方按钮，优先点含 邮箱/登录 字样的
		const githublike = /github|linuxdo|google|gitlab|twitter|discord|wechat|apple/i.test(txt);
		if (githublike) continue;
		if (!/邮箱|用户名|Email|登录|Login|Sign/i.test(txt)) continue;
		btn.click();
		if (findUsername()) return true;
	}

	// 4) Tabs 切换
	for (const tab of document.querySelectorAll('.semi-card .semi-tabs-tab, [role="tab"], [class*="Tabs"] [role="button"]')) {
		if (!isVisible(tab) || inDialog(tab)) continue;
		tab.click();
		if (findUsername()) return true;
	}

	return !!findUsername();
}"""


@dataclass(frozen=True)
class BrowserLoginResult:
	cookies: dict[str, str]
	api_user: str | None = None


@dataclass(frozen=True)
class BrowserLoginSettings:
	headless: bool
	humanize: bool
	wait_timeout_ms: int
	profile_dir: Path
	cloakbrowser_binary_path: str | None
	persist_profile: bool


def _env_bool(name: str, default: bool) -> bool:
	raw = os.getenv(name)
	if raw is None:
		return default
	return raw.strip().lower() in {'1', 'true', 'yes', 'on'}


def load_browser_login_settings(
	account_name: str, provider: str, *, persist_profile: bool = True
) -> BrowserLoginSettings:
	profile_base = Path(os.getenv('CHECKIN_BROWSER_PROFILE_DIR', '.browser_profiles'))
	profile_dir = profile_base / provider / account_name
	humanize = _env_bool('CHECKIN_HUMANIZE', True)
	if provider == 'agentrouter':
		humanize = _env_bool('CHECKIN_HUMANIZE_AGENTROUTER', humanize)
	return BrowserLoginSettings(
		headless=_env_bool('CHECKIN_HEADLESS', True),
		humanize=humanize,
		wait_timeout_ms=int(os.getenv('CHECKIN_WAIT_TIMEOUT_MS', str(DEFAULT_TIMEOUT_MS))),
		profile_dir=profile_dir,
		cloakbrowser_binary_path=os.getenv('CLOAKBROWSER_BINARY_PATH', '').strip() or None,
		persist_profile=persist_profile,
	)


def _ensure_binary_path(settings: BrowserLoginSettings) -> None:
	if settings.cloakbrowser_binary_path:
		os.environ['CLOAKBROWSER_BINARY_PATH'] = settings.cloakbrowser_binary_path


class _EphemeralBrowserContext:
	def __init__(self, context: BrowserContext, browser) -> None:
		self._context = context
		self._browser = browser

	def __getattr__(self, name: str):
		return getattr(self._context, name)

	async def close(self, *args, **kwargs) -> None:
		try:
			await self._context.close(*args, **kwargs)
		finally:
			await self._browser.close()


async def launch_login_context(settings: BrowserLoginSettings, *, use_proxy: bool = False) -> BrowserContext:
	_ensure_binary_path(settings)

	launch_kwargs: dict = {
		'headless': settings.headless,
		'humanize': settings.humanize,
		'viewport': {'width': 1920, 'height': 1080},
	}
	if settings.humanize:
		launch_kwargs['human_preset'] = 'careful'

	proxy = get_playwright_proxy(use_proxy=use_proxy)
	if proxy:
		launch_kwargs['proxy'] = proxy
		if is_debug_enabled():
			print(f'[INFO] Browser proxy enabled: {proxy["server"]}')
		else:
			print('[INFO] Browser proxy enabled')
	elif use_proxy:
		print('[WARN] Provider requires proxy but CHECKIN_PROXY_URL is not set')

	if settings.persist_profile:
		from cloakbrowser import launch_persistent_context_async

		settings.profile_dir.mkdir(parents=True, exist_ok=True)
		return await launch_persistent_context_async(str(settings.profile_dir), **launch_kwargs)

	from cloakbrowser import launch_async

	context_kwargs = {'viewport': launch_kwargs.pop('viewport')}
	browser = await launch_async(**launch_kwargs)
	context = await browser.new_context(**context_kwargs)
	return _EphemeralBrowserContext(context, browser)


def get_screenshot_dir() -> Path:
	return Path(os.getenv('CHECKIN_SCREENSHOT_DIR', DEFAULT_SCREENSHOT_DIR))


def _sanitize_screenshot_part(value: str) -> str:
	cleaned = re.sub(r'[^\w.-]+', '_', value.strip())
	return cleaned or 'unknown'


async def save_login_screenshot(
	page: Page,
	provider: str,
	account_name: str,
	label: str,
) -> Path | None:
	if not is_debug_enabled():
		return None

	screenshot_dir = get_screenshot_dir()
	screenshot_dir.mkdir(parents=True, exist_ok=True)
	timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
	filename = (
		f'{_sanitize_screenshot_part(provider)}_{_sanitize_screenshot_part(account_name)}'
		f'_{timestamp}_{_sanitize_screenshot_part(label)}.png'
	)
	path = screenshot_dir / filename
	try:
		await page.screenshot(path=str(path), full_page=True, timeout=15_000)
		_pending_notify_screenshots.append(path)
		print(f'[INFO] Screenshot saved: {path}')
		return path
	except Exception as exc:
		print(f'[WARN] Failed to save screenshot ({label}): {exc}')
		return None


def take_pending_screenshots() -> list[Path]:
	"""取出待推送的登录截图列表并清空缓存。"""
	paths = list(_pending_notify_screenshots)
	_pending_notify_screenshots.clear()
	return paths


async def prepare_browser_page(page: Page) -> None:
	await setup_popup_guard(page)


async def wait_for_site_ready(page: Page, timeout_ms: int = WAF_READY_TIMEOUT_MS) -> None:
	"""等待 WAF 通过并关闭弹窗。"""
	waf_timeout = min(timeout_ms, WAF_READY_TIMEOUT_MS)
	await page.wait_for_load_state('domcontentloaded', timeout=waf_timeout)
	try:
		await page.wait_for_function(_SITE_READY_JS, timeout=waf_timeout)
	except Exception:
		await asyncio.sleep(3)
	closed = await dismiss_popups(page)
	if closed:
		print(f'[INFO] Dismissed {closed} popup dialog(s)')


async def _wait_for_optional_load_state(page: Page, state: str, timeout_ms: int) -> bool:
	try:
		await page.wait_for_load_state(state, timeout=timeout_ms)
		return True
	except Exception as exc:  # nosec B110
		debug_print(f'[INFO] Optional load state "{state}" not reached within {timeout_ms}ms: {exc}')
		return False


async def _settle_page(page: Page, delay_seconds: float, networkidle_timeout_ms: int) -> None:
	await asyncio.sleep(delay_seconds)
	await _wait_for_optional_load_state(page, 'networkidle', networkidle_timeout_ms)


async def _wait_for_login_shell(page: Page, timeout_ms: int) -> bool:
	shell_timeout = min(timeout_ms, 60_000)
	try:
		await page.wait_for_function(_LOGIN_SHELL_READY_JS, timeout=shell_timeout)
		return True
	except Exception:  # nosec B110
		return False


async def _wait_for_proxy_ready(base_url: str, timeout_s: int = 30) -> bool:
	"""等待代理（如 VMess URLTest 选节点）就绪，最多 timeout_s 秒。

	VMess sing-box URLTest 启动后几秒内可能在选最优节点，此时代理会
	返回连接重置或空响应。等 URLTest 完成后再访问，成功率显著提高。
	"""
	proxy_cfg = get_playwright_proxy()
	proxy_arg = proxy_cfg.get('server') if proxy_cfg else None
	deadline = time.monotonic() + timeout_s
	last_err = ''
	retry = 0
	while time.monotonic() < deadline:
		retry += 1
		try:
			async with httpx.AsyncClient(
				proxy=proxy_arg,
				timeout=httpx.Timeout(10, connect=5),
				follow_redirects=True,
			) as client:
				resp = await client.head(base_url)
				if resp.status_code < 500:
					if retry > 1:
						print(f'[INFO] Proxy ready after {retry} probes ({resp.status_code})')
					return True
		except Exception as exc:  # nosec B112
			last_err = f'{type(exc).__name__}: {exc}'[:120]
		await asyncio.sleep(min(2, max(0.5, deadline - time.monotonic() - 0.1)))
	print(f'[WARN] Proxy not ready after {timeout_s}s; last err: {last_err}. Continuing anyway...')
	return False


async def navigate_login_page(
	page: Page,
	login_url: str,
	timeout_ms: int,
	*,
	provider: str = '',
	account_name: str = '',
) -> None:
	"""预热站点、导航登录页并等待 SPA 渲染完成。"""
	from urllib.parse import urlparse

	parsed = urlparse(login_url)
	base_url = f'{parsed.scheme}://{parsed.netloc}/'
	attempt_timeout = min(timeout_ms, 60_000)

	# 先等代理就绪（VMess URLTest 启动时选节点需要几秒）
	try:
		await _wait_for_proxy_ready(base_url, timeout_s=30)
	except Exception as exc:  # nosec B110
		print(f'[WARN] _wait_for_proxy_ready skipped: {exc}')

	try:
		print(f'[INFO] Warming up {base_url} before login')
		await page.goto(base_url, wait_until='load', timeout=attempt_timeout)
		await _settle_page(page, 3, 15_000)
		closed = await dismiss_popups(page)
		if closed:
			print(f'[INFO] Dismissed {closed} popup dialog(s) during warmup')
	except Exception as exc:
		print(f'[WARN] Warmup navigation failed: {exc}')

	for attempt in range(3):
		print(f'[INFO] Navigating login page (attempt {attempt + 1}/3): {login_url}')
		await page.goto(login_url, wait_until='load', timeout=attempt_timeout)
		await _settle_page(page, 5, 20_000)

		if await _wait_for_login_shell(page, attempt_timeout):
			await wait_for_site_ready(page, timeout_ms)
			if await page.evaluate(_LOGIN_SHELL_READY_JS):
				return

		print(f'[WARN] Login page shell not ready on attempt {attempt + 1}')
		await _log_login_page_state(page)
		if provider and account_name:
			await save_login_screenshot(page, provider, account_name, f'login-shell-attempt-{attempt + 1}')
		if attempt < 2:
			await asyncio.sleep(5)
			try:
				await page.reload(wait_until='load', timeout=attempt_timeout)
			except Exception:  # nosec B110
				pass

	raise TimeoutError(f'Login page never rendered: {login_url}')


async def has_session_cookie(page: Page) -> bool:
	cookies = await page.context.cookies()
	return any(c.get('name') == SESSION_COOKIE_NAME and c.get('value') for c in cookies)


def _extract_user_profile(payload: object) -> dict | None:
	if not isinstance(payload, dict):
		return None
	data = payload.get('data')
	if payload.get('success') is True and isinstance(data, dict) and data.get('id'):
		return data
	if payload.get('id'):
		return payload
	return None


async def _parse_user_self_response(response) -> dict | None:
	if USER_SELF_API_SUFFIX not in response.url or response.status != 200:
		return None
	try:
		payload = await response.json()
	except Exception:  # nosec B110
		return None
	return _extract_user_profile(payload)


async def is_logged_in(page: Page) -> bool:
	"""快速判断：是否在 /console，或仍停留在登录页。"""
	url = page.url.lower()
	if CONSOLE_PATH in url:
		return True
	if '/login' in url or '/signin' in url or '/sign-in' in url:
		return False

	try:
		if await page.locator('.semi-card button:has(.semi-icon-mail)').first.is_visible():
			return False
	except Exception:  # nosec B110
		pass
	return False


async def wait_for_session_cookie(page: Page, timeout_ms: int = SESSION_WAIT_TIMEOUT_MS) -> bool:
	deadline = time.monotonic() + timeout_ms / 1000
	while time.monotonic() < deadline:
		if await has_session_cookie(page):
			return True
		await asyncio.sleep(0.5)
	return False


async def wait_for_logged_in(page: Page, timeout_ms: int = SESSION_WAIT_TIMEOUT_MS) -> bool:
	deadline = time.monotonic() + timeout_ms / 1000
	while time.monotonic() < deadline:
		if await is_logged_in(page):
			return True
		await asyncio.sleep(0.5)
	return False


async def verify_browser_login(page: Page, console_url: str, timeout_ms: int) -> dict | None:
	"""跳转 /console 并拦截 /api/user/self，用浏览器会话确认登录用户。"""
	verify_timeout = min(timeout_ms, SESSION_WAIT_TIMEOUT_MS)
	captured_profile: dict | None = None
	verified = asyncio.Event()

	async def on_response(response) -> None:
		nonlocal captured_profile
		if captured_profile is not None:
			return
		profile = await _parse_user_self_response(response)
		if profile:
			captured_profile = profile
			verified.set()

	page.on('response', on_response)
	try:
		print(f'[INFO] Verifying login via {console_url} and {USER_SELF_API_SUFFIX}')
		await page.goto(console_url, wait_until='load', timeout=min(timeout_ms, 60_000))
		try:
			await page.wait_for_load_state('networkidle', timeout=20_000)
		except Exception:  # nosec B110
			pass

		if captured_profile is None:
			try:
				await asyncio.wait_for(verified.wait(), timeout=verify_timeout / 1000)
			except TimeoutError:
				pass
	finally:
		page.remove_listener('response', on_response)

	if captured_profile:
		if is_debug_enabled():
			user_id = captured_profile.get('id')
			username = captured_profile.get('username', '')
			print(f'[INFO] Login verified via {USER_SELF_API_SUFFIX}: id={user_id}, username={username}')
		else:
			print('[INFO] Login verified')
		return captured_profile

	if CONSOLE_PATH in page.url.lower():
		print(f'[WARN] Reached {CONSOLE_PATH} but {USER_SELF_API_SUFFIX} returned no user profile')
	else:
		debug_print(f'[WARN] Login verification failed: current URL={page.url}')
		print('[WARN] Login verification failed')
	return None


async def wait_for_waf_ready(page: Page, timeout_ms: int = WAF_READY_TIMEOUT_MS) -> None:
	await wait_for_site_ready(page, timeout_ms)


async def _first_visible_locator(page: Page, selectors: tuple[str, ...]) -> Locator | None:
	for selector in selectors:
		locator = page.locator(selector).first
		try:
			if await locator.is_visible():
				return locator
		except Exception:  # nosec B112
			continue
	return None


async def _is_email_form_visible(page: Page) -> bool:
	return await _first_visible_locator(page, USERNAME_SELECTORS) is not None


async def _dismiss_blocking_overlays(page: Page) -> None:
	if await _is_email_form_visible(page):
		return
	for _ in range(3):
		closed = await dismiss_popups(page)
		if closed == 0:
			break
		await asyncio.sleep(0.3)


async def _click_locator(button: Locator) -> bool:
	try:
		await button.scroll_into_view_if_needed()
		await button.click(timeout=FORM_ACTION_TIMEOUT_MS)
		return True
	except Exception:
		try:
			await button.click(force=True, timeout=FORM_ACTION_TIMEOUT_MS)
			return True
		except Exception:  # nosec B112
			return False


async def _wait_for_login_page_ready(page: Page, timeout_ms: int) -> None:
	if await _is_email_form_visible(page):
		return

	remaining_ms = timeout_ms
	for selector in LOGIN_PAGE_READY_SELECTORS:
		if remaining_ms <= 0:
			break
		try:
			await page.locator(selector).first.wait_for(state='visible', timeout=remaining_ms)
			return
		except Exception:  # nosec B112
			continue

	for pattern in EMAIL_LOGIN_BUTTON_NAMES:
		if remaining_ms <= 0:
			break
		try:
			await page.get_by_role('button', name=pattern).first.wait_for(state='visible', timeout=remaining_ms)
			return
		except Exception:  # nosec B112
			continue


async def _click_email_login_entry(page: Page) -> bool:
	for selector in EMAIL_LOGIN_ENTRY_SELECTORS:
		buttons = page.locator(selector)
		button_count = await buttons.count()
		for index in range(button_count):
			button = buttons.nth(index)
			try:
				if await button.is_visible():
					# 跳过第三方登录按钮（GitHub / LinuxDO 等）
					try:
						txt = (await button.inner_text(timeout=2000) or '').replace(r'\s+', ' ').strip()
					except Exception:  # nosec B110
						txt = ''
					if re.search(r'github|linuxdo|google|gitlab|twitter|discord|wechat|apple', txt, re.I):
						continue
					if not re.search(r'邮箱|用户名|Email|Username|登录|Login|Sign', txt, re.I):
						# Semi Design 图标按钮可能无文字，仍尝试点击（.semi-icon-mail 路径）
						if not re.search(r'semi-icon-mail|aria-label.*mail|aria-label.*email',
						                 await button.evaluate('el => el.outerHTML.slice(0, 500)') or '',
						                 re.I):
							continue
					if await _click_locator(button):
						return True
			except Exception:  # nosec B112
				continue

	for pattern in EMAIL_LOGIN_BUTTON_NAMES:
		for scope in (page.locator('.semi-card'), page.locator('main'), page.locator('[class*="Login"]'), page):
			try:
				button = scope.get_by_role('button', name=pattern).first
				if await button.is_visible() and await _click_locator(button):
					return True
			except Exception:  # nosec B112
				continue

	return False


async def _wait_for_username_input(page: Page, timeout_ms: int) -> bool:
	if timeout_ms <= 0:
		return await _is_email_form_visible(page)

	for selector in USERNAME_SELECTORS:
		try:
			await page.locator(selector).first.wait_for(state='visible', timeout=timeout_ms)
			return True
		except Exception:  # nosec B112
			continue
	return False


async def _log_login_page_state(page: Page) -> None:
	state = await page.evaluate(
		"""() => {
			const isVisible = (el) => {
				if (!el || !el.isConnected) return false;
				const style = window.getComputedStyle(el);
				if (style.display === 'none' || style.visibility === 'hidden' || parseFloat(style.opacity) === 0) return false;
				const rect = el.getBoundingClientRect();
				return rect.width > 0 && rect.height > 0;
			};
			const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim().slice(0, 80);
			const buttons = [...document.querySelectorAll('button, [role="button"]')]
				.filter(isVisible)
				.map((b) => norm(b.innerText || b.textContent || ''));
			const inputs = [...document.querySelectorAll('input')]
				.filter(isVisible)
				.map((i) => ({
					type: i.type || '',
					name: i.name || '',
					id: i.id || '',
					auto: i.getAttribute('autocomplete') || '',
					placeholder: (i.getAttribute('placeholder') || '').slice(0, 40),
				}));
			const modals = [...document.querySelectorAll('[aria-modal="true"], [role="dialog"]')]
				.filter(isVisible).length;
			return {
				title: document.title || '',
				readyState: document.readyState,
				url: location.pathname,
				bodySnippet: norm(document.body?.innerText || '').slice(0, 300),
				scriptCount: document.querySelectorAll('script').length,
				hasSemiCard: !!document.querySelector('.semi-card'),
				mailEntryCount: document.querySelectorAll('.semi-card button:has(.semi-icon-mail)').length,
				usernameVisible: isVisible(document.querySelector('#username')),
				passwordVisible: isVisible(document.querySelector('input[type="password"]')),
				modalVisible: modals,
				buttons: buttons.slice(0, 12),
				inputs: inputs.slice(0, 8),
			};
		}"""
	)
	debug_print(f'[INFO] Login page state: {state}')


async def _open_email_login_form(
	page: Page,
	timeout_ms: int,
	*,
	provider: str = '',
	account_name: str = '',
) -> None:
	deadline = time.monotonic() + timeout_ms / 1000

	await _dismiss_blocking_overlays(page)
	if await _is_email_form_visible(page):
		return

	ready_timeout = min(timeout_ms, WAF_READY_TIMEOUT_MS)
	try:
		await _wait_for_login_page_ready(page, ready_timeout)
	except Exception:  # nosec B110
		pass

	while time.monotonic() < deadline:
		remaining_ms = int((deadline - time.monotonic()) * 1000)
		if remaining_ms <= 0:
			break

		await _dismiss_blocking_overlays(page)
		if await _is_email_form_visible(page):
			return

		if await _click_email_login_entry(page):
			await asyncio.sleep(1)
			wait_ms = min(remaining_ms, FORM_ACTION_TIMEOUT_MS)
			if await _wait_for_username_input(page, wait_ms):
				return

		tabs = page.locator('.semi-card .semi-tabs-tab')
		tab_count = await tabs.count()
		for index in range(tab_count):
			tab = tabs.nth(index)
			if not await tab.is_visible():
				continue
			await tab.click(timeout=EMAIL_TAB_TIMEOUT_MS)
			wait_ms = min(int((deadline - time.monotonic()) * 1000), EMAIL_TAB_TIMEOUT_MS)
			if await _wait_for_username_input(page, wait_ms):
				return

		if await page.evaluate(_OPEN_EMAIL_FORM_JS):
			await asyncio.sleep(1)
			wait_ms = min(int((deadline - time.monotonic()) * 1000), FORM_ACTION_TIMEOUT_MS)
			if await _wait_for_username_input(page, wait_ms):
				return

		await asyncio.sleep(0.5)

	remaining_ms = int((deadline - time.monotonic()) * 1000)
	if remaining_ms > 0 and await _wait_for_username_input(page, remaining_ms):
		return

	debug_print(f'[INFO] Login page URL: {page.url}')
	await _log_login_page_state(page)
	if provider and account_name:
		await save_login_screenshot(page, provider, account_name, 'email-form-timeout')
	raise TimeoutError(f'Cannot open email login form, selectors: {USERNAME_SELECTORS}')


async def _set_input_value(locator: Locator, value: str, timeout_ms: int) -> None:
	click_timeout = min(timeout_ms, 5000)
	try:
		await locator.click(timeout=click_timeout)
	except Exception:
		try:
			await locator.click(force=True, timeout=click_timeout)
		except Exception:  # nosec B110
			pass

	try:
		await locator.fill(value, timeout=timeout_ms)
		if await locator.input_value(timeout=2000) == value:
			return
	except Exception:  # nosec B110
		pass

	await locator.evaluate(
		"""(el, v) => {
			const setter = Object.getOwnPropertyDescriptor(
				window.HTMLInputElement.prototype, 'value'
			)?.set;
			setter?.call(el, v);
			el.dispatchEvent(new Event('input', { bubbles: true }));
			el.dispatchEvent(new Event('change', { bubbles: true }));
		}""",
		value,
	)


async def fill_email_credentials(page: Page, email: str, password: str, timeout_ms: int) -> None:
	await _dismiss_blocking_overlays(page)
	action_timeout = min(timeout_ms, FORM_ACTION_TIMEOUT_MS)

	username_input = await _first_visible_locator(page, USERNAME_SELECTORS)
	if not username_input:
		for selector in USERNAME_SELECTORS:
			locator = page.locator(selector).first
			try:
				await locator.wait_for(state='visible', timeout=action_timeout)
				username_input = locator
				break
			except Exception:  # nosec B112
				continue
	if not username_input:
		raise TimeoutError(f'Cannot find username input: {USERNAME_SELECTORS}')

	password_input = await _first_visible_locator(page, PASSWORD_SELECTORS)
	if not password_input:
		for selector in PASSWORD_SELECTORS:
			locator = page.locator(selector).first
			try:
				await locator.wait_for(state='visible', timeout=action_timeout)
				password_input = locator
				break
			except Exception:  # nosec B112
				continue
	if not password_input:
		raise TimeoutError(f'Cannot find password input: {PASSWORD_SELECTORS}')

	await _set_input_value(username_input, email, action_timeout)
	await _set_input_value(password_input, password, action_timeout)


_SUBMIT_LABELS = (
	re.compile(r'继续'),
	re.compile(r'下一步'),
	re.compile(r'确认'),
	re.compile(r'登录'),
	re.compile(r'登 录'),
	re.compile(r'Next', re.I),
	re.compile(r'Continue', re.I),
	re.compile(r'Sign\s*in', re.I),
	re.compile(r'Log\s*in', re.I),
	re.compile(r'Login', re.I),
	re.compile(r'^\s*继续\s*$'),
	re.compile(r'^\s*登录\s*$'),
	re.compile(r'^\s*登 录\s*$'),
	re.compile(r'^\s*下一步\s*$'),
	re.compile(r'^\s*确认\s*$'),
	re.compile(r'^\s*Next\s*$', re.I),
	re.compile(r'^\s*Continue\s*$', re.I),
	re.compile(r'^\s*Sign\s*in\s*$', re.I),
	re.compile(r'^\s*Log\s*in\s*$', re.I),
	re.compile(r'^\s*Login\s*$', re.I),
)


_SUBMIT_NEGATIVE = (
	re.compile(r'忘记|重置|找回', re.I),
	re.compile(r'forgot|reset|recover|resend', re.I),
	re.compile(r'注册|signup|sign\s*up|新用户|创建账号', re.I),
	re.compile(r'register|create.*account|join\s*now', re.I),
	re.compile(r'切换|换一|换个|其他方式|其他登录|切换到', re.I),
	re.compile(r'微信|wechat|apple|google|github|linuxdo|qrcode|qr|扫码', re.I),
	re.compile(r'取消|cancel|关闭|close|稍后|later', re.I),
	re.compile(r'显示密码|隐藏密码|toggle.*password|眼睛|可见|不可见|显示', re.I),
)


def _label_suspicious(text: str) -> bool:
	if not text:
		return False
	t = re.sub(r'\s+', ' ', text).strip()
	if not t:
		return False
	for rx in _SUBMIT_NEGATIVE:
		if rx.search(t):
			return True
	return False


async def _count_login_form_inputs(page: Page) -> tuple[int, int]:
	async def _cnt(sels) -> int:
		cnt = 0
		for s in sels:
			try:
				cnt += await page.locator(s).count()
			except Exception:  # nosec B112
				pass
		return cnt
	return await _cnt(USERNAME_SELECTORS), await _cnt(PASSWORD_SELECTORS)


async def _submit_happened(page_before_url: str, page_after_url: str, inputs_before: tuple[int, int], inputs_after: tuple[int, int]) -> bool:
	"""判断点击提交按钮后是否真的触发了提交/跳转/表单变化；但要排除跳错页（如/reset、/register）。"""
	if page_after_url != page_before_url:
		return True
	if inputs_before[0] > 0 and inputs_after[0] == 0:
		return True
	if inputs_after[1] < inputs_before[1]:
		return True
	return False


def _submit_destination_ok(origin_url: str, current_url: str) -> bool:
	"""按钮点击后跳转到的 URL 是否在"预期方向"。允许 /console、/dashboard、原 login 页本身（表单在处理），禁止 /reset/register 等。"""
	from urllib.parse import urlparse
	o = urlparse(origin_url)
	c = urlparse(current_url)
	# 域名或协议变了 -> 一般是正确外部 OAuth 重定向（我们不用），先允许
	if (o.scheme, o.netloc) != (c.scheme, c.netloc):
		return True
	path = c.path.rstrip('/')
	origin_path = o.path.rstrip('/')
	if path == origin_path:
		return True
	# 允许跳转到控制台、首页、仪表板、API 调用后的回跳
	if path in ('', '/', CONSOLE_PATH.rstrip('/'), '/dashboard', '/app', '/home', '/user', '/account'):
		return True
	if path.startswith(CONSOLE_PATH.rstrip('/')) or path.startswith('/dashboard') or path.startswith('/app'):
		return True
	# 如果路径名是 /login/xxx 也允许（login 子路径）
	if path.startswith(origin_path + '/'):
		return True
	# 负向：/reset /forgot /register /signup /signup/invite 等一律禁止
	NEG_PATHS = (
		'/reset', '/forgot', '/recover', '/password-reset', '/password_reset',
		'/register', '/signup', '/sign-up', '/join', '/invite', '/verify',
	)
	low = path.lower()
	for n in NEG_PATHS:
		if low.startswith(n):
			return False
	return True


async def submit_login_form(page: Page, timeout_ms: int) -> None:
	action_timeout = min(timeout_ms, FORM_ACTION_TIMEOUT_MS)
	baseline_url = page.url
	baseline_inputs = await _count_login_form_inputs(page)
	already_tried: list[Locator] = []

	async def _candidate_skip(submit: Locator) -> str | None:
		"""如果候选按钮不应被点，返回原因文本；否则返回 None。"""
		try:
			if not await submit.is_visible():
				return 'not visible'
		except Exception:
			return 'is_visible failed'
		# 重复检查
		for t in already_tried:
			try:
				if await submit.evaluate('(a, b) => a === b', t):
					return 'duplicate'
			except Exception:  # nosec B112
				pass
		# disabled 检查
		try:
			disabled = await submit.evaluate(
				'el => !!el.closest?.("[disabled], [aria-disabled=\"true\"]") || !!el.disabled || el.getAttribute?.("aria-disabled") === "true"'
			)
			if disabled:
				return 'disabled'
		except Exception:  # nosec B110
			pass
		# 文本负向过滤：通过 innerText + aria-label + 候选按钮的 content 检查
		try:
			text_parts = []
			try:
				text_parts.append(await submit.inner_text(timeout=1500) or '')
			except Exception:
				pass
			for attr in ('aria-label', 'title', 'value', 'placeholder'):
				try:
					v = await submit.get_attribute(attr)
					if v:
						text_parts.append(v)
				except Exception:
					pass
			joined = ' '.join(text_parts)
			if _label_suspicious(joined):
				return f'suspicious label: {joined[:120]!r}'
		except Exception:  # nosec B110
			pass
		return None

	async def _try_click(submit: Locator) -> tuple[bool, str]:
		skip_reason = await _candidate_skip(submit)
		if skip_reason is not None:
			return False, skip_reason
		already_tried.append(submit)
		try:
			await submit.scroll_into_view_if_needed()
			await submit.click(timeout=action_timeout)
		except Exception:  # nosec B110
			try:
				await submit.click(force=True, timeout=action_timeout)
			except Exception:  # nosec B110
				# 兜底：密码框聚焦后按 Enter
				pw = await _first_visible_locator(page, PASSWORD_SELECTORS)
				if pw is not None:
					try:
						await pw.focus()
						await page.keyboard.press('Enter')
					except Exception:  # nosec B110
						pass
		# 等待页面是否有反应
		changed = False
		try:
			await asyncio.wait_for(
				page.wait_for_function(
					"""([u0, nuser0, npw0]) => {
						if (location.href !== u0) return true;
						const countSel = (sels) => {
							let c = 0;
							for (const s of sels) {
								try { c += document.querySelectorAll(s).length; } catch {}
							}
							return c;
						};
						const nuser = countSel(['#username','input[name="username"]','input[name="email"]','input[type="email"]','input[autocomplete="username"]','input[autocomplete="email"]']);
						const npw = countSel(['#password','input[name="password"]','input[type="password"]','input[autocomplete="current-password"]']);
						if (nuser < nuser0) return true;
						if (npw < npw0) return true;
						return false;
					}""",
					arg=[baseline_url, baseline_inputs[0], baseline_inputs[1]],
					timeout=8000,
				),
				timeout=9,
			)
			changed = True
		except Exception:  # nosec B110
			try:
				changed = page.url != baseline_url
			except Exception:
				changed = False
		# 如果 URL 错了（跳到 /reset 等），立刻回退
		if changed and not _submit_destination_ok(baseline_url, page.url):
			bad_url = page.url
			try:
				await page.go_back(wait_until='domcontentloaded', timeout=10000)
			except Exception:  # nosec B110
				try:
					await page.goto(baseline_url, wait_until='domcontentloaded', timeout=15000)
				except Exception:  # nosec B110
					pass
			await asyncio.sleep(1.5)
			return False, f'navigated to bad URL {bad_url}, went back'
		return changed, 'clicked'

	# 收集所有候选：先 role，后 selector
	candidates: list[Locator] = []
	for pattern in _SUBMIT_LABELS:
		for scope in (page.locator('form'), page):
			try:
				candidates.append(scope.get_by_role('button', name=pattern).first)
			except Exception:  # nosec B112
				pass
	for selector in SUBMIT_SELECTORS:
		try:
			candidates.append(page.locator(selector).first)
		except Exception:  # nosec B112
			pass

	for loc in candidates:
		ok, reason = await _try_click(loc)
		if ok:
			break
	else:
		# 全部候选失败，最后兜底：密码框 + Enter
		pw = await _first_visible_locator(page, PASSWORD_SELECTORS)
		if pw is not None:
			try:
				await pw.focus()
				await page.keyboard.press('Enter')
				await asyncio.sleep(3)
			except Exception:  # nosec B110
				pass

	current_inputs = await _count_login_form_inputs(page)
	happened = _submit_happened(baseline_url, page.url, baseline_inputs, current_inputs)
	dest_ok = _submit_destination_ok(baseline_url, page.url)
	if current_inputs[0] > 0 and happened and not dest_ok:
		raise TimeoutError(
			f'Submit failed: navigated to bad destination {page.url!r} (expected login/console). '
			f'Tried {len(already_tried)} candidates.'
		)
	if current_inputs[0] > 0 and not happened:
		raise TimeoutError(
			f'Submit button click failed: tried {len(already_tried)} candidates, '
			f'URL/form unchanged. Inputs before={baseline_inputs} after={current_inputs}'
		)

	await _wait_for_optional_load_state(page, 'domcontentloaded', action_timeout)
	await _wait_for_optional_load_state(page, 'networkidle', min(timeout_ms, 30_000))
	await wait_for_logged_in(page, SESSION_WAIT_TIMEOUT_MS)


async def login_with_email_form(
	page: Page,
	email: str,
	password: str,
	timeout_ms: int,
	*,
	provider: str = '',
	account_name: str = '',
) -> None:
	await _open_email_login_form(
		page,
		timeout_ms,
		provider=provider,
		account_name=account_name,
	)
	await fill_email_credentials(page, email, password, timeout_ms)
	await submit_login_form(page, timeout_ms)
