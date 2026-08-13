"""Concurrency behaviour of GuildPlayer.

The queue is advanced by a driver task owned by the player. These tests pin the
invariant that matters: a queued track always ends up either played or reported,
never silently stranded with nothing left to advance it.
"""
import asyncio

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
