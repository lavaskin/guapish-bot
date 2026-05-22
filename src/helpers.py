from datetime import datetime

from src.models.app_config import AppConfig
from src.models.firebase_config import FirebaseConfig
from src.models.guapish_bot import GuapishBot

from google.cloud.firestore_v1.base_query import FieldFilter


REQUESTS_PAGE_MAX_LINES = 25
REQUESTS_PAGE_MAX_CHARS = 1600
REQUEST_TITLE_MAX_CHARS = 150


def create_bot():
	config = AppConfig()
	firebase_config = FirebaseConfig(config)
	return GuapishBot(config, firebase_config)

def get_months_since(date) -> int:
	# Get the difference in months between two dates
	now = datetime.now()
	return (now.year - date.year) * 12 + (now.month - date.month)

def get_request_entries(request) -> int:
	months = get_months_since(request['date'])
	if months >= 12:
		months += ((months - 12) * 2)
	return months + 1 # +1 for the default entry

def get_all_requests(ref, sort_direction: str = 'desc'):
	# Get all requests that are not picked
	raw_requests = ref.where(filter=FieldFilter('picked', '==', False)).stream()
	requests = [doc.to_dict() for doc in raw_requests]

	# Sort the movies by date requested and return them
	reverse = sort_direction.lower() != 'asc'
	sorted_requests = sorted(requests, key=lambda x: x['date'], reverse=reverse)
	return sorted_requests

def render_requests_page(page: str, page_index: int, total_pages: int) -> str:
	header = f'**Current Raffle Requests ({page_index + 1}/{total_pages})**'
	return f'{header}\n{page}'

def format_request_line(request: dict, index: int) -> str:
	title = request['title']
	if len(title) > REQUEST_TITLE_MAX_CHARS:
		title = title[:REQUEST_TITLE_MAX_CHARS - 3].rstrip() + '...'

	return f'{index}. {title} ({request["year"]})'

def build_request_pages(requests: list[dict]) -> list[str]:
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
