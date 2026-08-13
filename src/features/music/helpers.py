from dataclasses import dataclass

import discord

from src.features.music.track import Track


QUEUE_PAGE_MAX_LINES = 10
QUEUE_PAGE_MAX_CHARS = 1600
TITLE_MAX_CHARS = 80
PROGRESS_WIDTH = 14
MUSIC_COLOR = 0x7C4DFF
ERROR_COLOR = 0xED4245


@dataclass(frozen=True, slots=True)
class QueuePage:
	body: str
	thumbnail: str | None = None
	track_count: int = 0
	total_duration: int | None = None


def format_duration(seconds: float | int | None) -> str:
	if seconds is None:
		return '--:--'

	total = max(0, int(seconds))
	hours, remainder = divmod(total, 3600)
	minutes, secs = divmod(remainder, 60)
	if hours:
		return f'{hours}:{minutes:02d}:{secs:02d}'
	return f'{minutes}:{secs:02d}'


def truncate_title(title: str) -> str:
	if len(title) <= TITLE_MAX_CHARS:
		return title
	return title[:TITLE_MAX_CHARS - 3].rstrip() + '...'


def format_progress(elapsed: float, duration: int | None, width: int = PROGRESS_WIDTH) -> str:
	played = format_duration(elapsed)
	total = format_duration(duration)
	if duration is None or duration <= 0:
		return f'`{played} / {total}`'

	ratio = min(1.0, max(0.0, elapsed / duration))
	filled = int(ratio * width)
	if filled >= width and ratio < 1:
		filled = width - 1
	bar = '▰' * filled + '▱' * (width - filled)
	return f'{bar}\n`{played} / {total}`'


def _linked_title(track: Track, *, truncate: bool = False) -> str:
	title = truncate_title(track.title) if truncate else track.title
	if track.webpage_url:
		return f'[{title}]({track.webpage_url})'
	return title


def format_track_line(index: int, track: Track) -> str:
	return (
		f'`{index}.` {_linked_title(track, truncate=True)} '
		f'`{format_duration(track.duration)}` — {track.requester_name}'
	)


def build_queue_pages(current: Track | None, upcoming: list[Track]) -> list[QueuePage]:
	lines: list[str] = []

	if current is not None:
		lines.append(
			f'**Now playing**\n{_linked_title(current, truncate=True)} '
			f'`{format_duration(current.duration)}` — {current.requester_name}'
		)
		if upcoming:
			lines.append('**Up next**')

	for index, track in enumerate(upcoming, start=1):
		lines.append(format_track_line(index, track))

	page_bodies: list[str] = []
	page_lines: list[str] = []
	page_chars = 0

	for line in lines:
		line_length = len(line) + 1
		page_is_full = len(page_lines) >= QUEUE_PAGE_MAX_LINES
		page_would_overflow = page_chars + line_length > QUEUE_PAGE_MAX_CHARS

		if page_lines and (page_is_full or page_would_overflow):
			page_bodies.append('\n'.join(page_lines))
			page_lines = []
			page_chars = 0

		page_lines.append(line)
		page_chars += line_length

	if page_lines:
		page_bodies.append('\n'.join(page_lines))

	tracks = ([current] if current is not None else []) + upcoming
	duration_values = [track.duration for track in tracks if track.duration]
	total_duration = sum(duration_values) if duration_values else None
	thumbnail = next((track.thumbnail for track in tracks if track.thumbnail), None)

	return [
		QueuePage(
			body=body,
			thumbnail=thumbnail,
			track_count=len(tracks),
			total_duration=total_duration,
		)
		for body in page_bodies
	]


def _embed_title(title: str) -> str:
	if not title:
		return 'Unknown'
	if len(title) <= 256:
		return title
	return title[:255].rstrip() + '…'


def _track_embed(author: str, track: Track, *, color: int = MUSIC_COLOR) -> discord.Embed:
	embed = discord.Embed(
		title=_embed_title(track.title),
		url=track.webpage_url or None,
		color=color,
	)
	embed.set_author(name=author)
	if track.thumbnail:
		embed.set_thumbnail(url=track.thumbnail)
	if track.uploader:
		embed.set_footer(text=track.uploader)
	return embed


def playing_embed(track: Track) -> discord.Embed:
	embed = _track_embed('Now Playing', track)
	embed.add_field(name='Duration', value=format_duration(track.duration), inline=True)
	embed.add_field(name='Requested by', value=track.requester_name, inline=True)
	return embed


def queued_embed(track: Track, position: int) -> discord.Embed:
	embed = _track_embed('Added to Queue', track)
	embed.add_field(name='Duration', value=format_duration(track.duration), inline=True)
	embed.add_field(name='Position', value=str(position), inline=True)
	embed.add_field(name='Requested by', value=track.requester_name, inline=True)
	return embed


def now_playing_embed(track: Track, elapsed: float, paused: bool) -> discord.Embed:
	author = 'Paused' if paused else 'Now Playing'
	embed = _track_embed(author, track)
	embed.description = format_progress(elapsed, track.duration)
	embed.add_field(name='Requested by', value=track.requester_name, inline=True)
	return embed


def skipped_embed(skipped: Track, next_track: Track | None, remaining: int) -> discord.Embed:
	embed = _track_embed('Skipped', skipped)
	if next_track is None:
		embed.description = 'Queue is empty.'
		return embed

	if remaining == 0:
		suffix = 'last in queue'
	else:
		suffix = f'{remaining} more in queue'
	embed.description = f'Now playing {_linked_title(next_track)} — {suffix}.'
	if next_track.thumbnail:
		embed.set_thumbnail(url=next_track.thumbnail)
	return embed


def paused_embed(track: Track) -> discord.Embed:
	return _track_embed('Paused', track)


def resumed_embed(track: Track) -> discord.Embed:
	return _track_embed('Resumed', track)


def cleared_embed(count: int, current: Track | None) -> discord.Embed:
	noun = 'track' if count == 1 else 'tracks'
	embed = discord.Embed(
		description=f'Cleared **{count}** {noun} from the queue.',
		color=MUSIC_COLOR,
	)
	embed.set_author(name='Queue Cleared')
	if current is not None and current.thumbnail:
		embed.set_thumbnail(url=current.thumbnail)
	return embed


def stopped_embed() -> discord.Embed:
	embed = discord.Embed(
		description='Stopped and left the voice channel.',
		color=MUSIC_COLOR,
	)
	embed.set_author(name='Stopped')
	return embed


def queue_embed(page: QueuePage, page_index: int, total_pages: int) -> discord.Embed:
	embed = discord.Embed(description=page.body, color=MUSIC_COLOR)
	embed.set_author(name='Queue')
	footer = f'Page {page_index + 1}/{total_pages}'
	if page.track_count:
		noun = 'track' if page.track_count == 1 else 'tracks'
		footer += f' • {page.track_count} {noun}'
	if page.total_duration:
		footer += f' • {format_duration(page.total_duration)}'
	embed.set_footer(text=footer)
	if page.thumbnail:
		embed.set_thumbnail(url=page.thumbnail)
	return embed


def render_queue_page(page: QueuePage, page_index: int, total_pages: int) -> discord.Embed:
	return queue_embed(page, page_index, total_pages)


def playback_failed_embed(track: Track) -> discord.Embed:
	embed = _track_embed('Could not play', track, color=ERROR_COLOR)
	embed.description = 'Skipping.'
	return embed


def disconnected_embed() -> discord.Embed:
	embed = discord.Embed(
		description='I am no longer connected to voice, so playback stopped.',
		color=ERROR_COLOR,
	)
	embed.set_author(name='Playback Stopped')
	return embed
