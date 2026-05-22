from src.models.app_config import AppConfig
from src.models.firebase_config import FirebaseConfig

import discord


class GuapishBot(discord.Bot):
	def __init__(self, app_config: AppConfig, firebase_config: FirebaseConfig):
		super().__init__(intents=discord.Intents.default())
		self.app_config = app_config
		self.firebase_config = firebase_config
