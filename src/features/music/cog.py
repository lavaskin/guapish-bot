import asyncio

import discord

from src.core.bot import GuapishBot
from src.core.pagination import PaginationView
from src.features.music.extractor import TrackExtractError, clear_cache, extract_track
from src.features.music.helpers import build_queue_pages, format_duration, render_queue_page
from src.features.music.player import GuildPlayer


MAX_QUEUE_SIZE = 50
MAX_TRACKS_PER_USER = 10


class MusicCog(discord.Cog):
	def __init__(self, bot: GuapishBot):
		self.bot = bot
		self.players: dict[int, GuildPlayer] = {}
		clear_cache()

	def cog_unload(self):
		for player in list(self.players.values()):
			voice_client = player.shutdown_sync()
			if voice_client is not None and voice_client.is_connected():
				try:
					asyncio.get_running_loop().create_task(voice_client.disconnect(force=True))
				except RuntimeError:
					pass
		self.players.clear()

	def _get_player(self, guild_id: int) -> GuildPlayer:
		player = self.players.get(guild_id)
		if player is None:
			player = GuildPlayer(self.bot, guild_id)
			self.players[guild_id] = player
		return player

	def _user_channel(self, ctx):
		voice = getattr(ctx.author, 'voice', None)
		if voice is None:
			return None
		return voice.channel

	def _require_controller(self, ctx) -> tuple[GuildPlayer | None, str | None]:
		if ctx.guild is None:
			return None, 'This command can only be used in a server.'

		channel = self._user_channel(ctx)
		if channel is None:
			return None, 'You must be in a voice channel.'

		player = self.players.get(ctx.guild.id)
		if player is None or not player.is_connected:
			return None, 'I am not playing anything.'

		if player.voice_client.channel.id != channel.id:
			return None, 'You must be in the same voice channel as me.'

		return player, None

	def _channel_has_humans(self, channel) -> bool:
		return any(not member.bot for member in channel.members)

	async def _sync_alone_state(self, player: GuildPlayer, channel):
		if self._channel_has_humans(channel):
			player.cancel_alone_timer()
		else:
			player.start_alone_timer()

	@discord.Cog.listener()
	async def on_voice_state_update(self, member, before, after):
		guild = member.guild
		player = self.players.get(guild.id)
		if player is None:
			return

		if self.bot.user is not None and member.id == self.bot.user.id:
			if before.channel is not None and after.channel is None:
				await player.handle_disconnect()
				return
			if after.channel is not None:
				await self._sync_alone_state(player, after.channel)
			return

		if not player.is_connected or player.voice_client.channel is None:
			return

		bot_channel = player.voice_client.channel
		left_bot_channel = before.channel is not None and before.channel.id == bot_channel.id
		joined_bot_channel = after.channel is not None and after.channel.id == bot_channel.id
		if not left_bot_channel and not joined_bot_channel:
			return

		await self._sync_alone_state(player, bot_channel)

	@discord.slash_command(description='Play a YouTube song by title or URL.')
	async def play(self, ctx, query: str):
		if ctx.guild is None:
			await ctx.respond('This command can only be used in a server.', ephemeral=True)
			return

		query = query.strip()
		if not query:
			await ctx.respond('Please provide a title or YouTube URL.', ephemeral=True)
			return

		channel = self._user_channel(ctx)
		if channel is None:
			await ctx.respond('You must be in a voice channel to play music.', ephemeral=True)
			return

		player = self._get_player(ctx.guild.id)
		if player.is_connected and player.voice_client.channel.id != channel.id:
			await ctx.respond(f'I am already playing in {player.voice_client.channel.mention}.', ephemeral=True)
			return

		await ctx.defer()

		async with player.request_lock:
			# Checked before extraction so a full queue costs no network work.
			# request_lock is the only path that grows the queue, so this holds.
			queued = list(player.queue)
			if len(queued) >= MAX_QUEUE_SIZE:
				await ctx.respond(f'The queue is full ({MAX_QUEUE_SIZE} tracks). Try again once it drains.')
				return

			owned = sum(1 for queued_track in queued if queued_track.requester_id == ctx.author.id)
			if owned >= MAX_TRACKS_PER_USER:
				await ctx.respond(f'You already have {MAX_TRACKS_PER_USER} tracks queued. Wait for some to play.')
				return

			try:
				track = await extract_track(query, ctx.author.id, ctx.author.name)
			except TrackExtractError as error:
				await ctx.respond(str(error))
				return
			except Exception as error:
				print(f' ERR (play) > {error}')
				await ctx.respond('Could not find that track.')
				return

			try:
				await player.connect(channel)
			except Exception as error:
				print(f' ERR (play connect) > {error}')
				await ctx.respond('Could not join your voice channel.')
				return

			# Cover the case where the last human left while we were extracting/connecting;
			# no further voice event will fire for us in that window.
			await self._sync_alone_state(player, channel)

			player.text_channel = ctx.channel
			print(f'LOG > Queued by {ctx.author.name}: {track.title}')
			should_start, position = await player.enqueue(track)

		if should_start:
			await player.wait_for_start()

		duration = format_duration(track.duration)
		if player.current is track:
			await ctx.respond(f':notes: Playing **{track.title}** ({duration})')
		elif not should_start:
			await ctx.respond(f':notes: Queued **{track.title}** ({duration}) — position {position}')
		else:
			await ctx.respond('Could not play that track.')

	@discord.slash_command(description='Pause the current track.')
	async def pause(self, ctx):
		player, error = self._require_controller(ctx)
		if error:
			await ctx.respond(error, ephemeral=True)
			return

		if not player.pause():
			await ctx.respond('Nothing is playing.', ephemeral=True)
			return

		await ctx.respond(':pause_button: Paused.')

	@discord.slash_command(description='Resume the current track.')
	async def resume(self, ctx):
		player, error = self._require_controller(ctx)
		if error:
			await ctx.respond(error, ephemeral=True)
			return

		if not player.resume():
			await ctx.respond('Nothing is paused.', ephemeral=True)
			return

		await ctx.respond(':arrow_forward: Resumed.')

	@discord.slash_command(description='Skip the current track.')
	async def skip(self, ctx):
		player, error = self._require_controller(ctx)
		if error:
			await ctx.respond(error, ephemeral=True)
			return

		# skip() is now non-blocking, so no defer is needed; that also keeps the
		# 'Nothing is playing' path genuinely ephemeral.
		result = await player.skip()
		if result is None:
			await ctx.respond('Nothing is playing.', ephemeral=True)
			return

		skipped, next_track, remaining = result
		if next_track is None:
			await ctx.respond(f':track_next: Skipped **{skipped.title}**. Queue is empty.')
			return

		if remaining == 0:
			await ctx.respond(
				f':track_next: Skipped **{skipped.title}**.\n'
				f'Now playing **{next_track.title}** — last in queue.'
			)
			return

		await ctx.respond(
			f':track_next: Skipped **{skipped.title}**.\n'
			f'Now playing **{next_track.title}** — {remaining} more in queue.'
		)

	@discord.slash_command(description='Clear the queue. The current track keeps playing.')
	async def clear(self, ctx):
		player, error = self._require_controller(ctx)
		if error:
			await ctx.respond(error, ephemeral=True)
			return

		count = player.clear()
		if count == 0:
			await ctx.respond('The queue is already empty.', ephemeral=True)
			return

		await ctx.respond(f'Cleared **{count}** track(s) from the queue.')

	@discord.slash_command(description='Stop playback, clear the queue, and leave voice.')
	async def stop(self, ctx):
		player, error = self._require_controller(ctx)
		if error:
			await ctx.respond(error, ephemeral=True)
			return

		await player.stop()
		await ctx.respond(':stop_button: Stopped and left the voice channel.')

	@discord.slash_command(description='Show the current music queue.')
	async def queue(self, ctx):
		if ctx.guild is None:
			await ctx.respond('This command can only be used in a server.', ephemeral=True)
			return

		player = self.players.get(ctx.guild.id)
		if player is None or (player.current is None and not player.queue):
			await ctx.respond('The queue is empty.', ephemeral=True)
			return

		pages = build_queue_pages(player.current, list(player.queue))
		view = PaginationView(
			pages,
			ctx.author.id,
			render_queue_page,
			unauthorized_message='Only the person who opened this queue can change pages.',
		)
		await ctx.respond(view.render_current_page(), view=view)
		view.message = await ctx.interaction.original_response()

	@discord.slash_command(description='Show the track that is currently playing.')
	async def nowplaying(self, ctx):
		if ctx.guild is None:
			await ctx.respond('This command can only be used in a server.', ephemeral=True)
			return

		player = self.players.get(ctx.guild.id)
		if player is None or player.current is None:
			await ctx.respond('Nothing is playing.', ephemeral=True)
			return

		track = player.current
		status = 'Paused' if player.is_paused else 'Playing'
		await ctx.respond(
			f':notes: **{track.title}**\n'
			f'{format_duration(player.elapsed)} / {format_duration(track.duration)} — {status}\n'
			f'Requested by {track.requester_name}'
		)
