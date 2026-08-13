import asyncio
from collections import deque
from datetime import datetime
from pathlib import Path

import discord

from src.core.bot import GuapishBot
from src.features.music.extractor import download_audio
from src.features.music.track import Track


FFMPEG_OPTIONS = '-vn'
IDLE_TIMEOUT = 60
INSTANT_FAIL_SECONDS = 2


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
		self._play_gen = 0
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
		self._cancel_idle()
		if self.voice_client and self.voice_client.is_connected():
			if self.voice_client.channel.id != channel.id:
				await self.voice_client.move_to(channel)
			return

		self.voice_client = await channel.connect()

	async def enqueue_and_maybe_start(self, track: Track) -> bool:
		async with self.lock:
			self.queue.append(track)
			if self.current is not None:
				return False
			await self._play_next()
			return self.current is track

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
			if skipped is None:
				return None

			upcoming = list(self.queue)
			next_track = upcoming[0] if upcoming else None
			remaining = len(upcoming)

			self._play_gen += 1
			self.current = None
			self.started_at = None
			self.elapsed_offset = 0.0
			self._cleanup_file()
			if self.voice_client and (self.voice_client.is_playing() or self.voice_client.is_paused()):
				self.voice_client.stop()

		asyncio.create_task(self._start_next())
		return skipped, next_track, remaining

	async def _start_next(self):
		async with self.lock:
			if self.current is not None:
				return
			await self._play_next()

	def clear(self) -> int:
		count = len(self.queue)
		self.queue.clear()
		return count

	async def stop(self):
		async with self.lock:
			self._play_gen += 1
			self.queue.clear()
			self.current = None
			self.started_at = None
			self.elapsed_offset = 0.0
			self._cleanup_file()
			self._cancel_idle()

			voice_client = self.voice_client
			self.voice_client = None
			if voice_client is None:
				return

			if voice_client.is_playing() or voice_client.is_paused():
				voice_client.stop()
			if voice_client.is_connected():
				await voice_client.disconnect()

	async def _play_next(self):
		self._cancel_idle()
		self._cleanup_file()

		if not self.queue:
			self.current = None
			self.started_at = None
			self.elapsed_offset = 0.0
			self._start_idle()
			return

		track = self.queue.popleft()
		self.current = track
		self.started_at = None
		self.elapsed_offset = 0.0

		try:
			path = await download_audio(track.webpage_url, self.guild_id)
			self._current_file = path
			source = discord.FFmpegOpusAudio(str(path), options=FFMPEG_OPTIONS)
		except Exception as error:
			print(f' ERR > Failed to start {track.title}: {error}')
			self.current = None
			self._cleanup_file()
			await self._notify(f'Could not play **{track.title}**. Skipping.')
			await self._play_next()
			return

		if self.voice_client is None or not self.voice_client.is_connected():
			self.current = None
			self._cleanup_file()
			return

		self._play_gen += 1
		gen = self._play_gen
		self.started_at = datetime.now()
		self.voice_client.play(source, after=lambda err, gen=gen: self._after(err, gen))
		print(f'LOG > Playing {track.title} in guild {self.guild_id}')

	def _after(self, error, gen: int):
		if error:
			print(f' ERR > Player error: {error}')

		try:
			asyncio.run_coroutine_threadsafe(self._on_track_end(gen, error), self.loop)
		except Exception as error:
			print(f' ERR > Failed to schedule next track: {error}')

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

				if failed and track is not None:
					print(f' ERR > Playback failed for {track.title}: {error}')
					await self._notify(f'Could not play **{track.title}**. Skipping.')

				await self._play_next()
		except Exception as error:
			print(f' ERR > play next: {error}')

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

	async def _notify(self, message: str):
		if self.text_channel is None:
			return

		try:
			await self.text_channel.send(message)
		except Exception as error:
			print(f' ERR > notify: {error}')

	def _cleanup_file(self):
		path = self._current_file
		self._current_file = None
		if path is None:
			return

		try:
			path.unlink(missing_ok=True)
		except OSError as error:
			print(f' ERR > Failed to delete {path}: {error}')

	def _start_idle(self):
		self._cancel_idle()
		self._idle_task = asyncio.create_task(self._idle_disconnect())

	def _cancel_idle(self):
		task = self._idle_task
		self._idle_task = None
		if task is not None and not task.done():
			task.cancel()

	async def _idle_disconnect(self):
		try:
			await asyncio.sleep(IDLE_TIMEOUT)
		except asyncio.CancelledError:
			return

		self._idle_task = None
		print(f'LOG > Idle disconnect in guild {self.guild_id}')
		await self.stop()
