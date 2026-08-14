import os

from dotenv import load_dotenv


TRUTHY = {'true', 't', 'yes', 'y', '1', 'on'}
FALSY = {'false', 'f', 'no', 'n', '0', 'off'}

# The original hardcoded roller list. Kept as the default so an unset
# ALLOWED_ROLLERS_* does not silently revoke access for two of the three.
DEFAULT_ALLOWED_ROLLERS = '148907812670406656,373724550350897154,289947773183197185'


class AppConfig:
	def __init__(self):
		self.dev_mode = True
		self.bot_token = ''
		self.allowed_rollers: list[str] = []
		self.patreon_role = ''

		self.load_env()

		print(f'LOG > Prod Mode = {str(not self.dev_mode)}')

	def env(self, key: str, default: str | None = None) -> str | None:
		suffix = 'DEV' if self.dev_mode else 'PROD'
		return os.getenv(f'{key}_{suffix}', default)

	def require_env(self, key: str) -> str:
		value = self.env(key)
		if not value:
			raise ValueError(f'{key} must be set')
		return value

	def env_list(self, key: str, default: str | None = None) -> list[str]:
		value = self.env(key, default)
		if not value:
			return []
		return [part.strip() for part in value.split(',') if part.strip()]

	def load_env(self) -> None:
		load_dotenv()

		self.dev_mode = self._parse_bool(os.getenv('DEV_MODE'), default=True, key='DEV_MODE')

		self.bot_token = self.env('BOT_TOKEN')
		self.allowed_rollers = self.env_list('ALLOWED_ROLLERS', DEFAULT_ALLOWED_ROLLERS)
		self.patreon_role = self.env('PATREON_ROLE')

	@staticmethod
	def _parse_bool(value: str | None, *, default: bool, key: str = 'DEV_MODE') -> bool:
		if value is None:
			return default

		normalized = value.strip().lower()
		if normalized in TRUTHY:
			return True
		if normalized in FALSY:
			return False

		# Never silently fall through to prod on a typo.
		raise ValueError(f'{key} must be one of {sorted(TRUTHY | FALSY)}, got: {value!r}')
