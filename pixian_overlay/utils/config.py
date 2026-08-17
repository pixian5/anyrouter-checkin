#!/usr/bin/env python3
"""
配置管理模块
"""

import json
import os
from dataclasses import dataclass
from typing import Dict, List, Literal
from urllib.parse import urlsplit


@dataclass
class ProviderConfig:
	"""Provider 配置"""

	name: str
	domain: str
	login_path: str = '/login'
	console_path: str = '/console'
	sign_in_path: str | None = '/api/user/sign_in'
	user_info_path: str = '/api/user/self'
	api_user_key: str = 'new-api-user'
	bypass_method: Literal['waf_cookies'] | None = None
	waf_cookie_names: List[str] | None = None
	persist_profile: bool = False

	def __post_init__(self):
		if not isinstance(self.name, str) or not self.name.strip():
			raise ValueError('provider name cannot be empty')
		self.name = self.name.strip()
		if not isinstance(self.domain, str):
			raise ValueError('provider domain must be an HTTP(S) URL')
		self.domain = self.domain.strip().rstrip('/')
		parsed_domain = urlsplit(self.domain)
		if parsed_domain.scheme not in {'http', 'https'} or not parsed_domain.netloc:
			raise ValueError('provider domain must be an HTTP(S) URL')
		for field_name in ('login_path', 'console_path', 'user_info_path'):
			path = getattr(self, field_name)
			if not isinstance(path, str) or not path.startswith('/'):
				raise ValueError(f'{field_name} must start with /')
		if self.sign_in_path is not None and (
			not isinstance(self.sign_in_path, str) or not self.sign_in_path.startswith('/')
		):
			raise ValueError('sign_in_path must be null or start with /')
		if not isinstance(self.api_user_key, str) or not self.api_user_key.strip():
			raise ValueError('api_user_key cannot be empty')
		self.api_user_key = self.api_user_key.strip()
		if not isinstance(self.persist_profile, bool):
			raise ValueError('persist_profile must be a boolean')
		if self.bypass_method not in (None, 'waf_cookies'):
			raise ValueError('unsupported bypass_method')
		if self.waf_cookie_names is not None and not isinstance(self.waf_cookie_names, list):
			raise ValueError('waf_cookie_names must be an array')

		required_waf_cookies: list[str] = []
		if self.waf_cookie_names:
			for item in self.waf_cookie_names:
				name = '' if not item or not isinstance(item, str) else item.strip()
				if not name:
					print(f'[WARNING] Found invalid WAF cookie name: {item}')
					continue

				if name not in required_waf_cookies:
					required_waf_cookies.append(name)

		if not required_waf_cookies:
			self.bypass_method = None

		self.waf_cookie_names = list(required_waf_cookies)

	@classmethod
	def from_dict(cls, name: str, data: dict, *, defaults: 'ProviderConfig | None' = None) -> 'ProviderConfig':
		"""从字典创建 ProviderConfig

		配置格式:
		- 基础: {"domain": "https://example.com"}
		- 完整: {"domain": "https://example.com", "login_path": "/login", ...}
		"""
		default_persist_profile = defaults.persist_profile if defaults else False
		default_domain = defaults.domain if defaults else None
		domain = data.get('domain') or default_domain
		if not isinstance(domain, str) or not domain:
			raise ValueError('domain is required for a new provider')
		return cls(
			name=name,
			domain=domain,
			login_path=data.get('login_path', defaults.login_path if defaults else '/login'),
			console_path=data.get('console_path', defaults.console_path if defaults else '/console'),
			sign_in_path=data.get('sign_in_path', defaults.sign_in_path if defaults else '/api/user/sign_in'),
			user_info_path=data.get('user_info_path', defaults.user_info_path if defaults else '/api/user/self'),
			api_user_key=data.get('api_user_key', defaults.api_user_key if defaults else 'new-api-user'),
			bypass_method=data.get('bypass_method', defaults.bypass_method if defaults else None),
			waf_cookie_names=data.get('waf_cookie_names', defaults.waf_cookie_names if defaults else None),
			persist_profile=data.get('persist_profile', default_persist_profile),
		)

	def needs_waf_cookies(self) -> bool:
		"""判断是否需要获取 WAF cookies"""
		return self.bypass_method == 'waf_cookies'

	def needs_manual_check_in(self) -> bool:
		"""判断是否需要手动调用签到接口"""
		return self.sign_in_path is not None


@dataclass
class AppConfig:
	"""应用配置"""

	providers: Dict[str, ProviderConfig]

	@classmethod
	def load_from_env(cls) -> 'AppConfig':
		"""从环境变量加载配置"""
		providers = {
			'anyrouter': ProviderConfig(
				name='anyrouter',
				domain='https://anyrouter.top',
				login_path='/login',
				sign_in_path='/api/user/sign_in',
				user_info_path='/api/user/self',
				api_user_key='new-api-user',
				bypass_method='waf_cookies',
				waf_cookie_names=['acw_tc', 'cdn_sec_tc', 'acw_sc__v2'],
				persist_profile=True,
			),
			'agentrouter': ProviderConfig(
				name='agentrouter',
				domain='https://agentrouter.org',
				login_path='/login',
				sign_in_path=None,  # 无需签到接口，查询用户信息时自动完成签到
				user_info_path='/api/user/self',
				api_user_key='new-api-user',
				bypass_method='waf_cookies',
				waf_cookie_names=['acw_tc'],
				persist_profile=False,
			),
		}

		# 尝试从环境变量加载自定义 providers
		providers_str = os.getenv('PROVIDERS')
		if providers_str:
			try:
				providers_data = json.loads(providers_str)

				if not isinstance(providers_data, dict):
					print('[WARNING] PROVIDERS must be a JSON object, ignoring custom providers')
					return cls(providers=providers)

				# 解析自定义 providers,会覆盖默认配置
				for name, provider_data in providers_data.items():
					try:
						providers[name] = ProviderConfig.from_dict(
							name,
							provider_data,
							defaults=providers.get(name),
						)
					except Exception as e:
						print(f'[WARNING] Failed to parse provider "{name}": {e}, skipping')
						continue

				print(f'[INFO] Loaded {len(providers_data)} custom provider(s) from PROVIDERS environment variable')
			except json.JSONDecodeError as e:
				print(
					f'[WARNING] Failed to parse PROVIDERS environment variable: {e}, using default configuration only'
				)
			except Exception as e:
				print(f'[WARNING] Error loading PROVIDERS: {e}, using default configuration only')

		return cls(providers=providers)

	def get_provider(self, name: str) -> ProviderConfig | None:
		"""获取指定 provider 配置"""
		return self.providers.get(name)


@dataclass
class AccountConfig:
	"""账号配置"""

	cookies: dict | str | None
	api_user: str | None = None
	provider: str = 'anyrouter'
	name: str | None = None
	email: str | None = None
	password: str | None = None

	@classmethod
	def from_dict(cls, data: dict, index: int) -> 'AccountConfig':
		"""从字典创建 AccountConfig"""
		provider = data.get('provider', 'anyrouter').strip()
		name = data.get('name')
		name = name.strip() if isinstance(name, str) else name
		email = data.get('email')
		email = email.strip() if isinstance(email, str) else email
		api_user = data.get('api_user')
		api_user = str(api_user).strip() if api_user is not None else None

		return cls(
			cookies=data.get('cookies'),
			api_user=api_user,
			provider=provider,
			name=name,
			email=email,
			password=data.get('password'),
		)

	def has_login_credentials(self) -> bool:
		"""是否配置了邮箱密码登录"""
		return bool(self.email and self.password)

	def get_display_name(self, index: int) -> str:
		"""获取显示名称，优先显示name，其次邮箱，其次API用户ID"""
		if self.name:
			return self.name
		if self.email:
			return self.email
		if self.api_user:
			return self.api_user
		return f'Account {index + 1}'


def load_accounts_config() -> list[AccountConfig] | None:
	"""从环境变量加载账号配置"""
	accounts_str = os.getenv('ANYROUTER_ACCOUNTS')
	if not accounts_str:
		print('ERROR: ANYROUTER_ACCOUNTS environment variable not found')
		return None

	try:
		accounts_data = json.loads(accounts_str)
	except json.JSONDecodeError as e:
		print(f'ERROR: ANYROUTER_ACCOUNTS JSON 解析失败: {e}')
		print('HINT: 常见原因 - 末尾多余逗号、使用了单引号、包含注释、或换行格式问题')
		return None

	try:
		if not isinstance(accounts_data, list):
			print('ERROR: Account configuration must use array format [{}]')
			return None

		accounts = []
		for i, account_dict in enumerate(accounts_data):
			if not isinstance(account_dict, dict):
				print(f'ERROR: Account {i + 1} configuration format is incorrect')
				return None

			provider = account_dict.get('provider', 'anyrouter')
			if not isinstance(provider, str) or not provider.strip():
				print(f'ERROR: Account {i + 1} provider must be a non-empty string')
				return None
			email = account_dict.get('email')
			password = account_dict.get('password')
			if email is not None and (not isinstance(email, str) or not email.strip()):
				print(f'ERROR: Account {i + 1} email must be a non-empty string')
				return None
			if password is not None and (not isinstance(password, str) or not password):
				print(f'ERROR: Account {i + 1} password must be a non-empty string')
				return None
			has_login = bool(email and password)
			api_user = account_dict.get('api_user')
			if not has_login and (api_user is None or not str(api_user).strip()):
				print(
					f'ERROR: Account {i + 1} missing required field (api_user) - only email+password login can omit it'
				)
				return None

			has_cookies = 'cookies' in account_dict and account_dict['cookies']
			if not has_cookies and not has_login:
				print(f'ERROR: Account {i + 1} must have either cookies or email+password')
				return None

			if 'name' in account_dict and (
				not isinstance(account_dict['name'], str) or not account_dict['name'].strip()
			):
				print(f'ERROR: Account {i + 1} name field cannot be empty')
				return None

			accounts.append(AccountConfig.from_dict(account_dict, i))

		return accounts
	except Exception as e:
		print(f'ERROR: Account configuration format is incorrect: {e}')
		return None
