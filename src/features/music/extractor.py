import asyncio
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp
from yt_dlp.networking.impersonate import ImpersonateTarget

from src.features.music.track import Track


CACHE_DIR = Path(tempfile.gettempdir()) / 'guapish-music'
MAX_DURATION_SECONDS = 30 * 60

YDL_OPTS = {
	'format': 'bestaudio/best',
	'noplaylist': True,
	'quiet': True,
	'no_warnings': True,
	'impersonate': ImpersonateTarget('chrome'),
}


class TrackExtractError(Exception):
	pass


def _is_youtube_url(url: str) -> bool:
	host = (urlparse(url).hostname or '').lower()
	if host.startswith('www.'):
		host = host[4:]
	return host == 'youtube.com' or host == 'youtu.be' or host.endswith('.youtube.com')


def _is_youtube_info(info: dict) -> bool:
	extractor = (info.get('extractor_key') or info.get('extractor') or '').lower()
	if 'youtube' in extractor:
		return True

	url = info.get('webpage_url') or info.get('original_url') or ''
	return bool(url) and _is_youtube_url(url)


def _is_live(info: dict) -> bool:
	if info.get('is_live'):
		return True
	return info.get('live_status') in ('is_live', 'is_upcoming', 'post_live')


def _search_query(query: str) -> str:
	if query.startswith(('http://', 'https://')):
		if not _is_youtube_url(query):
			raise TrackExtractError('Only YouTube URLs are supported.')
		return query
	return f'ytsearch1:{query}'


def _validate_info(info: dict):
	if not _is_youtube_info(info):
		raise TrackExtractError('Only YouTube tracks are supported.')

	if _is_live(info):
		raise TrackExtractError('Live streams are not supported.')

	duration = info.get('duration')
	if duration is None:
		raise TrackExtractError('That track has no known duration.')
	if int(duration) > MAX_DURATION_SECONDS:
		limit = MAX_DURATION_SECONDS // 60
		raise TrackExtractError(f'Tracks longer than {limit} minutes are not supported.')


def _extract_info(query: str) -> dict:
	with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
		info = ydl.extract_info(_search_query(query), download=False)
		if not info:
			raise ValueError(f'No results for: {query}')

		if 'entries' in info:
			entries = [entry for entry in info['entries'] if entry]
			if not entries:
				raise ValueError(f'No results for: {query}')
			info = entries[0]

		_validate_info(info)
		return info


def _thumbnail(info: dict) -> str | None:
	url = info.get('thumbnail')
	if url:
		return url

	thumbs = info.get('thumbnails') or []
	for thumb in reversed(thumbs):
		thumb_url = thumb.get('url') if isinstance(thumb, dict) else None
		if thumb_url:
			return thumb_url

	video_id = info.get('id')
	if video_id:
		return f'https://i.ytimg.com/vi/{video_id}/hqdefault.jpg'
	return None


def _uploader(info: dict) -> str | None:
	return info.get('artist') or info.get('uploader') or info.get('channel') or None


def _webpage_url(info: dict, fallback: str) -> str:
	url = info.get('webpage_url') or info.get('original_url')
	if url:
		return url

	video_id = info.get('id')
	if video_id:
		return f'https://www.youtube.com/watch?v={video_id}'

	return fallback


def _downloaded_path(ydl: yt_dlp.YoutubeDL, info: dict) -> Path:
	requested = info.get('requested_downloads') or []
	if requested:
		filepath = requested[0].get('filepath')
		if filepath:
			return Path(filepath)

	path = Path(ydl.prepare_filename(info))
	if path.exists():
		return path

	raise ValueError(f'Download finished but file is missing: {path}')


def _download_audio(webpage_url: str, guild_id: int, token: str) -> Path:
	CACHE_DIR.mkdir(parents=True, exist_ok=True)
	opts = {
		**YDL_OPTS,
		# The token keeps concurrent downloads of the same video in the same guild
		# (e.g. a skip landing on a duplicate request) from writing the same file.
		'outtmpl': str(CACHE_DIR / f'{guild_id}-{token}-%(id)s.%(ext)s'),
		'noprogress': True,
		'overwrites': True,
	}
	with yt_dlp.YoutubeDL(opts) as ydl:
		info = ydl.extract_info(webpage_url, download=True)
		if not info:
			raise ValueError(f'Could not download: {webpage_url}')
		if 'entries' in info:
			entries = [entry for entry in info['entries'] if entry]
			if not entries:
				raise ValueError(f'Could not download: {webpage_url}')
			info = entries[0]
		return _downloaded_path(ydl, info)


async def extract_info(query: str) -> dict:
	loop = asyncio.get_running_loop()
	return await loop.run_in_executor(None, _extract_info, query)


async def extract_track(query: str, requester_id: int, requester_name: str) -> Track:
	info = await extract_info(query)
	duration = info.get('duration')
	return Track(
		title=info.get('title') or 'Unknown',
		webpage_url=_webpage_url(info, query),
		duration=int(duration) if duration is not None else None,
		requester_id=requester_id,
		requester_name=requester_name,
		query=query,
		thumbnail=_thumbnail(info),
		uploader=_uploader(info),
	)


async def download_audio(webpage_url: str, guild_id: int) -> Path:
	loop = asyncio.get_running_loop()
	token = uuid.uuid4().hex[:8]
	last_error: Exception | None = None
	for attempt in range(2):
		try:
			return await loop.run_in_executor(None, _download_audio, webpage_url, guild_id, token)
		except Exception as error:
			last_error = error
			print(f' ERR > Download attempt {attempt + 1} failed for {webpage_url}: {error}')

	raise last_error or ValueError(f'Could not download: {webpage_url}')


def clear_cache():
	"""Drop any audio left behind by a previous process. Safe to call at startup."""
	if not CACHE_DIR.exists():
		return

	removed = 0
	for path in CACHE_DIR.iterdir():
		if not path.is_file():
			continue
		try:
			path.unlink()
			removed += 1
		except OSError as error:
			print(f' ERR > Failed to delete stale cache file {path}: {error}')

	if removed:
		print(f'LOG > Cleared {removed} stale music cache file(s)')
