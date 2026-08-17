import json

from pixian_overlay.utils.config import AppConfig, ProviderConfig, load_accounts_config


def test_builtin_provider_profile_persistence_defaults(monkeypatch):
	monkeypatch.delenv('PROVIDERS', raising=False)

	config = AppConfig.load_from_env()

	assert config.providers['anyrouter'].persist_profile is True
	assert config.providers['agentrouter'].persist_profile is False
	assert config.providers['anyrouter'].console_path == '/console'


def test_provider_profile_persistence_can_override_builtin(monkeypatch):
	monkeypatch.setenv(
		'PROVIDERS',
		json.dumps(
			{
				'anyrouter': {'domain': 'https://anyrouter.top', 'persist_profile': False},
				'agentrouter': {'domain': 'https://agentrouter.org', 'persist_profile': True},
			}
		),
	)

	config = AppConfig.load_from_env()

	assert config.providers['anyrouter'].persist_profile is False
	assert config.providers['agentrouter'].persist_profile is True


def test_custom_provider_profile_persistence_defaults_to_false(monkeypatch):
	monkeypatch.setenv('PROVIDERS', json.dumps({'custom': {'domain': 'https://custom.example.com'}}))

	config = AppConfig.load_from_env()

	assert config.providers['custom'].persist_profile is False


def test_partial_builtin_provider_override_inherits_defaults(monkeypatch):
	monkeypatch.setenv('PROVIDERS', json.dumps({'anyrouter': {'persist_profile': False}}))

	config = AppConfig.load_from_env()
	provider = config.get_provider('anyrouter')

	assert provider is not None
	assert provider.domain == 'https://anyrouter.top'
	assert provider.persist_profile is False


def test_provider_from_dict_inherits_profile_persistence_from_defaults():
	defaults = ProviderConfig(name='custom', domain='https://old.example.com', persist_profile=True)

	provider = ProviderConfig.from_dict(
		'custom',
		{'domain': 'https://new.example.com'},
		defaults=defaults,
	)

	assert provider.persist_profile is True


def test_custom_provider_can_override_console_path_and_user_info_path():
	provider = ProviderConfig.from_dict(
		'custom',
		{'domain': 'https://example.test', 'console_path': '/dashboard', 'user_info_path': '/api/profile'},
	)
	assert provider.console_path == '/dashboard'
	assert provider.user_info_path == '/api/profile'


def test_account_config_rejects_null_api_user_without_email_login(monkeypatch):
	monkeypatch.setenv(
		'ANYROUTER_ACCOUNTS',
		json.dumps([{'api_user': None, 'cookies': {'session': 'token'}, 'provider': 'anyrouter'}]),
	)

	assert load_accounts_config() is None


def test_account_config_normalizes_string_identity_fields(monkeypatch):
	monkeypatch.setenv(
		'ANYROUTER_ACCOUNTS',
		json.dumps(
			[
				{
					'api_user': 123,
					'cookies': {'session': 'token'},
					'provider': ' anyrouter ',
					'name': ' primary ',
				}
			]
		),
	)

	accounts = load_accounts_config()

	assert accounts is not None
	assert accounts[0].api_user == '123'
	assert accounts[0].provider == 'anyrouter'
	assert accounts[0].name == 'primary'


def test_provider_rejects_invalid_domain():
	try:
		ProviderConfig(name='custom', domain='not-a-url')
	except ValueError as error:
		assert 'http' in str(error).lower()
	else:
		raise AssertionError('invalid provider domain must be rejected')
