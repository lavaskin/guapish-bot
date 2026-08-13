"""Regression tests for /skip reporting.

Background: skip() used to await the queue driver and then report
`player.current`. Under rapid skips every pending skip awaited the *same*
driver task, which got superseded again and again and drained the whole queue
before any caller read `current` — so every skip reported "Queue is empty" even
with tracks still queued. skip() now reads the successor off the queue under the
lock and returns immediately.

The sweep is the test that originally caught it; plain sequential skipping did
not reproduce the bug.
"""
import asyncio

import pytest


async def start_first_track(player, timeout: float = 2.0):
	deadline = asyncio.get_running_loop().time() + timeout
	while asyncio.get_running_loop().time() < deadline:
		if player.current is not None and player.voice_client.is_playing():
			return True
		await asyncio.sleep(0.005)
	return False


async def test_sequential_skips_report_correct_successor(make_player, make_track):
	player = make_player(download_delay=0.05)
	tracks = [make_track(f't{i}') for i in range(1, 4)]
	for track in tracks:
		await player.enqueue(track)
	assert await start_first_track(player)

	assert [(s.title, n.title if n else None, r) for s, n, r in
			[await player.skip(), await player.skip(), await player.skip()]] == [
		('t1', 't2', 1),
		('t2', 't3', 0),
		('t3', None, 0),
	]


async def test_skip_with_empty_player_returns_none(make_player):
	player = make_player()
	assert await player.skip() is None


async def test_skip_with_nothing_live_drops_the_upcoming_track(make_player, make_track):
	"""Deterministic cover for the 'nothing current but queue non-empty' branch."""
	player = make_player()
	player.queue.append(make_track('t1'))
	player.queue.append(make_track('t2'))

	result = await player.skip()

	assert result is not None
	skipped, next_track, remaining = result
	assert (skipped.title, next_track.title, remaining) == ('t1', 't2', 0)


async def test_skip_mid_download_does_not_claim_nothing_is_playing(make_player, make_track):
	"""A skip clearing `current` mid-download leaves no track live.

	The driver cannot pop the next track until its in-flight download finishes,
	so `current` stays None for seconds. A further skip must act on the queue
	instead of reporting 'nothing is playing' for that whole window.
	"""
	player = make_player(download_delay=0.4)
	for i in range(1, 4):
		await player.enqueue(make_track(f't{i}'))
	assert await start_first_track(player)

	first = await player.skip()              # driver begins downloading t2
	await asyncio.sleep(0.1)
	assert player.current is not None, 'driver should have claimed t2 before downloading'

	second = await player.skip()             # clears t2 while it is still downloading
	assert player.current is None, 'expected the no-track-live window'

	third = await player.skip()
	assert third is not None, 'skip claimed nothing was playing mid-download'

	assert [first[0].title, second[0].title, third[0].title] == ['t1', 't2', 't3']


@pytest.mark.slow
@pytest.mark.parametrize('download_delay', [0.0, 0.05, 0.2])
@pytest.mark.parametrize('skip_gap', [0.0, 0.01, 0.05, 0.1, 0.2, 0.3])
async def test_rapid_skips_report_consistently(make_player, make_track, download_delay, skip_gap):
	"""Skip reporting must be identical regardless of skip/download timing."""
	player = make_player(download_delay=download_delay)
	for i in range(1, 4):
		await player.enqueue(make_track(f't{i}'))
	assert await start_first_track(player)

	results = {}

	async def do_skip(index):
		await asyncio.sleep(skip_gap * index)
		results[index] = await player.skip()

	await asyncio.gather(*[do_skip(i) for i in range(3)])
	await asyncio.sleep(download_delay * 2 + 0.2)

	observed = []
	for index in sorted(results):
		result = results[index]
		assert result is not None, (
			f'skip #{index} reported nothing playing '
			f'(dl={download_delay}, gap={skip_gap})'
		)
		skipped, next_track, remaining = result
		observed.append((skipped.title, next_track.title if next_track else None, remaining))

	assert observed == [('t1', 't2', 1), ('t2', 't3', 0), ('t3', None, 0)], (
		f'inconsistent skip reporting at dl={download_delay}, gap={skip_gap}'
	)

	assert not (player.current is None and player.queue), 'queue stranded with no driver'
