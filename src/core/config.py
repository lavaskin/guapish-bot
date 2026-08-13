import os

from dotenv import load_dotenv


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

		env = os.getenv('DEV_MODE', 'True')
		self.dev_mode = True if env == 'True' else False

		self.bot_token = self.env('BOT_TOKEN')
		self.allowed_rollers = self.env_list('ALLOWED_ROLLERS', '148907812670406656') # Default to me! (lavaskin)
		self.patreon_role = self.env('PATREON_ROLE')
