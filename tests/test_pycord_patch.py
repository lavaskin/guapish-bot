"""The py-cord 2.8.0 voice bug behind 'leave the channel, come back, no music'.

These run against the real discord.voice.VoiceClient, so if a py-cord upgrade
fixes the bug upstream, test_unpatched_pycord_still_has_the_bug fails and tells us
the patch can go.
"""
import asyncio
import types

import pytest
from discord.utils import MISSING
from discord.voice import VoiceClient

from src.features.music import pycord_patch


# Captured before anything can call pycord_patch.apply().
PRISTINE_REMOVE_SSRC = VoiceClient._remove_ssrc
PRISTINE_RECV_HOOK = VoiceClient._recv_hook


def make_voice_client() -> VoiceClient:
	"""A VoiceClient with just the state _remove_ssrc / _recv_hook touch."""
	client = VoiceClient.__new__(VoiceClient)
	client._reader = MISSING          # not recording, which is the default
	client._id_to_ssrc = {}
	client._ssrc_to_id = {}
	return client


def test_unpatched_pycord_still_has_the_bug():
	"""_remove_ssrc dereferences self._reader without the guard every other use has."""
	client = make_voice_client()
	# Anyone sharing the channel gets an SSRC via opcode 5 (speaking).
	client._id_to_ssrc[148907812670406656] = 11583

	with pytest.raises(AttributeError):
		PRISTINE_REMOVE_SSRC(client, user_id=148907812670406656)


def test_patched_remove_ssrc_drops_the_ssrc_without_raising():
	client = make_voice_client()
	client._id_to_ssrc[42] = 11583
	client._ssrc_to_id[11583] = 42

	pycord_patch._remove_ssrc(client, user_id=42)

	assert client._id_to_ssrc == {}
	assert client._ssrc_to_id == {}


def test_patched_remove_ssrc_still_notifies_a_live_reader():
	dropped = []
	client = make_voice_client()
	client._reader = types.SimpleNamespace(
		speaking_timer=types.SimpleNamespace(drop_ssrc=dropped.append),
	)
	client._id_to_ssrc[42] = 11583
	client._ssrc_to_id[11583] = 42

	pycord_patch._remove_ssrc(client, user_id=42)

	assert dropped == [11583], 'recording sessions must still get the callback'


def test_patched_remove_ssrc_ignores_unknown_users():
	client = make_voice_client()
	pycord_patch._remove_ssrc(client, user_id=99)
	assert client._id_to_ssrc == {}


async def test_recv_hook_cannot_kill_the_poll_ws_runner():
	"""_poll_ws only catches CancelledError/ConnectionClosed/TimeoutError.

	Anything else escaping the receive path kills the runner, and that runner is the
	only thing that reads the voice socket and reconnects it.
	"""
	async def exploding_hook(self, ws, msg):
		raise AttributeError("'_MissingSentinel' object has no attribute 'speaking_timer'")

	guarded = pycord_patch._guarded_recv_hook(exploding_hook)

	# Must not propagate.
	await guarded(make_voice_client(), None, {'op': 13, 'd': {'user_id': '1'}})


async def test_apply_is_idempotent_and_installs_both_patches():
	pycord_patch._APPLIED = False
	original_hook = VoiceClient._recv_hook
	try:
		pycord_patch.apply()
		patched_hook = VoiceClient._recv_hook
		pycord_patch.apply()

		assert VoiceClient._remove_ssrc is pycord_patch._remove_ssrc
		assert VoiceClient._recv_hook is patched_hook, 'apply() double-wrapped the hook'

		# The real crash path is now survivable end to end.
		client = make_voice_client()
		client._id_to_ssrc[148907812670406656] = 11583
		await VoiceClient._recv_hook(
			client, None, {'op': 13, 'd': {'user_id': '148907812670406656'}},
		)
		assert client._id_to_ssrc == {}
	finally:
		VoiceClient._recv_hook = original_hook
		VoiceClient._remove_ssrc = PRISTINE_REMOVE_SSRC
		pycord_patch._APPLIED = False


def test_pycord_source_still_lacks_the_guard():
	"""Read the installed source so an upstream fix makes this fail loudly."""
	import inspect

	import discord.voice.client as pycord_client

	source = inspect.getsource(pycord_client)
	start = source.index('def _remove_ssrc')
	body = source[start:source.index('\n    async def', start)]

	assert 'self._reader.speaking_timer' in body
	assert 'if self._reader' not in body, (
		'py-cord now guards _remove_ssrc; pycord_patch can be removed'
	)
