"""Environment parsing.

A malformed DEV_MODE used to fall through to production silently, which meant a
typo pointed the bot at the real token and the real Firestore collections.
"""
import pytest

from src.core.config import DEFAULT_ALLOWED_ROLLERS, AppConfig


@pytest.mark.parametrize('value', ['True', 'true', 'TRUE', ' true ', 't', 'yes', '1', 'on'])
def test_truthy_values_select_dev(value):
	assert AppConfig._parse_bool(value, default=True) is True


@pytest.mark.parametrize('value', ['False', 'false', 'FALSE', ' false ', 'f', 'no', '0', 'off'])
def test_falsy_values_select_prod(value):
	assert AppConfig._parse_bool(value, default=False) is False


def test_missing_value_uses_default():
	assert AppConfig._parse_bool(None, default=True) is True
	assert AppConfig._parse_bool(None, default=False) is False


@pytest.mark.parametrize('value', ['Ture', 'prod', '', 'maybe', '2'])
def test_unrecognised_values_raise_instead_of_defaulting_to_prod(value):
	with pytest.raises(ValueError):
		AppConfig._parse_bool(value, default=True)


def test_default_roller_list_is_not_narrowed():
	"""Regression: the refactor dropped two of the three original rollers."""
	assert len([r for r in DEFAULT_ALLOWED_ROLLERS.split(',') if r.strip()]) == 3


def test_env_list_splits_and_strips():
	config = AppConfig.__new__(AppConfig)
	config.dev_mode = True
	config.env = lambda key, default=None: ' 1, 2 ,3 '
	assert config.env_list('ANYTHING') == ['1', '2', '3']


def test_env_list_of_empty_value_is_empty():
	config = AppConfig.__new__(AppConfig)
	config.dev_mode = True
	config.env = lambda key, default=None: ''
	assert config.env_list('ANYTHING') == []
