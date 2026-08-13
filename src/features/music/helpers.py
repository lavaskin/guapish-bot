from src.features.music.track import Track


QUEUE_PAGE_MAX_LINES = 10
QUEUE_PAGE_MAX_CHARS = 1600
TITLE_MAX_CHARS = 80


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


def render_queue_page(page: str, page_index: int, total_pages: int) -> str:
	header = f'**Queue ({page_index + 1}/{total_pages})**'
	return f'{header}\n{page}'


def format_track_line(index: int, track: Track) -> str:
	return f'{index}. {truncate_title(track.title)} ({format_duration(track.duration)}) — {track.requester_name}'


def build_queue_pages(current: Track | None, upcoming: list[Track]) -> list[str]:
	lines: list[str] = []

	if current is not None:
		title = truncate_title(current.title)
		lines.append(f'**Now:** {title} ({format_duration(current.duration)}) — {current.requester_name}')
		if upcoming:
			lines.append('**Up next:**')

	for index, track in enumerate(upcoming, start=1):
		lines.append(format_track_line(index, track))

	pages: list[str] = []
	page_lines: list[str] = []
	page_chars = 0

	for line in lines:
		line_length = len(line) + 1
		page_is_full = len(page_lines) >= QUEUE_PAGE_MAX_LINES
		page_would_overflow = page_chars + line_length > QUEUE_PAGE_MAX_CHARS

		if page_lines and (page_is_full or page_would_overflow):
			pages.append('\n'.join(page_lines))
			page_lines = []
			page_chars = 0

		page_lines.append(line)
		page_chars += line_length

	if page_lines:
		pages.append('\n'.join(page_lines))

	return pages
