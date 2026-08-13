import json

from utils.config import AppConfig, ProviderConfig


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
	monkeypatch.setenv('PROVIDERS', json.dumps({'anyrouter': {'use_proxy': True}}))

	config = AppConfig.load_from_env()
	provider = config.get_provider('anyrouter')

	assert provider is not None
	assert provider.domain == 'https://anyrouter.top'
	assert provider.use_proxy is True


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
