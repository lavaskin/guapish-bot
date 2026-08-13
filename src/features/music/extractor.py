import asyncio
import tempfile
from pathlib import Path

import yt_dlp
from yt_dlp.networking.impersonate import ImpersonateTarget

from src.features.music.track import Track


CACHE_DIR = Path(tempfile.gettempdir()) / 'guapish-music'

YDL_OPTS = {
	'format': 'bestaudio/best',
	'noplaylist': True,
	'quiet': True,
	'no_warnings': True,
	'nocheckcertificate': True,
	'impersonate': ImpersonateTarget('chrome'),
}


def _search_query(query: str) -> str:
	if query.startswith(('http://', 'https://')):
		return query
	return f'ytsearch1:{query}'


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

		return info


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


def _download_audio(webpage_url: str, guild_id: int) -> Path:
	CACHE_DIR.mkdir(parents=True, exist_ok=True)
	opts = {
		**YDL_OPTS,
		'outtmpl': str(CACHE_DIR / f'{guild_id}-%(id)s.%(ext)s'),
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
	)


async def download_audio(webpage_url: str, guild_id: int) -> Path:
	loop = asyncio.get_running_loop()
	last_error: Exception | None = None
	for attempt in range(2):
		try:
			return await loop.run_in_executor(None, _download_audio, webpage_url, guild_id)
		except Exception as error:
			last_error = error
			print(f' ERR > Download attempt {attempt + 1} failed for {webpage_url}: {error}')

	raise last_error or ValueError(f'Could not download: {webpage_url}')
