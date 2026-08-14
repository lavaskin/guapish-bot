"""Command-surface behaviour: queue limits, alone detection, error reporting."""
import types

import pytest

from src.core import events
from src.features.music import cog as music_cog
from src.features.music import player as player_module


def make_cog():
	cog = music_cog.MusicCog.__new__(music_cog.MusicCog)
	cog.bot = types.SimpleNamespace(user=types.SimpleNamespace(id=999))
	cog.players = {}
	return cog


class FakeContext:
	def __init__(self, user_id: int = 1, channel_id: int = 9):
		self.sent = []
		self.ephemeral = []
		self.guild = types.SimpleNamespace(id=1)
		self.channel = None
		self.author = types.SimpleNamespace(
			id=user_id,
			name=f'user{user_id}',
			voice=types.SimpleNamespace(
				channel=types.SimpleNamespace(id=channel_id, mention='#voice', members=[]),
			),
		)

	async def defer(self, *a, **k):
		pass

	async def respond(self, message, **kwargs):
		self.sent.append(message)
		if kwargs.get('ephemeral'):
			self.ephemeral.append(message)


@pytest.fixture
async def cog_with_player(make_player):
	# async: GuildPlayer captures the running loop at construction time.
	cog = make_cog()
	player = make_player()
	player.voice_client = None          # not connected yet
	cog.players[1] = player
	return cog, player


async def test_queue_is_capped_globally(cog_with_player, make_track):
	cog, player = cog_with_player
	for i in range(music_cog.MAX_QUEUE_SIZE):
		player.queue.append(make_track(f't{i}', requester_id=100 + i))

	ctx = FakeContext(user_id=1)
	await music_cog.MusicCog.play.callback(cog, ctx, 'another song')

	assert any('queue is full' in m for m in ctx.sent)
	assert len(player.queue) == music_cog.MAX_QUEUE_SIZE


async def test_queue_is_capped_per_user(cog_with_player, make_track):
	cog, player = cog_with_player
	for i in range(music_cog.MAX_TRACKS_PER_USER):
		player.queue.append(make_track(f't{i}', requester_id=1))

	ctx = FakeContext(user_id=1)
	await music_cog.MusicCog.play.callback(cog, ctx, 'another song')

	assert any('already have' in m for m in ctx.sent)


async def test_per_user_cap_does_not_block_other_users(cog_with_player, make_track, monkeypatch):
	cog, player = cog_with_player
	for i in range(music_cog.MAX_TRACKS_PER_USER):
		player.queue.append(make_track(f't{i}', requester_id=1))

	reached = {}

	async def fake_extract(query, requester_id, requester_name):
		reached['yes'] = True
		raise music_cog.TrackExtractError('stubbed')

	monkeypatch.setattr(music_cog, 'extract_track', fake_extract)

	ctx = FakeContext(user_id=2)
	await music_cog.MusicCog.play.callback(cog, ctx, 'another song')

	assert reached.get('yes'), 'a different user was blocked by user 1 hitting their cap'


async def test_play_requires_a_voice_channel(cog_with_player):
	cog, _ = cog_with_player
	ctx = FakeContext()
	ctx.author.voice = None

	await music_cog.MusicCog.play.callback(cog, ctx, 'song')

	assert any('voice channel' in m for m in ctx.ephemeral)


async def test_alone_timer_arms_when_only_bots_remain():
	cog = make_cog()
	player = player_module.GuildPlayer(bot=None, guild_id=1)
	channel = types.SimpleNamespace(members=[types.SimpleNamespace(bot=True)])

	await cog._sync_alone_state(player, channel)

	assert player._alone_task is not None
	player._cancel_alone()


async def test_alone_timer_cleared_when_a_human_is_present():
	cog = make_cog()
	player = player_module.GuildPlayer(bot=None, guild_id=1)
	empty = types.SimpleNamespace(members=[types.SimpleNamespace(bot=True)])
	peopled = types.SimpleNamespace(members=[
		types.SimpleNamespace(bot=True),
		types.SimpleNamespace(bot=False),
	])

	await cog._sync_alone_state(player, empty)
	await cog._sync_alone_state(player, peopled)

	assert player._alone_task is None


async def test_humans_detected_from_voice_states_when_member_cache_is_empty():
	cog = make_cog()
	channel = types.SimpleNamespace(
		members=[],
		voice_states={42: types.SimpleNamespace(member=None)},
	)

	assert cog._channel_has_humans(channel)


async def test_voice_states_ignore_the_bot_and_other_bots():
	cog = make_cog()
	channel = types.SimpleNamespace(
		members=[],
		voice_states={
			999: types.SimpleNamespace(member=types.SimpleNamespace(bot=True)),
			100: types.SimpleNamespace(member=types.SimpleNamespace(bot=True)),
		},
	)

	assert not cog._channel_has_humans(channel)


async def test_rejoin_recovers_stalled_playback(make_player, make_track):
	import asyncio

	cog = make_cog()
	player = make_player()
	cog.players[1] = player
	channel = player.voice_client.channel
	channel.voice_states = {42: types.SimpleNamespace(member=None)}

	track = make_track('t1')
	await player.enqueue(track)
	await asyncio.sleep(0.15)
	assert player.voice_client.is_playing()

	player.voice_client._playing = False
	player.voice_client._after = None

	guild = types.SimpleNamespace(id=1)
	member = types.SimpleNamespace(id=42, guild=guild, bot=False)
	before = types.SimpleNamespace(channel=None)
	after = types.SimpleNamespace(channel=channel)

	await cog.on_voice_state_update(member, before, after)
	await asyncio.sleep(0.15)

	assert player.current is track
	assert player.voice_client.is_playing()
	assert player._alone_task is None


class ErrorContext:
	def __init__(self, already_responded: bool, followup_fails: bool = False):
		self.command = 'testcmd'
		self.response = types.SimpleNamespace(is_done=lambda: already_responded)
		self._already_responded = already_responded
		self._followup_fails = followup_fails
		self.followups = []
		self.responses = []

	@property
	def followup(self):
		return types.SimpleNamespace(send=self._send_followup)

	async def _send_followup(self, message, **kwargs):
		if self._followup_fails:
			raise RuntimeError('discord unavailable')
		self.followups.append(message)

	async def respond(self, message, **kwargs):
		if self._already_responded:
			raise RuntimeError('already responded')
		self.responses.append(message)


async def test_error_handler_uses_followup_when_already_responded():
	core = events.CoreCog.__new__(events.CoreCog)
	ctx = ErrorContext(already_responded=True)

	await core.on_application_command_error(ctx, ValueError('boom'))

	assert len(ctx.followups) == 1
	assert ctx.responses == []


async def test_error_handler_responds_when_not_yet_responded():
	core = events.CoreCog.__new__(events.CoreCog)
	ctx = ErrorContext(already_responded=False)

	await core.on_application_command_error(ctx, ValueError('boom'))

	assert len(ctx.responses) == 1
	assert ctx.followups == []


async def test_error_handler_swallows_secondary_failure():
	core = events.CoreCog.__new__(events.CoreCog)
	ctx = ErrorContext(already_responded=True, followup_fails=True)

	await core.on_application_command_error(ctx, ValueError('boom'))
