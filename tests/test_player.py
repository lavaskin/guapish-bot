"""Concurrency behaviour of GuildPlayer.

The queue is advanced by a driver task owned by the player. These tests pin the
invariant that matters: a queued track always ends up either played or reported,
never silently stranded with nothing left to advance it.
"""
import asyncio
import time
import types

import pytest


async def settle(seconds: float = 0.15):
	await asyncio.sleep(seconds)


async def test_queue_advances_without_caller_driving_it(make_player, make_track):
	"""enqueue() alone must be enough; the caller never drives the queue."""
	player = make_player(download_delay=0.01)
	track = make_track('t1')

	await player.enqueue(track)
	await settle()

	assert player.current is track
	assert player.voice_client.is_playing()


async def test_driver_survives_caller_cancellation(make_player, make_track):
	"""A cancelled slash command must not take playback down with it."""
	player = make_player(download_delay=0.05)
	track = make_track('t1')

	async def caller():
		await player.enqueue(track)
		await player.wait_for_start()

	task = asyncio.create_task(caller())
	await asyncio.sleep(0.01)
	task.cancel()
	with pytest.raises(asyncio.CancelledError):
		await task

	await settle(0.2)
	assert player.current is track
	assert player.voice_client.is_playing()


async def test_skip_mid_download_advances_and_strands_nothing(make_player, make_track):
	"""Skipping while the driver is downloading must not abandon the queue."""
	player = make_player(download_delay=0.08)
	first, second = make_track('t1'), make_track('t2')

	await player.enqueue(first)
	await player.enqueue(second)
	await asyncio.sleep(0.01)

	result = await player.skip()
	await settle(0.3)

	assert result is not None
	assert player.current is second
	assert list(player.queue) == []


async def test_failed_downloads_skip_through_to_playable_track(make_player, make_track):
	player = make_player(fail_on=('bad',))
	for title in ('bad1', 'bad2', 'good'):
		await player.enqueue(make_track(title))

	await settle(0.2)
	assert player.current is not None
	assert player.current.title == 'good'


class FakeTextChannel:
	def __init__(self):
		self.sent = []

	async def send(self, content=None, *, embed=None):
		self.sent.append(embed or content)


async def test_track_end_chains_to_next_track(make_player, make_track):
	player = make_player()
	first, second = make_track('t1'), make_track('t2')
	await player.enqueue(first)
	await player.enqueue(second)
	await settle(0.05)
	assert player.current is first

	# Look like a completed play rather than an instant failure.
	player.started_at = None
	player.elapsed_offset = 99
	await player._on_track_end(player._play_gen, None)
	await settle(0.05)

	assert player.current is second


async def test_natural_advance_announces_now_playing(make_player, make_track):
	player = make_player()
	channel = FakeTextChannel()
	player.text_channel = channel
	first, second = make_track('t1'), make_track('t2')
	await player.enqueue(first)
	await player.enqueue(second)
	await settle(0.05)
	assert channel.sent == []

	player.started_at = None
	player.elapsed_offset = 99
	await player._on_track_end(player._play_gen, None)
	await settle(0.05)

	assert player.current is second
	assert len(channel.sent) == 1
	assert channel.sent[0].author.name == 'Now Playing'
	assert channel.sent[0].title == 't2'


async def test_skip_does_not_announce_now_playing(make_player, make_track):
	player = make_player()
	channel = FakeTextChannel()
	player.text_channel = channel
	first, second = make_track('t1'), make_track('t2')
	await player.enqueue(first)
	await player.enqueue(second)
	await settle(0.05)

	await player.skip()
	await settle(0.15)

	assert player.current is second
	assert channel.sent == []


async def test_idle_timer_not_armed_while_disconnected(make_player):
	player = make_player(connected=False)
	await player._play_next()
	assert player._idle_task is None


async def test_idle_timer_armed_when_connected_and_empty(make_player):
	player = make_player()
	await player._play_next()
	assert player._idle_task is not None
	player._cancel_idle()


async def test_alone_timer_completes_full_teardown(make_player, fast_timeouts):
	"""The timer calls stop(); a self-cancel would abort mid-disconnect."""
	player = make_player()
	voice_client = player.voice_client

	player.start_alone_timer()
	await settle(0.3)

	assert voice_client.disconnect_calls == 1
	assert player.voice_client is None


async def test_idle_timer_disconnects_when_queue_drains(make_player, fast_timeouts):
	player = make_player()
	voice_client = player.voice_client
	await player._play_next()          # empty queue -> arms idle timer
	await settle(0.3)

	assert voice_client.disconnect_calls == 1


async def test_concurrent_enqueues_play_exactly_one_track(make_player, make_track):
	player = make_player(download_delay=0.02)
	await asyncio.gather(*[player.enqueue(make_track(f't{i}')) for i in range(5)])
	await settle(0.3)

	assert player.current is not None
	assert len(player.queue) == 4


async def test_stop_clears_everything(make_player, make_track):
	player = make_player()
	voice_client = player.voice_client
	for i in range(3):
		await player.enqueue(make_track(f't{i}'))
	await settle(0.05)

	await player.stop()

	assert player.current is None
	assert list(player.queue) == []
	assert player.voice_client is None
	assert voice_client.disconnect_calls == 1


async def test_disconnect_while_playing_does_not_resurrect_playback(make_player, make_track):
	"""Losing the voice connection must not leave a driver replaying the queue."""
	player = make_player(download_delay=0.05)
	for i in range(3):
		await player.enqueue(make_track(f't{i}'))
	await asyncio.sleep(0.01)

	player.voice_client.connected = False
	await player.handle_disconnect()
	await settle(0.3)

	assert player.current is None
	assert list(player.queue) == []


async def test_connect_force_disconnects_stale_client(make_player):
	player = make_player()
	stale = player.voice_client
	stale.connected = False

	class FakeChannel:
		async def connect(self):
			return type(stale)()

	await player.connect(FakeChannel())

	assert stale.disconnect_calls == 1
	assert player.voice_client is not stale
	assert player.voice_client.is_connected()


# --- voice outage handling -------------------------------------------------
#
# py-cord rebuilds a dropped voice session by disconnecting (without
# de-registering its voice client) and re-joining, so the bot briefly reports
# is_connected() == False and Discord emits a 'the bot left the channel' voice
# state. Neither may be treated as a real disconnect.


async def test_transient_outage_keeps_playing_the_same_track(make_player, make_track, monkeypatch):
	from src.features.music import player as player_module
	monkeypatch.setattr(player_module, 'RECONNECT_POLL', 0.02)

	player = make_player(download_delay=0.05)
	track = make_track('t1')
	player.voice_client.connected = False

	await player.enqueue(track)
	await settle(0.2)

	# The driver is holding the track, not dropping it.
	assert player.current is None
	assert list(player.queue) == [track]

	player.voice_client.connected = True
	await settle(0.3)

	assert player.current is track
	assert player.voice_client.is_playing()


async def test_transient_outage_does_not_strand_the_rest_of_the_queue(make_player, make_track, monkeypatch):
	"""The reported bug: the bot goes quiet and every later /play just queues."""
	from src.features.music import player as player_module
	monkeypatch.setattr(player_module, 'RECONNECT_POLL', 0.02)

	player = make_player(download_delay=0.05)
	for i in range(3):
		await player.enqueue(make_track(f't{i}'))
	await settle(0.2)
	assert player.current is not None

	player.voice_client.connected = False
	player.voice_client.stop()      # audio dies -> after() -> _on_track_end -> next track
	await settle(0.2)
	player.voice_client.connected = True
	await settle(0.4)

	assert player.current is not None, 'queue was stranded with no driver'
	assert player.voice_client.is_playing()


async def test_permanent_outage_leaves_a_clean_restartable_player(make_player, make_track, monkeypatch):
	from src.features.music import player as player_module
	monkeypatch.setattr(player_module, 'RECONNECT_GRACE', 0.05)
	monkeypatch.setattr(player_module, 'RECONNECT_POLL', 0.02)

	player = make_player()
	player.voice_client.connected = False
	for i in range(3):
		await player.enqueue(make_track(f't{i}'))
	await settle(0.6)

	assert player.current is None
	assert list(player.queue) == [], 'a queue nobody owns makes every later /play a no-op'


async def test_finished_track_is_never_replayed(make_player, make_track):
	"""after() fires from a thread; an enqueue landing in that window must not replay."""
	player = make_player()
	first, second = make_track('t1'), make_track('t2')
	await player.enqueue(first)
	await settle()
	assert player.current is first
	assert player.voice_client.plays == 1

	player.started_at = None
	player.elapsed_offset = 99
	player.voice_client.finish()        # after() lands ~5ms later
	await player.enqueue(second)        # ...right inside the window
	await settle(0.3)

	assert player.current is second
	assert player.voice_client.plays == 2, 'a track was played twice'


async def test_confirm_disconnect_ignores_a_recovered_session(make_player, make_track, monkeypatch):
	from src.features.music import player as player_module
	monkeypatch.setattr(player_module, 'RECONNECT_GRACE', 0.05)

	player = make_player()
	voice_client = player.voice_client
	player.bot = types.SimpleNamespace(
		get_guild=lambda guild_id: types.SimpleNamespace(voice_client=voice_client),
	)
	await player.enqueue(make_track('t1'))
	await settle()

	player.confirm_disconnect()
	await settle(0.3)

	assert player.voice_client is voice_client
	assert player.current is not None
	assert voice_client.disconnect_calls == 0


async def test_confirm_disconnect_tears_down_a_real_disconnect(make_player, make_track, monkeypatch):
	from src.features.music import player as player_module
	monkeypatch.setattr(player_module, 'RECONNECT_GRACE', 0.05)

	player = make_player()
	voice_client = player.voice_client
	await player.enqueue(make_track('t1'))
	await settle()

	voice_client.connected = False
	player.confirm_disconnect()
	await settle(0.3)

	assert player.voice_client is None
	assert player.current is None
	assert list(player.queue) == []


async def test_note_reconnect_cancels_a_pending_teardown(make_player, make_track, monkeypatch):
	from src.features.music import player as player_module
	monkeypatch.setattr(player_module, 'RECONNECT_GRACE', 0.2)

	player = make_player()
	await player.enqueue(make_track('t1'))
	await settle()

	player.confirm_disconnect()
	player.note_reconnect()
	await settle(0.4)

	assert player.voice_client is not None
	assert player.current is not None


async def test_resync_readopts_the_voice_client_pycord_holds(make_player):
	"""A detached reference is what makes every later /play fail permanently."""
	player = make_player()
	registered = player.voice_client
	player.bot = types.SimpleNamespace(
		get_guild=lambda guild_id: types.SimpleNamespace(voice_client=registered),
	)
	player.voice_client = None
	assert not player.is_connected

	assert player.resync() is registered
	assert player.is_connected


async def test_stop_disconnects_a_client_we_lost_our_reference_to(make_player):
	player = make_player()
	registered = player.voice_client
	player.bot = types.SimpleNamespace(
		get_guild=lambda guild_id: types.SimpleNamespace(voice_client=registered),
	)
	player.voice_client = None

	await player.stop()

	assert registered.disconnect_calls == 1


async def test_disconnect_is_bounded_and_forces_cleanup(make_player, monkeypatch):
	"""py-cord's disconnect() can block for its full 60s connect timeout."""
	from src.features.music import player as player_module
	monkeypatch.setattr(player_module, 'DISCONNECT_TIMEOUT', 0.05)

	player = make_player()
	voice_client = player.voice_client

	async def hang(force=False):
		await asyncio.sleep(30)

	voice_client.disconnect = hang

	start = asyncio.get_running_loop().time()
	await player.stop()
	elapsed = asyncio.get_running_loop().time() - start

	assert elapsed < 1
	assert player.voice_client is None
	# Without this the client stays registered and channel.connect() keeps raising
	# 'Already connected to a voice channel.' forever.
	assert voice_client.cleanup_calls == 1


async def test_hung_download_cannot_pin_the_current_track(make_player, make_track, monkeypatch):
	from src.features.music import player as player_module
	monkeypatch.setattr(player_module, 'DOWNLOAD_TIMEOUT', 0.05)

	async def hang(webpage_url, guild_id):
		await asyncio.sleep(30)

	player = make_player()      # installs its own fake download; override it after
	monkeypatch.setattr(player_module, 'download_audio', hang)
	await player.enqueue(make_track('t1'))
	await settle(0.3)

	assert player.current is None, 'a wedged download pinned current; /play would only queue'


# --- Discord orphaning the voice session when the channel empties -----------
#
# Real log timeline from a live repro:
#   20:54:25  op 6 heartbeat ack          <- last one ever
#   20:54:26  the only human leaves
#   20:54:30  op 3 sent, no ack
#   20:54:32  the human rejoins           <- bot connected, is_playing() True, silent
#   20:54:35  op 3 sent, no ack
#   20:54:40  op 3 sent, no ack
# py-cord only reacts at its 30s receive timeout / hardcoded 60s heartbeat
# timeout, so the bot is silent for up to a minute and /stop looks like the fix.


async def test_rejoin_revives_a_session_discord_stopped_acking(make_player, make_track):
	player = make_player()
	voice_client = player.voice_client
	await player.enqueue(make_track('t1'))
	await settle()
	assert player.current is not None

	# Last ack lands just before the channel empties, then nothing.
	voice_client.set_last_heartbeat_ack(time.perf_counter() - 1)
	player.start_alone_timer()
	await asyncio.sleep(0.02)

	channel = voice_client.channel
	await player.note_humans_present()
	await settle(0.3)

	# Closing the websocket would be useless here: the runner that would notice it
	# is the thing that died. The session has to be rebuilt outright.
	assert channel.connects == 1, 'a dead session was left in place'
	assert player.voice_client is not voice_client
	assert player.current is not None, 'the track must survive the rebuild'
	assert player.voice_client.is_playing()
	player._cancel_alone()
	player._cancel_watchdog()


async def test_rejoin_leaves_a_healthy_session_alone(make_player, make_track):
	player = make_player()
	voice_client = player.voice_client
	await player.enqueue(make_track('t1'))
	await settle()

	player.start_alone_timer()
	await asyncio.sleep(0.02)
	# Discord kept acking after the channel emptied, so the session is fine.
	voice_client.set_last_heartbeat_ack(time.perf_counter())

	await player.note_humans_present()

	assert voice_client.channel.connects == 0
	assert player.voice_client is voice_client
	assert player._alone_task is None


async def test_rejoin_does_nothing_without_heartbeat_info(make_player, make_track):
	"""Never tear down a working session just because we cannot see its clock."""
	player = make_player()
	voice_client = player.voice_client
	await player.enqueue(make_track('t1'))
	await settle()

	player.start_alone_timer()
	await player.note_humans_present()

	assert voice_client.channel.connects == 0
	assert player.voice_client is voice_client


async def test_watchdog_revives_a_dead_session_with_nobody_rejoining(make_player, make_track, monkeypatch):
	from src.features.music import player as player_module
	monkeypatch.setattr(player_module, 'VOICE_WATCHDOG_POLL', 0.02)
	monkeypatch.setattr(player_module, 'VOICE_STALE_SECONDS', 0.05)
	monkeypatch.setattr(player_module, 'RECONNECT_GRACE', 0.05)

	player = make_player()
	voice_client = player.voice_client
	voice_client.set_last_heartbeat_ack(time.perf_counter() - 10)

	await player.enqueue(make_track('t1'))
	await settle()
	player._start_watchdog()
	await settle(0.3)

	assert voice_client.channel.connects == 1, 'the watchdog never rebuilt the dead session'
	assert player.voice_client is not voice_client
	player._cancel_watchdog()


async def test_watchdog_stays_quiet_on_a_healthy_session(make_player, make_track, monkeypatch):
	from src.features.music import player as player_module
	monkeypatch.setattr(player_module, 'VOICE_WATCHDOG_POLL', 0.02)
	monkeypatch.setattr(player_module, 'VOICE_STALE_SECONDS', 5)

	player = make_player()
	voice_client = player.voice_client
	voice_client.set_last_heartbeat_ack(time.perf_counter())

	await player.enqueue(make_track('t1'))
	await settle()
	player._start_watchdog()
	await settle(0.3)

	assert voice_client.channel.connects == 0
	player._cancel_watchdog()


async def test_watchdog_ignores_a_session_with_nothing_playing(make_player, monkeypatch):
	from src.features.music import player as player_module
	monkeypatch.setattr(player_module, 'VOICE_WATCHDOG_POLL', 0.02)
	monkeypatch.setattr(player_module, 'VOICE_STALE_SECONDS', 0.05)

	player = make_player()
	voice_client = player.voice_client
	voice_client.set_last_heartbeat_ack(time.perf_counter() - 10)

	player._start_watchdog()
	await settle(0.3)

	assert voice_client.channel.connects == 0
	player._cancel_watchdog()


async def test_teardown_stops_the_watchdog(make_player):
	player = make_player()
	player._start_watchdog()
	task = player._watchdog_task

	await player.stop()
	await settle(0.05)

	assert player._watchdog_task is None
	assert task.cancelled() or task.done()
