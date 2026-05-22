import os

from dotenv import load_dotenv


class AppConfig:
	def __init__(self):
		self.dev_mode = True
		self.bot_token = ''
		self.requests_collection = ''
		self.metadata_collection = ''
		self.patreon_role = ''

		# Overwrite defaults with env variables
		self.load_env()

		print(f'LOG > Prod Mode = {str(not self.dev_mode)}')

	def load_env(self) -> None:
		load_dotenv()

		env = os.getenv('DEV_MODE', 'True')
		self.dev_mode = True if env == 'True' else False

		self.bot_token = os.getenv('BOT_TOKEN_DEV') if self.dev_mode else os.getenv('BOT_TOKEN_PROD')
		self.requests_collection = os.getenv('REQUESTS_COLLECTION_DEV') if self.dev_mode else os.getenv('REQUESTS_COLLECTION_PROD')
		self.metadata_collection = os.getenv('METADATA_COLLECTION_DEV') if self.dev_mode else os.getenv('METADATA_COLLECTION_PROD')
		self.patreon_role = os.getenv('PATREON_ROLE_DEV') if self.dev_mode else os.getenv('PATREON_ROLE_PROD')
