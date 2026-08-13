"""Music cards: embed copy, queue paging, and pagination embed edits."""
import types

import discord

from src.core.pagination import PaginationView
from src.features.music.helpers import (
	build_queue_pages,
	cleared_embed,
	format_progress,
	now_playing_embed,
	playing_embed,
	queued_embed,
	render_queue_page,
	skipped_embed,
)
from src.features.music.track import Track


def make_track(title: str, **overrides) -> Track:
	fields = dict(
		title=title,
		webpage_url=f'https://youtu.be/{title}',
		duration=180,
		requester_id=1,
		requester_name='jackson',
		query=title,
		thumbnail=f'https://i.ytimg.com/vi/{title}/hqdefault.jpg',
		uploader='Artist',
	)
	fields.update(overrides)
	return Track(**fields)


def test_playing_card_links_title_and_shows_artwork():
	track = make_track('Never Gonna Give You Up')
	embed = playing_embed(track)

	assert embed.author.name == 'Now Playing'
	assert embed.title == track.title
	assert embed.url == track.webpage_url
	assert embed.thumbnail.url == track.thumbnail
	assert embed.footer.text == 'Artist'
	assert embed.fields[0].value == '3:00'
	assert embed.fields[1].value == 'jackson'


def test_queued_card_includes_position():
	embed = queued_embed(make_track('Song'), 4)

	assert embed.author.name == 'Added to Queue'
	assert embed.fields[1].name == 'Position'
	assert embed.fields[1].value == '4'


def test_now_playing_card_shows_progress_and_paused_state():
	track = make_track('Song', duration=200)
	embed = now_playing_embed(track, elapsed=50, paused=True)

	assert embed.author.name == 'Paused'
	assert '`0:50 / 3:20`' in embed.description
	assert '▰' in embed.description
	assert '▱' in embed.description


def test_progress_bar_is_empty_at_start_and_full_at_end():
	assert format_progress(0, 100).startswith('▱')
	assert '▰' not in format_progress(0, 100).split('\n')[0]
	assert format_progress(100, 100).startswith('▰')
	assert '▱' not in format_progress(100, 100).split('\n')[0]


def test_skip_card_variants():
	skipped = make_track('Old')
	nxt = make_track('New', thumbnail='https://example.com/new.jpg')

	empty = skipped_embed(skipped, None, 0)
	assert empty.description == 'Queue is empty.'

	last = skipped_embed(skipped, nxt, 0)
	assert 'last in queue' in last.description
	assert '[New](https://youtu.be/New)' in last.description
	assert last.thumbnail.url == 'https://example.com/new.jpg'

	more = skipped_embed(skipped, nxt, 3)
	assert '3 more in queue' in more.description


def test_queue_pages_render_as_cards():
	current = make_track('Now')
	upcoming = [make_track(f't{i}') for i in range(1, 4)]
	pages = build_queue_pages(current, upcoming)

	assert len(pages) == 1
	assert pages[0].track_count == 4
	assert pages[0].total_duration == 720
	assert pages[0].thumbnail == current.thumbnail
	assert '**Now playing**' in pages[0].body
	assert '[Now](https://youtu.be/Now)' in pages[0].body
	assert '`1.` [t1](https://youtu.be/t1)' in pages[0].body

	embed = render_queue_page(pages[0], 0, 1)
	assert embed.author.name == 'Queue'
	assert embed.description == pages[0].body
	assert embed.footer.text == 'Page 1/1 • 4 tracks • 12:00'
	assert embed.thumbnail.url == current.thumbnail


def test_cleared_card_uses_singular_for_one_track():
	embed = cleared_embed(1, make_track('Now'))
	assert embed.description == 'Cleared **1** track from the queue.'


class FakeInteraction:
	def __init__(self, user_id: int = 1):
		self.user = types.SimpleNamespace(id=user_id)
		self.edits = []
		self.response = types.SimpleNamespace(edit_message=self._edit)

	async def _edit(self, **kwargs):
		self.edits.append(kwargs)


async def test_pagination_edits_embed_pages():
	pages = build_queue_pages(make_track('Now'), [make_track('Next')])
	view = PaginationView(pages + pages, owner_id=1, render_page=render_queue_page)
	interaction = FakeInteraction()

	await view.next_page.callback(interaction)

	assert len(interaction.edits) == 1
	edit = interaction.edits[0]
	assert edit['content'] is None
	assert isinstance(edit['embed'], discord.Embed)
	assert edit['embed'].author.name == 'Queue'


async def test_pagination_still_edits_text_pages():
	view = PaginationView(
		['one', 'two'],
		owner_id=1,
		render_page=lambda page, index, total: f'{page} ({index + 1}/{total})',
	)
	interaction = FakeInteraction()

	await view.next_page.callback(interaction)

	assert interaction.edits[0]['content'] == 'two (2/2)'
	assert 'embed' not in interaction.edits[0]
