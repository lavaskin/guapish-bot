import asyncio
from collections import deque
from datetime import datetime
from pathlib import Path

import discord

from src.core.bot import GuapishBot
from src.features.music.extractor import download_audio
from src.features.music.helpers import disconnected_embed, playback_failed_embed, playing_embed
from src.features.music.track import Track


FFMPEG_OPTIONS = '-vn'
IDLE_TIMEOUT = 60
INSTANT_FAIL_SECONDS = 2


def _unlink(path: Path | None):
	if path is None:
		return

	try:
		path.unlink(missing_ok=True)
	except OSError as error:
		print(f' ERR > Failed to delete {path}: {error}')


class GuildPlayer:
	def __init__(self, bot: GuapishBot, guild_id: int):
		self.bot = bot
		self.guild_id = guild_id
		self.loop = asyncio.get_running_loop()
		self.queue: deque[Track] = deque()
		self.current: Track | None = None
		self.started_at: datetime | None = None
		self.elapsed_offset = 0.0
		self.lock = asyncio.Lock()
		self.request_lock = asyncio.Lock()
		self.voice_client: discord.VoiceClient | None = None
		self.text_channel: discord.abc.Messageable | None = None
		self._idle_task: asyncio.Task | None = None
		self._alone_task: asyncio.Task | None = None
		self._driver_task: asyncio.Task | None = None
		self._play_gen = 0
		self._idle_gen = 0
		self._alone_gen = 0
		self._current_file: Path | None = None

	@property
	def is_playing(self) -> bool:
		return bool(self.voice_client and self.voice_client.is_playing())

	@property
	def is_paused(self) -> bool:
		return bool(self.voice_client and self.voice_client.is_paused())

	@property
	def is_connected(self) -> bool:
		return bool(self.voice_client and self.voice_client.is_connected())

	@property
	def elapsed(self) -> float:
		running = 0.0
		if self.started_at is not None:
			running = (datetime.now() - self.started_at).total_seconds()
		return self.elapsed_offset + running

	async def connect(self, channel: discord.VoiceChannel | discord.StageChannel):
		async with self.lock:
			self._cancel_idle()
			if self.voice_client and self.voice_client.is_connected():
				if self.voice_client.channel.id != channel.id:
					await self.voice_client.move_to(channel)
				return

			self.voice_client = await channel.connect()

	async def enqueue(self, track: Track) -> tuple[bool, int]:
		async with self.lock:
			self._cancel_idle()
			should_start = self.current is None and not self.queue
			self.queue.append(track)
			# Spawn the driver under the same lock acquisition as the append, so a
			# queued track can never be left with nothing to advance it.
			self._ensure_driver()
			return should_start, len(self.queue)

	def _ensure_driver(self, *, announce: bool = False):
		"""Start the queue driver if one is not already running. Caller must hold self.lock."""
		if self._driver_task is not None and not self._driver_task.done():
			return

		self._driver_task = asyncio.create_task(self._drive(announce=announce))

	async def _drive(self, announce: bool = False):
		try:
			await self._play_next(announce=announce)
		except Exception as error:
			print(f' ERR > driver: {error}')

	async def wait_for_start(self):
		"""Wait for the running driver to settle, without owning its lifetime."""
		task = self._driver_task
		if task is None or task.done():
			return

		# Shielded: if the caller (a slash command) is cancelled, the driver keeps going.
		await asyncio.shield(task)

	def pause(self) -> bool:
		if not self.is_playing:
			return False

		self.voice_client.pause()
		if self.started_at is not None:
			self.elapsed_offset += (datetime.now() - self.started_at).total_seconds()
			self.started_at = None
		return True

	def resume(self) -> bool:
		if not self.is_paused:
			return False

		self.voice_client.resume()
		self.started_at = datetime.now()
		return True

	async def skip(self) -> tuple[Track, Track | None, int] | None:
		async with self.lock:
			skipped = self.current
			if skipped is not None:
				self._play_gen += 1
				self.current = None
				self.started_at = None
				self.elapsed_offset = 0.0
				self._cleanup_file()
				if self.voice_client and (self.voice_client.is_playing() or self.voice_client.is_paused()):
					self.voice_client.stop()
			elif self.queue:
				# Nothing is live yet because a download from a previous skip is still
				# in flight. Skip the track that is about to play rather than reporting
				# 'nothing is playing' for the whole length of that download.
				skipped = self.queue.popleft()
				self._play_gen += 1
			else:
				return None

			# Report the successor straight off the queue rather than waiting for the
			# driver to start it. Waiting is wrong under rapid skips: every queued
			# skip would await the same driver task, which gets superseded again and
			# again and drains the whole queue before any caller reads self.current.
			next_track = self.queue[0] if self.queue else None
			remaining = max(0, len(self.queue) - 1)
			self._ensure_driver()

		return skipped, next_track, remaining

	def clear(self) -> int:
		count = len(self.queue)
		self.queue.clear()
		return count

	async def stop(self):
		async with self.lock:
			await self._cleanup(disconnect=True)

	async def handle_disconnect(self):
		async with self.lock:
			await self._cleanup(disconnect=False)

	def shutdown_sync(self) -> discord.VoiceClient | None:
		self._play_gen += 1
		self._idle_gen += 1
		self._alone_gen += 1
		self.queue.clear()
		self.current = None
		self.started_at = None
		self.elapsed_offset = 0.0
		self._cleanup_file()
		self._cancel_idle()
		self._cancel_alone()
		voice_client = self.voice_client
		self.voice_client = None
		if voice_client and (voice_client.is_playing() or voice_client.is_paused()):
			voice_client.stop()
		return voice_client

	def start_alone_timer(self):
		self._cancel_alone()
		self._alone_gen += 1
		gen = self._alone_gen
		self._alone_task = asyncio.create_task(self._alone_disconnect(gen))

	def cancel_alone_timer(self):
		self._cancel_alone()

	async def _cleanup(self, *, disconnect: bool):
		self._play_gen += 1
		self.queue.clear()
		self.current = None
		self.started_at = None
		self.elapsed_offset = 0.0
		self._cleanup_file()
		self._cancel_idle()
		self._cancel_alone()

		voice_client = self.voice_client
		self.voice_client = None
		if voice_client is None:
			return

		if voice_client.is_playing() or voice_client.is_paused():
			voice_client.stop()
		if disconnect and voice_client.is_connected():
			await voice_client.disconnect()

	async def _play_next(self, announce: bool = False):
		while True:
			async with self.lock:
				self._cancel_idle()
				if self.current is not None:
					return

				self._cleanup_file()
				if not self.queue:
					self.started_at = None
					self.elapsed_offset = 0.0
					# Only arm the idle timer if there is still a session to time out.
					if self.voice_client is not None and self.voice_client.is_connected():
						self._start_idle()
					return

				track = self.queue.popleft()
				self.current = track
				self.started_at = None
				self.elapsed_offset = 0.0
				self._play_gen += 1
				gen = self._play_gen

			try:
				path = await download_audio(track.webpage_url, self.guild_id)
			except Exception as error:
				print(f' ERR > Failed to start {track.title}: {error}')
				async with self.lock:
					superseded = gen != self._play_gen
					if self.current is track:
						self.current = None
				if superseded:
					# Someone skipped/stopped us mid-download. Re-enter the loop so any
					# remaining queue is still advanced rather than stranded.
					continue
				await self._notify(playback_failed_embed(track))
				continue

			dropped = False
			started = False
			async with self.lock:
				if gen != self._play_gen:
					_unlink(path)
					continue

				if self.voice_client is None or not self.voice_client.is_connected():
					self.current = None
					_unlink(path)
					dropped = True
				else:
					try:
						source = discord.FFmpegOpusAudio(str(path), options=FFMPEG_OPTIONS)
					except Exception as error:
						print(f' ERR > Failed to start {track.title}: {error}')
						self.current = None
						_unlink(path)
					else:
						self._current_file = path
						self.started_at = datetime.now()
						self.voice_client.play(source, after=lambda err, gen=gen: self._after(err, gen))
						print(f'LOG > Playing {track.title} in guild {self.guild_id}')
						started = True

			if dropped:
				await self._notify(disconnected_embed())
				return

			if started:
				if announce:
					await self._notify(playing_embed(track))
				return

			await self._notify(playback_failed_embed(track))

	def _after(self, error, gen: int):
		if error:
			print(f' ERR > Player error: {error}')

		coro = self._on_track_end(gen, error)
		try:
			asyncio.run_coroutine_threadsafe(coro, self.loop)
		except Exception as schedule_error:
			# Loop already gone (shutdown). Close the coroutine so it does not
			# leak as a never-awaited object.
			coro.close()
			print(f' ERR > Failed to schedule next track: {schedule_error}')

	async def _on_track_end(self, gen: int, error):
		try:
			async with self.lock:
				if gen != self._play_gen:
					return

				track = self.current
				failed = error is not None or self._looks_failed()
				self.current = None
				self.started_at = None
				self.elapsed_offset = 0.0
				self._cleanup_file()
				self._ensure_driver(announce=True)

			if failed and track is not None:
				print(f' ERR > Playback failed for {track.title}: {error}')
				await self._notify(playback_failed_embed(track))
		except Exception as error:
			print(f' ERR > track end: {error}')

	def _looks_failed(self) -> bool:
		track = self.current
		if track is None:
			return False

		played = self.elapsed
		if played >= INSTANT_FAIL_SECONDS:
			return False
		if track.duration is not None and track.duration <= INSTANT_FAIL_SECONDS:
			return False
		return True

	async def _notify(self, message: str | discord.Embed):
		if self.text_channel is None:
			return

		try:
			if isinstance(message, discord.Embed):
				await self.text_channel.send(embed=message)
			else:
				await self.text_channel.send(message)
		except Exception as error:
			print(f' ERR > notify: {error}')

	def _cleanup_file(self):
		path = self._current_file
		self._current_file = None
		_unlink(path)

	def _start_idle(self):
		self._cancel_idle()
		gen = self._idle_gen
		self._idle_task = asyncio.create_task(self._idle_disconnect(gen))

	def _cancel_idle(self):
		self._idle_gen += 1
		task = self._idle_task
		self._idle_task = None
		# A timer that cancels itself would abort mid-cleanup (e.g. during
		# voice_client.disconnect()) and leave the player half torn down.
		if task is not None and task is not asyncio.current_task() and not task.done():
			task.cancel()

	async def _idle_disconnect(self, gen: int):
		try:
			await asyncio.sleep(IDLE_TIMEOUT)
		except asyncio.CancelledError:
			return

		if gen != self._idle_gen:
			return

		print(f'LOG > Idle disconnect in guild {self.guild_id}')
		await self._stop_if_idle(gen)

	async def _stop_if_idle(self, gen: int):
		async with self.lock:
			if gen != self._idle_gen:
				return
			if self.current is not None or self.queue:
				return
			await self._cleanup(disconnect=True)

	def _cancel_alone(self):
		self._alone_gen += 1
		task = self._alone_task
		self._alone_task = None
		# See _cancel_idle: never cancel the task we are currently running on.
		if task is not None and task is not asyncio.current_task() and not task.done():
			task.cancel()

	async def _alone_disconnect(self, gen: int):
		try:
			await asyncio.sleep(IDLE_TIMEOUT)
		except asyncio.CancelledError:
			return

		if gen != self._alone_gen:
			return

		print(f'LOG > Alone disconnect in guild {self.guild_id}')
		await self.stop()
