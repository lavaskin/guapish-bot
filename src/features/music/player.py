import asyncio
import time
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
# py-cord tears its own voice session down and re-joins on a voice server
# migration or a bad close code (see discord/voice/state.py::_poll_ws, which
# calls disconnect(cleanup=False) and then reconnects). Discord echoes that as a
# 'the bot left the channel' voice state, identical to a real disconnect, so we
# have to wait for the dust to settle before destroying anything. Reconnects
# normally land in well under a second.
RECONNECT_GRACE = 15
RECONNECT_POLL = 0.25
# A wedged yt-dlp must never pin `current` forever; that is what leaves the bot
# connected, silent, and answering every /play with 'queued at position N'.
DOWNLOAD_TIMEOUT = 180
# py-cord's disconnect() waits up to its connect timeout (60s) for the gateway to
# echo the disconnect, so never let it block a command for that long.
DISCONNECT_TIMEOUT = 5
# How many times to re-attempt a track across voice outages before giving up.
MAX_OUTAGE_RETRIES = 3
# When the bot is the only participant left in a channel, Discord orphans its
# voice session: it silently stops acking voice heartbeats and never sends a close
# frame. py-cord keeps reporting is_connected() == True, AudioPlayer keeps pushing
# packets into the void, is_playing() stays True and elapsed keeps ticking, so the
# bot sits there connected and completely silent. py-cord only notices via its own
# 30s receive timeout (voice/gateway.py:438) or the hardcoded 60s heartbeat
# timeout (voice/gateway.py:491), which is far too late to be usable.
#
# Voice heartbeats go out every 5s (interval is capped at 5 in voice/gateway.py:211),
# so two missed acks is a confident diagnosis.
VOICE_STALE_SECONDS = 14
VOICE_WATCHDOG_POLL = 4
# Rewind a little when resuming a rebuilt session so nothing is skipped.
REVIVE_REWIND = 2.0


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
		self._confirm_task: asyncio.Task | None = None
		self._watchdog_task: asyncio.Task | None = None
		self._play_gen = 0
		self._idle_gen = 0
		self._alone_gen = 0
		self._confirm_gen = 0
		self._watchdog_gen = 0
		self._current_file: Path | None = None
		# perf_counter, so it is directly comparable to the voice keep-alive clock.
		self._alone_since: float | None = None
		self._reviving = False
		self._resume_at = 0.0

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

	def _registered_voice_client(self) -> discord.VoiceClient | None:
		"""The voice client py-cord holds for this guild, if any.

		py-cord allows exactly one voice client per guild and only de-registers it
		at the very end of a successful disconnect. Whatever it still holds is the
		truth: if our own reference drifts away from it, channel.connect() raises
		ClientException('Already connected to a voice channel.') forever and the
		guild is wedged until the process restarts.
		"""
		guild = self.bot.get_guild(self.guild_id) if self.bot is not None else None
		return getattr(guild, 'voice_client', None)

	def resync(self) -> discord.VoiceClient | None:
		"""Re-adopt py-cord's voice client so our view can never drift from it."""
		registered = self._registered_voice_client()
		if registered is not None and registered is not self.voice_client:
			self.voice_client = registered
		return self.voice_client

	async def connect(self, channel: discord.VoiceChannel | discord.StageChannel):
		async with self.lock:
			self._cancel_idle()
			live = self.resync()
			if live is not None and live.is_connected():
				# channel can be None after py-cord processes a disconnect state.
				live_channel = getattr(live, 'channel', None)
				if live_channel is None or live_channel.id != channel.id:
					await live.move_to(channel)
				self._cancel_confirm()
				self._start_watchdog()
				return

			self.voice_client = None
			# Bounded, and it de-registers the client by hand if py-cord cannot,
			# so the next connect() is never blocked by a corpse.
			await self._force_disconnect(live)
			self.voice_client = await channel.connect()
			self._cancel_confirm()
			self._start_watchdog()

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
			voice_client = self._teardown() or self._registered_voice_client()
			self._stop_audio(voice_client)
			# Bounded; the old code awaited py-cord's disconnect (which itself waits
			# up to 60s for the gateway echo) while holding this lock, so /play,
			# /skip and /stop could all hang behind it.
			await self._force_disconnect(voice_client)

	async def handle_disconnect(self):
		async with self.lock:
			voice_client = self._teardown()
			self._stop_audio(voice_client)

	def _teardown(self) -> discord.VoiceClient | None:
		"""Drop all playback state and hand back the voice client we owned."""
		self._play_gen += 1
		self.queue.clear()
		self.current = None
		self.started_at = None
		self.elapsed_offset = 0.0
		self._resume_at = 0.0
		self._cleanup_file()
		self._cancel_idle()
		self._cancel_alone()
		self._cancel_confirm()
		self._cancel_watchdog()
		voice_client = self.voice_client
		self.voice_client = None
		return voice_client

	def _stop_audio(self, voice_client: discord.VoiceClient | None):
		if voice_client is None:
			return

		try:
			if voice_client.is_playing() or voice_client.is_paused():
				voice_client.stop()
		except Exception as error:
			print(f' ERR > stop audio: {error}')

	async def _force_disconnect(self, voice_client: discord.VoiceClient | None):
		if voice_client is None:
			return

		try:
			await asyncio.wait_for(voice_client.disconnect(force=True), timeout=DISCONNECT_TIMEOUT)
		except asyncio.TimeoutError:
			print(f' ERR > disconnect timed out in guild {self.guild_id}')
		except Exception as error:
			print(f' ERR > disconnect: {error}')

		# py-cord de-registers the client on the very last line of disconnect(), so a
		# timeout or an exception leaves it registered: every later channel.connect()
		# then raises 'Already connected to a voice channel.', and resync() would
		# happily re-adopt the corpse we just killed.
		registered = self._registered_voice_client()
		if registered is not None and registered is not voice_client:
			return

		# Idempotent: _remove_voice_client is a pop(key, None).
		cleanup = getattr(voice_client, 'cleanup', None)
		if callable(cleanup):
			try:
				cleanup()
			except Exception as error:
				print(f' ERR > voice cleanup: {error}')

	def confirm_disconnect(self):
		"""Handle a 'bot left the channel' voice state without trusting it yet.

		py-cord emits the same event while rebuilding a dropped session, so tearing
		the player down here is what used to leave the bot sitting in the channel
		with a detached voice client, refusing to play anything until /stop.
		"""
		if self._confirm_task is not None and not self._confirm_task.done():
			return

		self._confirm_gen += 1
		gen = self._confirm_gen
		self._confirm_task = asyncio.create_task(self._confirm_disconnect(gen))

	def note_reconnect(self):
		"""The bot is in a voice channel again; abandon any pending teardown."""
		self._cancel_confirm()

	def _cancel_confirm(self):
		self._confirm_gen += 1
		task = self._confirm_task
		self._confirm_task = None
		if task is not None and task is not asyncio.current_task() and not task.done():
			task.cancel()

	async def _confirm_disconnect(self, gen: int):
		try:
			await asyncio.sleep(RECONNECT_GRACE)
		except asyncio.CancelledError:
			return

		if gen != self._confirm_gen:
			return

		async with self.lock:
			if gen != self._confirm_gen:
				return

			live = self.resync()
			if live is not None and live.is_connected():
				recovered = True
			else:
				recovered = False
				voice_client = self._teardown()
				self._stop_audio(voice_client)

		if recovered:
			await self.resume_if_idle()
			return

		await self._notify(disconnected_embed())

	def _voice_last_ack(self) -> float | None:
		"""When Discord last acked a voice heartbeat, on the perf_counter clock.

		The voice websocket advances this *only* on heartbeat acks: it overrides
		received_message and never calls the base KeepAliveHandler.tick(), so unlike
		the main gateway this is an exact 'is the voice session still alive' signal.
		"""
		keep_alive = getattr(getattr(self.voice_client, 'ws', None), '_keep_alive', None)
		last_ack = getattr(keep_alive, '_last_recv', None)
		return last_ack if isinstance(last_ack, (int, float)) else None

	async def revive_voice(self, reason: str) -> bool:
		"""Rebuild a voice session that is alive on paper but dead in practice.

		Closing the websocket and letting py-cord reconnect is not enough. The
		failure mode this recovers from is a dead VoiceConnectionState._poll_ws
		runner, and that runner is the very thing that would notice the close and
		reconnect. So drop the client entirely and build a new one.

		The current track is re-queued with a resume offset so playback picks up
		roughly where it left off instead of restarting.
		"""
		async with self.lock:
			if self._reviving:
				return False

			voice_client = self.voice_client
			channel = getattr(voice_client, 'channel', None)
			if voice_client is None or channel is None:
				return False

			self._reviving = True
			self._play_gen += 1
			track = self.current
			if track is not None:
				self._resume_at = max(0.0, self.elapsed - REVIVE_REWIND)
				self.queue.appendleft(track)
			self.current = None
			self.started_at = None
			self.elapsed_offset = 0.0
			self._cleanup_file()
			self._cancel_confirm()
			self._cancel_idle()
			self.voice_client = None

		print(f' ERR > Voice session in guild {self.guild_id} is dead ({reason}); rebuilding')
		try:
			self._stop_audio(voice_client)
			await self._force_disconnect(voice_client)
			await self.connect(channel)
		except Exception as error:
			print(f' ERR > Voice rebuild failed in guild {self.guild_id}: {error}')
			return False
		finally:
			self._reviving = False

		print(f'LOG > Rebuilt voice session in guild {self.guild_id}')
		await self.resume_if_idle()
		return True

	async def revive_if_orphaned(self) -> bool:
		"""Rebuild the session if Discord went quiet while the channel was empty.

		The precise test: has Discord acked a single voice heartbeat since the moment
		the last human left? If not, the session was orphaned when the channel
		emptied out. A healthy session acks every 5s, so this barely false-positives.
		"""
		alone_since = self._alone_since
		if alone_since is None or not self.is_connected:
			return False

		last_ack = self._voice_last_ack()
		if last_ack is None or last_ack >= alone_since:
			return False

		idle = max(0.0, time.perf_counter() - last_ack)
		return await self.revive_voice(f'no heartbeat ack in {idle:.0f}s, since before the channel emptied')

	async def note_humans_present(self):
		"""Someone is listening again."""
		# Before cancel_alone_timer(), which clears the marker this depends on.
		await self.revive_if_orphaned()
		self.cancel_alone_timer()
		await self.resume_if_idle()

	def _start_watchdog(self):
		if self._watchdog_task is not None and not self._watchdog_task.done():
			return

		self._watchdog_gen += 1
		self._watchdog_task = asyncio.create_task(self._watch_voice(self._watchdog_gen))

	def _cancel_watchdog(self):
		self._watchdog_gen += 1
		task = self._watchdog_task
		self._watchdog_task = None
		if task is not None and task is not asyncio.current_task() and not task.done():
			task.cancel()

	async def _watch_voice(self, gen: int):
		"""Backstop for a dead session nobody rejoins to trigger recovery for."""
		while True:
			try:
				await asyncio.sleep(VOICE_WATCHDOG_POLL)
			except asyncio.CancelledError:
				return

			if gen != self._watchdog_gen:
				return
			# Only meaningful while we believe we are transmitting.
			if not self.is_connected or self.current is None or self.is_paused:
				continue

			last_ack = self._voice_last_ack()
			if last_ack is None:
				continue

			idle = time.perf_counter() - last_ack
			if idle <= VOICE_STALE_SECONDS:
				continue

			await self.revive_voice(f'no heartbeat ack in {idle:.0f}s')
			# Give the reconnect room before judging the new session.
			try:
				await asyncio.sleep(RECONNECT_GRACE)
			except asyncio.CancelledError:
				return

	async def resume_if_idle(self):
		"""Self-heal: connected, nothing playing, but tracks still queued."""
		async with self.lock:
			if not self.is_connected:
				return
			if self.current is None and self.queue:
				self._ensure_driver(announce=True)

	def shutdown_sync(self) -> discord.VoiceClient | None:
		voice_client = self._teardown() or self._registered_voice_client()
		self._stop_audio(voice_client)
		return voice_client

	def start_alone_timer(self):
		was_alone_since = self._alone_since
		self._cancel_alone()
		# Keep the original moment the channel emptied: it is what revive_if_orphaned
		# compares the last heartbeat ack against.
		self._alone_since = was_alone_since if was_alone_since is not None else time.perf_counter()
		self._alone_gen += 1
		gen = self._alone_gen
		self._alone_task = asyncio.create_task(self._alone_disconnect(gen))

	def cancel_alone_timer(self):
		self._cancel_alone()

	async def _play_next(self, announce: bool = False):
		outage_retries = 0
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
					if self.is_connected:
						self._start_idle()
					return

				track = self.queue.popleft()
				self.current = track
				self.started_at = None
				self.elapsed_offset = 0.0
				self._play_gen += 1
				gen = self._play_gen

			try:
				# Bounded: an unbounded download pins `current` and silently kills the
				# queue, which no amount of downstream recovery can detect.
				path = await asyncio.wait_for(
					download_audio(track.webpage_url, self.guild_id),
					timeout=DOWNLOAD_TIMEOUT,
				)
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

			outage = False
			started = False
			async with self.lock:
				if gen != self._play_gen:
					_unlink(path)
					continue

				if not self.is_connected:
					# Hold the track instead of dropping it: this is usually py-cord
					# rebuilding a dropped session, and returning here is what used to
					# strand the rest of the queue with nothing left to advance it.
					_unlink(path)
					self.current = None
					self.started_at = None
					self.elapsed_offset = 0.0
					self.queue.appendleft(track)
					outage = True
				else:
					# Set by revive_voice so a rebuilt session picks up roughly where the
					# dead one left off rather than restarting the track.
					seek, self._resume_at = self._resume_at, 0.0
					try:
						source = discord.FFmpegOpusAudio(
							str(path),
							options=FFMPEG_OPTIONS,
							before_options=f'-ss {seek:.3f}' if seek > 0 else None,
						)
					except Exception as error:
						print(f' ERR > Failed to start {track.title}: {error}')
						self.current = None
						_unlink(path)
					else:
						self._current_file = path
						self.elapsed_offset = seek
						self.started_at = datetime.now()
						self.voice_client.play(source, after=lambda err, gen=gen: self._after(err, gen))
						resumed = f' from {seek:.0f}s' if seek > 0 else ''
						print(f'LOG > Playing {track.title}{resumed} in guild {self.guild_id}')
						started = True

			if outage:
				outage_retries += 1
				if outage_retries <= MAX_OUTAGE_RETRIES and await self._await_reconnect(gen):
					continue

				async with self.lock:
					if gen != self._play_gen:
						return
					# Genuinely gone. Leave a clean, restartable player rather than a
					# non-empty queue that no driver owns.
					self.queue.clear()
					self.current = None
					self.started_at = None
					self.elapsed_offset = 0.0
					self._cleanup_file()
				await self._notify(disconnected_embed())
				return

			if started:
				if announce:
					await self._notify(playing_embed(track))
				return

			await self._notify(playback_failed_embed(track))

	async def _await_reconnect(self, gen: int) -> bool:
		"""Wait out a voice outage. False once it is clear the session is gone."""
		print(f'LOG > Voice outage in guild {self.guild_id}, waiting for reconnect')
		deadline = self.loop.time() + RECONNECT_GRACE
		while self.loop.time() < deadline:
			await asyncio.sleep(RECONNECT_POLL)
			async with self.lock:
				if gen != self._play_gen:
					return False
				if self.resync() is not None and self.is_connected:
					return True
		return False

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

			voice_client = self._teardown() or self._registered_voice_client()
			self._stop_audio(voice_client)
			await self._force_disconnect(voice_client)

	def _cancel_alone(self):
		self._alone_gen += 1
		self._alone_since = None
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

		await self.stop()
