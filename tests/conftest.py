"""Shared fixtures for the offline test suite.

These tests exercise the real GuildPlayer / cog logic with fake voice clients
and fake downloads, so no Discord connection, Firebase credentials, ffmpeg or
network access is required.
"""
import sys
import threading
import time
import types
from pathlib import Path

import pytest


def _install_stubs():
	"""Stub firebase/google so importing src.features does not need credentials."""
	if 'firebase_admin' not in sys.modules:
		firebase_admin = types.ModuleType('firebase_admin')
		firebase_admin.credentials = types.SimpleNamespace(Certificate=lambda path: None)
		firebase_admin.firestore = types.SimpleNamespace(
			client=lambda: None,
			CollectionReference=object,
			DocumentReference=object,
		)
		firebase_admin.initialize_app = lambda cred: None
		sys.modules['firebase_admin'] = firebase_admin

	if 'google.cloud.firestore_v1.base_query' not in sys.modules:
		base_query = types.ModuleType('google.cloud.firestore_v1.base_query')
		base_query.FieldFilter = object
		sys.modules.update({
			'google': types.ModuleType('google'),
			'google.cloud': types.ModuleType('google.cloud'),
			'google.cloud.firestore_v1': types.ModuleType('google.cloud.firestore_v1'),
			'google.cloud.firestore_v1.base_query': base_query,
		})


_install_stubs()

import discord  # noqa: E402

from src.features.music.track import Track  # noqa: E402


@pytest.fixture(autouse=True)
def fake_audio_source(monkeypatch):
	"""FFmpegOpusAudio would try to spawn ffmpeg against a fake file."""
	monkeypatch.setattr(discord, 'FFmpegOpusAudio', lambda *a, **k: object())


@pytest.fixture
def audio_file(tmp_path) -> Path:
	path = tmp_path / 'audio.mp3'
	path.write_bytes(b'\x00')
	return path


class ThreadedVoiceClient:
	"""Fake voice client that fires `after` from a background thread.

	py-cord's AudioPlayer invokes the completion callback off the event loop, and
	modelling that matters: a fake that called `after` inline hid a real skip bug.
	"""

	def __init__(self, channel_id: int = 9, members=None):
		self.connected = True
		self.disconnect_calls = 0
		self._playing = False
		self._after = None
		self.channel = types.SimpleNamespace(
			id=channel_id,
			members=members if members is not None else [],
			mention='#voice',
		)

	def is_connected(self) -> bool:
		return self.connected

	def is_playing(self) -> bool:
		return self._playing

	def is_paused(self) -> bool:
		return False

	def play(self, source, after=None):
		self._playing = True
		self._after = after

	def stop(self):
		if not self._playing:
			return
		self._playing = False
		callback, self._after = self._after, None
		if callback is not None:
			threading.Thread(
				target=lambda: (time.sleep(0.005), callback(None)),
				daemon=True,
			).start()

	def finish(self):
		"""Simulate a track ending on its own."""
		self.stop()

	async def disconnect(self, force=False):
		self.disconnect_calls += 1
		self.connected = False
		self._playing = False


@pytest.fixture
def make_track():
	def _make(title: str, *, requester_id: int = 1, duration: int | None = 180) -> Track:
		return Track(
			title=title,
			webpage_url=f'https://youtu.be/{title}',
			duration=duration,
			requester_id=requester_id,
			requester_name=f'user{requester_id}',
			query=title,
		)
	return _make


@pytest.fixture
def make_player(monkeypatch, audio_file):
	"""Build a GuildPlayer wired to a fake voice client and fake downloader."""
	from src.features.music import player as player_module

	def _make(*, download_delay: float = 0.0, fail_on=(), connected: bool = True):
		import asyncio

		async def fake_download(webpage_url, guild_id):
			if any(marker in webpage_url for marker in fail_on):
				raise RuntimeError(f'download failed: {webpage_url}')
			if download_delay:
				await asyncio.sleep(download_delay)
			return audio_file

		monkeypatch.setattr(player_module, 'download_audio', fake_download)

		player = player_module.GuildPlayer(bot=None, guild_id=1)
		player.voice_client = ThreadedVoiceClient()
		player.voice_client.connected = connected
		return player

	return _make


@pytest.fixture
def fast_timeouts(monkeypatch):
	"""Shrink the idle/alone disconnect timers so timer tests finish quickly."""
	from src.features.music import player as player_module
	monkeypatch.setattr(player_module, 'IDLE_TIMEOUT', 0.05)
	return 0.05
