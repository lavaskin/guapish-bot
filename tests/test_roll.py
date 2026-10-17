"""/roll must never leave a request marked picked without announcing it, and announces it as a card."""
import types
from datetime import datetime

import pytest

from src.features.movies import cog as movies_cog
from src.features.movies.helpers import rolled_embed
from src.features.movies.metadata import MetadataModel
from src.features.movies.movie_request import MovieRequestModel
from src.features.movies.store import MoviesStore


ROLLER_ID = 1
PREVIOUS_WINNER = '7'


def make_request(document_id: str, user_id: str) -> MovieRequestModel:
	return MovieRequestModel(
		document_id=document_id,
		user_id=user_id,
		user_name=f'user{user_id}',
		title=f'Movie {document_id}',
		year=2000,
		date=datetime.now(),
	)


class FakeStore:
	"""In-memory stand-in for MoviesStore that records the order of calls."""

	def __init__(self, events: list[str]):
		self.events = events
		self.last_id = PREVIOUS_WINNER
		self.request = make_request('only', user_id='5')

	def get_metadata(self):
		self.events.append('read')
		return MetadataModel(document_id='meta', last_id=self.last_id)

	def get_eligible_requests(self, metadata=None):
		self.events.append('read')
		return [self.request] if not self.request.picked else []

	def mark_picked(self, request):
		self.events.append('write')
		request.picked = True
		self.last_id = request.user_id

	def unmark_picked(self, request, previous_last_id):
		self.events.append('revert')
		request.picked = False
		self.last_id = previous_last_id


class RollContext:
	def __init__(self, events: list[str], *, defer_fails: bool = False, respond_fails: bool = False):
		self.author = types.SimpleNamespace(id=ROLLER_ID, name='roller')
		self.events = events
		self.sent = []
		self._defer_fails = defer_fails
		self._respond_fails = respond_fails

	async def defer(self, *args, **kwargs):
		if self._defer_fails:
			raise RuntimeError('404 Not Found (error code: 10062): Unknown interaction')
		self.events.append('defer')

	async def respond(self, content=None, *, embed=None, **kwargs):
		if self._respond_fails:
			raise RuntimeError('discord unavailable')
		self.sent.append(embed if embed is not None else content)


def make_cog(store: FakeStore):
	cog = movies_cog.MoviesCog.__new__(movies_cog.MoviesCog)
	cog.bot = types.SimpleNamespace(app_config=types.SimpleNamespace(allowed_rollers=[str(ROLLER_ID)]))
	cog.store = store
	return cog


async def roll(cog, ctx):
	await movies_cog.MoviesCog.roll.callback(cog, ctx)


async def test_roll_acknowledges_before_touching_firestore():
	events = []
	store = FakeStore(events)

	await roll(make_cog(store), RollContext(events))

	assert events[0] == 'defer'


async def test_expired_interaction_leaves_requests_untouched():
	events = []
	store = FakeStore(events)

	with pytest.raises(RuntimeError, match='Unknown interaction'):
		await roll(make_cog(store), RollContext(events, defer_fails=True))

	assert 'write' not in events
	assert not store.request.picked
	assert store.last_id == PREVIOUS_WINNER


async def test_unannounced_pick_is_reverted():
	events = []
	store = FakeStore(events)

	with pytest.raises(RuntimeError, match='discord unavailable'):
		await roll(make_cog(store), RollContext(events, respond_fails=True))

	assert events[-2:] == ['write', 'revert']
	assert not store.request.picked
	assert store.last_id == PREVIOUS_WINNER


async def test_announced_pick_is_kept():
	events = []
	store = FakeStore(events)
	ctx = RollContext(events)

	await roll(make_cog(store), ctx)

	assert store.request.picked
	assert store.last_id == store.request.user_id
	assert 'revert' not in events
	assert len(ctx.sent) == 1
	assert ctx.sent[0].title == f'{store.request.title} ({store.request.year})'


def months_ago(months: int) -> datetime:
	now = datetime.now()
	year, month = divmod(now.year * 12 + now.month - 1 - months, 12)
	return datetime(year, month + 1, 3, 21, 30)


def test_roll_card_shows_when_the_request_was_added():
	request = make_request('abc', user_id='5')
	request.date = months_ago(14)

	embed = rolled_embed(request, entries=19, total_entries=40, request_count=6, roller_name='roller')

	assert embed.author.name == '🎲 Raffle Roll'
	assert embed.title == 'Movie abc (2000)'
	assert [field.name for field in embed.fields] == ['Requested by', 'Added', 'Odds']
	assert embed.fields[0].value == 'user5'
	assert embed.fields[1].value == f'{request.date:%B} 3, {request.date.year}\n*14 months ago*'
	assert embed.fields[2].value == '47.5%\n*19 of 40 entries*'
	assert embed.footer.text == 'Rolled by roller • 6 requests in the draw'


def test_roll_card_wording_for_a_fresh_lone_request():
	request = make_request('abc', user_id='5')

	embed = rolled_embed(request, entries=1, total_entries=1, request_count=1, roller_name='roller')

	assert embed.fields[1].value.endswith('*This month*')
	assert embed.fields[2].value == '100.0%\n*1 of 1 entry*'
	assert embed.footer.text == 'Rolled by roller • 1 request in the draw'


def test_roll_card_truncates_long_titles():
	request = make_request('abc', user_id='5')
	request.title = 'x' * 300

	embed = rolled_embed(request, entries=1, total_entries=1, request_count=1, roller_name='roller')

	assert len(embed.title) <= 256
	assert embed.title.endswith('... (2000)')


class FakeBatch:
	def __init__(self, commits: list):
		self._commits = commits
		self._ops = []

	def update(self, ref, data):
		self._ops.append((ref, data))

	def set(self, ref, data, merge=False):
		self._ops.append((ref, data))

	def commit(self):
		self._commits.append(self._ops)


def make_store():
	commits = []
	firebase = types.SimpleNamespace(
		batch=lambda: FakeBatch(commits),
		collection=lambda name: types.SimpleNamespace(document=lambda doc_id: f'{name}/{doc_id}'),
	)
	store = MoviesStore.__new__(MoviesStore)
	store.firebase = firebase
	store.requests_collection = 'requests'
	store.metadata_collection = 'metadata'
	return store, commits


def test_mark_picked_writes_request_and_last_winner_together():
	store, commits = make_store()

	store.mark_picked(make_request('abc', user_id='5'))

	assert commits == [[
		('requests/abc', {'picked': True}),
		('metadata/meta', {'last_id': '5'}),
	]]


def test_unmark_picked_restores_the_previous_winner_together():
	store, commits = make_store()

	store.unmark_picked(make_request('abc', user_id='5'), previous_last_id=None)

	assert commits == [[
		('requests/abc', {'picked': False}),
		('metadata/meta', {'last_id': None}),
	]]
