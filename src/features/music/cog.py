import asyncio

import discord

from src.core.bot import GuapishBot
from src.core.pagination import PaginationView
from src.features.music.extractor import TrackExtractError, clear_cache, extract_track
from src.features.music.helpers import (
	build_queue_pages,
	cleared_embed,
	now_playing_embed,
	paused_embed,
	playing_embed,
	queued_embed,
	render_queue_page,
	resumed_embed,
	skipped_embed,
	stopped_embed,
)
from src.features.music.player import GuildPlayer
from src.features.music.pycord_patch import apply as apply_pycord_patches


MAX_QUEUE_SIZE = 50
MAX_TRACKS_PER_USER = 10


class MusicCog(discord.Cog):
	def __init__(self, bot: GuapishBot):
		self.bot = bot
		self.players: dict[int, GuildPlayer] = {}
		# Before any voice connection exists; see pycord_patch for why.
		apply_pycord_patches()
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
		if player is not None:
			# Our reference can drift from py-cord's during a reconnect; without this
			# /stop would report 'not playing' and leave the guild wedged.
			player.resync()
		if player is None or not player.is_connected:
			return None, 'I am not playing anything.'

		bot_channel = player.voice_client.channel
		if bot_channel is not None and bot_channel.id != channel.id:
			return None, 'You must be in the same voice channel as me.'

		return player, None

	def _channel_has_humans(self, channel) -> bool:
		bot_id = getattr(getattr(self.bot, 'user', None), 'id', None)
		# channel.members needs the privileged members intent to be reliable; with
		# Intents.default() anyone who was already in voice when the process started
		# is missing from it. voice_states is the documented low-level replacement,
		# but a VoiceState carries no member (it is __slots__ with no such field),
		# so ids have to be resolved through the guild and anything we cannot
		# resolve has to count as a human.
		voice_states = getattr(channel, 'voice_states', None)
		if voice_states:
			guild = getattr(channel, 'guild', None)
			for user_id in voice_states:
				if user_id == bot_id:
					continue
				member = guild.get_member(user_id) if guild is not None else None
				if member is not None and getattr(member, 'bot', False):
					continue
				return True
			return False

		return any(not member.bot for member in getattr(channel, 'members', ()))

	async def _sync_alone_state(self, player: GuildPlayer, channel):
		if self._channel_has_humans(channel):
			# Discord orphans the bot's voice session while the channel is empty, so a
			# rejoin has to verify the session is still alive rather than assume it.
			await player.note_humans_present()
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
				# Do not trust this yet: py-cord emits it while rebuilding a dropped
				# voice session too, and tearing down here detaches us from the voice
				# client it is still holding for this guild.
				player.confirm_disconnect()
				return
			if after.channel is not None:
				player.note_reconnect()
				await self._sync_alone_state(player, after.channel)
			return

		# Deliberately not gated on is_connected: during a reconnect the session is
		# briefly down, and missing a rejoin in that window would leave the alone
		# timer armed and disconnect us 60s later with people still listening.
		player.resync()
		bot_channel = getattr(player.voice_client, 'channel', None)
		if bot_channel is None:
			return

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
		player.resync()
		bot_channel = player.voice_client.channel if player.is_connected else None
		if bot_channel is not None and bot_channel.id != channel.id:
			await ctx.respond(f'I am already playing in {bot_channel.mention}.', ephemeral=True)
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

		if player.current is track:
			await ctx.respond(embed=playing_embed(track))
		elif not should_start:
			await ctx.respond(embed=queued_embed(track, position))
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

		await ctx.respond(embed=paused_embed(player.current))

	@discord.slash_command(description='Resume the current track.')
	async def resume(self, ctx):
		player, error = self._require_controller(ctx)
		if error:
			await ctx.respond(error, ephemeral=True)
			return

		if not player.resume():
			await ctx.respond('Nothing is paused.', ephemeral=True)
			return

		await ctx.respond(embed=resumed_embed(player.current))

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
		await ctx.respond(embed=skipped_embed(skipped, next_track, remaining))

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

		await ctx.respond(embed=cleared_embed(count, player.current))

	@discord.slash_command(description='Stop playback, clear the queue, and leave voice.')
	async def stop(self, ctx):
		player, error = self._require_controller(ctx)
		if error:
			await ctx.respond(error, ephemeral=True)
			return

		await player.stop()
		await ctx.respond(embed=stopped_embed())

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
		await ctx.respond(embed=view.render_current_page(), view=view)
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

		await ctx.respond(embed=now_playing_embed(player.current, player.elapsed, player.is_paused))
