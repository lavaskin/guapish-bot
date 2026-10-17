from datetime import datetime

import discord

from src.features.movies.movie_request import MovieRequestModel


REQUESTS_PAGE_MAX_LINES = 25
REQUESTS_PAGE_MAX_CHARS = 1600
REQUEST_TITLE_MAX_CHARS = 150
ROLL_COLOR = 0xF1C40F


def get_months_since(date: datetime) -> int:
	now = datetime.now()
	return (now.year - date.year) * 12 + (now.month - date.month)

def get_request_entries(request: MovieRequestModel) -> int:
	months = get_months_since(request.date)
	if months >= 12:
		months += ((months - 12) * 2)
	return months + 1

def render_requests_page(page: str, page_index: int, total_pages: int) -> str:
	header = f'**Current Raffle Requests ({page_index + 1}/{total_pages})**'
	return f'{header}\n{page}'

def truncate_request_title(title: str) -> str:
	if len(title) <= REQUEST_TITLE_MAX_CHARS:
		return title
	return title[:REQUEST_TITLE_MAX_CHARS - 3].rstrip() + '...'

def format_request_line(request: MovieRequestModel, index: int) -> str:
	return f'{index}. {truncate_request_title(request.title)} ({request.year})'

def format_request_date(date: datetime) -> str:
	return f'{date:%B} {date.day}, {date.year}'

def format_request_age(date: datetime) -> str:
	months = get_months_since(date)
	if months <= 0:
		return 'This month'
	if months == 1:
		return '1 month ago'
	return f'{months} months ago'

def rolled_embed(
	request: MovieRequestModel,
	*,
	entries: int,
	total_entries: int,
	request_count: int,
	roller_name: str,
) -> discord.Embed:
	embed = discord.Embed(
		title=f'{truncate_request_title(request.title)} ({request.year})',
		color=ROLL_COLOR,
	)
	embed.set_author(name='🎲 Raffle Roll')
	embed.add_field(name='Requested by', value=request.user_name, inline=True)
	embed.add_field(
		name='Added',
		value=f'{format_request_date(request.date)}\n*{format_request_age(request.date)}*',
		inline=True,
	)

	chance = round((entries / total_entries) * 100, 1)
	entry_noun = 'entry' if total_entries == 1 else 'entries'
	embed.add_field(
		name='Odds',
		value=f'{chance}%\n*{entries} of {total_entries} {entry_noun}*',
		inline=True,
	)

	request_noun = 'request' if request_count == 1 else 'requests'
	embed.set_footer(text=f'Rolled by {roller_name} • {request_count} {request_noun} in the draw')
	return embed

def build_request_pages(requests: list[MovieRequestModel]) -> list[str]:
	pages: list[str] = []
	page_lines: list[str] = []
	page_chars = 0

	for index, request in enumerate(requests, start=1):
		line = format_request_line(request, index)
		line_length = len(line) + 1
		page_is_full = len(page_lines) >= REQUESTS_PAGE_MAX_LINES
		page_would_overflow = page_chars + line_length > REQUESTS_PAGE_MAX_CHARS

		if page_lines and (page_is_full or page_would_overflow):
			pages.append('\n'.join(page_lines))
			page_lines = []
			page_chars = 0

		page_lines.append(line)
		page_chars += line_length

	if page_lines:
		pages.append('\n'.join(page_lines))

	return pages
