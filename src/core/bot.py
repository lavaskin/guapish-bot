from src.core.config import AppConfig
from src.core.firebase import Firebase

import discord


class GuapishBot(discord.Bot):
	def __init__(self, app_config: AppConfig, firebase: Firebase):
		super().__init__(intents=discord.Intents.default())
		self.app_config = app_config
		self.firebase = firebase


def create_bot() -> GuapishBot:
	config = AppConfig()
	firebase = Firebase()
	return GuapishBot(config, firebase)
