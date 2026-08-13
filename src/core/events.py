import discord

from src.core.bot import GuapishBot


class CoreCog(discord.Cog):
	def __init__(self, bot: GuapishBot):
		self.bot = bot

	@discord.Cog.listener()
	async def on_application_command_error(self, ctx, error):
		print(f' ERR ({ctx.command}) > {error}')

		message = 'An error occurred while processing your command...'
		try:
			# Commands that already responded (or deferred) cannot be responded to
			# again; sending a followup instead keeps the handler from throwing.
			if ctx.response.is_done():
				await ctx.followup.send(message, ephemeral=True)
			else:
				await ctx.respond(message, ephemeral=True)
		except Exception as report_error:
			print(f' ERR > Failed to report command error: {report_error}')

	@discord.Cog.listener()
	async def on_ready(self):
		print('LOG > Bot Running\n')
