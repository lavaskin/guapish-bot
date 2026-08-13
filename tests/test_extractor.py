"""Source restrictions and temp-file handling in the music extractor."""
import asyncio

import pytest

from src.features.music import extractor as ex


@pytest.mark.parametrize('url', [
	'https://youtube.com/watch?v=abc',
	'https://www.youtube.com/watch?v=abc',
	'https://music.youtube.com/watch?v=abc',
	'https://m.youtube.com/watch?v=abc',
	'https://youtu.be/abc',
])
def test_youtube_urls_are_allowed(url):
	assert ex._search_query(url) == url


@pytest.mark.parametrize('url', [
	'https://evil.com/x',
	'https://notyoutube.com/x',
	'https://youtube.com.evil.com/x',
	'http://vimeo.com/1234',
])
def test_non_youtube_urls_are_rejected(url):
	with pytest.raises(ex.TrackExtractError):
		ex._search_query(url)


def test_plain_text_becomes_a_search():
	assert ex._search_query('some song name') == 'ytsearch1:some song name'


def test_live_streams_are_rejected():
	with pytest.raises(ex.TrackExtractError):
		ex._validate_info({'extractor_key': 'Youtube', 'duration': 60, 'is_live': True})
	with pytest.raises(ex.TrackExtractError):
		ex._validate_info({'extractor_key': 'Youtube', 'duration': 60, 'live_status': 'is_live'})


def test_missing_and_overlong_durations_are_rejected():
	with pytest.raises(ex.TrackExtractError):
		ex._validate_info({'extractor_key': 'Youtube'})
	with pytest.raises(ex.TrackExtractError):
		ex._validate_info({
			'extractor_key': 'Youtube',
			'duration': ex.MAX_DURATION_SECONDS + 1,
		})


def test_acceptable_track_passes_validation():
	ex._validate_info({'extractor_key': 'Youtube', 'duration': 200})


def test_non_youtube_extractor_is_rejected():
	with pytest.raises(ex.TrackExtractError):
		ex._validate_info({'extractor_key': 'Vimeo', 'duration': 200})


async def test_concurrent_downloads_of_same_video_use_distinct_paths(monkeypatch):
	"""Same guild + same video must not have two writers on one path."""
	seen = set()

	class SpyYoutubeDL:
		def __init__(self, opts):
			seen.add(opts['outtmpl'])

		def __enter__(self):
			return self

		def __exit__(self, *exc):
			return False

		def extract_info(self, *a, **k):
			raise RuntimeError('stop before downloading')

	monkeypatch.setattr(ex.yt_dlp, 'YoutubeDL', SpyYoutubeDL)

	async def attempt():
		with pytest.raises(Exception):
			await ex.download_audio('https://youtu.be/same', 7)

	await asyncio.gather(attempt(), attempt(), attempt())
	assert len(seen) == 3


def test_clear_cache_removes_stale_files_only(monkeypatch, tmp_path):
	monkeypatch.setattr(ex, 'CACHE_DIR', tmp_path)
	(tmp_path / '1-aaa-vid.m4a').write_text('x')
	(tmp_path / '2-bbb-vid.webm').write_text('x')
	nested = tmp_path / 'nested'
	nested.mkdir()

	ex.clear_cache()

	assert [p.name for p in tmp_path.iterdir()] == ['nested']


def test_clear_cache_is_safe_when_directory_is_absent(monkeypatch, tmp_path):
	monkeypatch.setattr(ex, 'CACHE_DIR', tmp_path / 'does-not-exist')
	ex.clear_cache()
