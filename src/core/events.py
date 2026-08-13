import discord

from src.core.bot import GuapishBot


class CoreCog(discord.Cog):
	def __init__(self, bot: GuapishBot):
		self.bot = bot

	@discord.Cog.listener()
	async def on_application_command_error(self, ctx, error):
		print(f' ERR ({ctx.command}) > {error}')

		await ctx.respond('An error occurred while processing your command...', ephemeral=True)

	@discord.Cog.listener()
	async def on_ready(self):
		print('LOG > Bot Running\n')
