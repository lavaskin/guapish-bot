from datetime import datetime

from src.features.movies.movie_request import MovieRequestModel


REQUESTS_PAGE_MAX_LINES = 25
REQUESTS_PAGE_MAX_CHARS = 1600
REQUEST_TITLE_MAX_CHARS = 150


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

def format_request_line(request: MovieRequestModel, index: int) -> str:
	title = request.title
	if len(title) > REQUEST_TITLE_MAX_CHARS:
		title = title[:REQUEST_TITLE_MAX_CHARS - 3].rstrip() + '...'

	return f'{index}. {title} ({request.year})'

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
