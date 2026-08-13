import random
import discord
from datetime import datetime

from src.core.bot import GuapishBot
from src.core.pagination import PaginationView
from src.features.movies.helpers import build_request_pages, get_months_since, get_request_entries, render_requests_page
from src.features.movies.movie_request import MovieRequestModel
from src.features.movies.store import MoviesStore


class MoviesCog(discord.Cog):
	def __init__(self, bot: GuapishBot):
		self.bot = bot
		self.store = MoviesStore(bot.firebase, bot.app_config)

	@discord.slash_command(description='Requests movie for the month. Requests reset at the start of each month (US/Central Time).')
	async def request(self, ctx, title: str, year: int):
		user = str(ctx.author.id)
		now = datetime.now()

		roles = [str(role.id) for role in ctx.author.roles]
		if self.bot.app_config.patreon_role not in roles:
			await ctx.respond('You must be a Patreon sub to use this command! Subscribe here:\n\t*https://www.patreon.com/GUAPISH*', ephemeral=True)
			return

		if year < 1890 or year > now.year + 1:
			await ctx.respond(f'Invalid year: **{year}**. Please enter one between 1890 and now.', ephemeral=True)
			return

		existing_requests = self.store.get_user_requests(user)
		for req in existing_requests:
			date = req.date
			if date.month == now.month and date.year == now.year:
				print(f'LOG > Double Request: {ctx.author.name} already requested \"{req.title} ({req.year})\"')
				month = date.strftime('%B')
				await ctx.respond(f'You already have a request for {month}:\n\t*{req.title} ({req.year})*\nPlease wait until the next month to request again.', ephemeral=True)
				return

		print(f'LOG > Requested by {ctx.author.name} ({user}): {title} ({year})')

		request = MovieRequestModel(
			document_id=None,
			user_id=user,
			user_name=ctx.author.name,
			title=title,
			year=year,
			date=now,
		)
		self.store.add_request(request)

		await ctx.respond(f':up_arrow: Requested **{title} ({year})**!')

	@discord.slash_command(name='requests', description='View all the movies currently requested in the raffle.')
	async def all_requests(
		self,
		ctx,
		sort: str = discord.Option(description='Sort requests by date added.', choices=['asc', 'desc'], default='desc'),
	):
		requests = self.store.get_all_requests(sort)
		if not requests:
			await ctx.respond('There are no current requests in the raffle.', ephemeral=True)
			return

		pages = build_request_pages(requests)
		view = PaginationView(
			pages,
			ctx.author.id,
			render_requests_page,
			unauthorized_message='Only the person who opened this request list can change pages.',
		)
		await ctx.respond(view.render_current_page(), view=view, ephemeral=True)
		view.message = await ctx.interaction.original_response()

	@discord.slash_command(name='myrequests', description='View all your current requests, as well as their percent chance of being picked.')
	async def my_requests(
		self,
		ctx,
		factor_in_weekly_odds: str = discord.Option(default="false", choices=["true", "false"], description="More accurate calculation for chance of movies being picked week by week",
	),
	):
		uid = str(ctx.author.id)

		factor_weekly_odds = factor_in_weekly_odds.lower() == "true"

		requests = []

		if factor_weekly_odds:
			metadata = self.store.get_metadata()

			if metadata.last_id == uid:
				requests = self.store.get_all_requests()
				factor_weekly_odds = False
			else:
				requests = self.store.get_eligible_requests(metadata=metadata)
		else:
			requests = self.store.get_all_requests()
		res = ''

		total_entries = 0
		for req in requests:
			total_entries += get_request_entries(req)
		total_chance = 0
		for req in requests:
			if req.user_id == uid:
				months = get_months_since(req.date)
				entries = get_request_entries(req)
				percent = round((entries / total_entries) * 100, 1)
				total_chance += percent
				res = f'1. {req.title} ({req.year}) [{percent}%, {months} months]\n' + res

		if res == '':
			await ctx.respond('You have no current requests!', ephemeral=True)
			return

		res += f'**Combined Chance**: {round(total_chance, 1)}%'
		if factor_weekly_odds: res += ' *(Factoring in Weekly Odds)*'
		await ctx.respond(res)

	@discord.slash_command(name='roll', description='Draws a movie request from the raffle. Only usable by certain users.')
	async def roll(self, ctx):
		user = str(ctx.author.id)

		if user not in self.bot.app_config.allowed_rollers:
			await ctx.respond('You are not allowed to use this command!', ephemeral=True)
			return

		try:
			requests = self.store.get_eligible_requests()
		except Exception as error:
			print(f' ERR (roll) > {error}')
			await ctx.respond('There are no valid requests at the moment.')
			return

		if not requests:
			await ctx.respond('There are no valid requests at the moment.')
			return

		new_requests: list[MovieRequestModel] = []
		for req in requests:
			entries = get_request_entries(req)
			new_requests.extend([req] * entries)

		picked_request = random.choice(new_requests)

		print(f'LOG > Rolled {picked_request.title} ({picked_request.year})')

		self.store.mark_picked(picked_request)

		await ctx.respond(f':down_arrow: Picked {picked_request.title} (*{picked_request.year}*) by **{picked_request.user_name}**')
