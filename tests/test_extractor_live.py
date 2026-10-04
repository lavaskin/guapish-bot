"""Live YouTube smoke test for the real extract -> download path.

The rest of the suite mocks yt-dlp, so it cannot notice YouTube breaking the
installed yt-dlp. That is exactly how /play ended up failing on almost every
track: a stale yt-dlp got HTTP 403 from googlevideo on the download step while
extraction kept succeeding. Run this after touching yt-dlp or when playback
starts failing:

    MUSIC_LIVE=1 ./test tests/test_extractor_live.py
"""
import os
import subprocess

import pytest

from src.features.music import extractor as ex


pytestmark = [
	pytest.mark.live,
	pytest.mark.skipif(os.environ.get('MUSIC_LIVE') != '1', reason='set MUSIC_LIVE=1 to hit YouTube'),
]

# A normal-length music video. Very short uploads (e.g. 'Me at the zoo') kept
# downloading fine while everything else 403'd, so they make a useless canary.
CANARY_URL = 'https://www.youtube.com/watch?v=dQw4w9WgXcQ'
MIN_BYTES = 1_000_000


def _assert_playable(path):
	assert path.exists()
	assert path.stat().st_size >= MIN_BYTES
	probe = subprocess.run(
		['ffmpeg', '-v', 'error', '-i', str(path), '-t', '5', '-f', 'null', '-'],
		capture_output=True,
		text=True,
	)
	assert probe.returncode == 0, probe.stderr


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch, tmp_path):
	monkeypatch.setattr(ex, 'CACHE_DIR', tmp_path)


async def test_url_downloads_a_playable_file():
	track = await ex.extract_track(CANARY_URL, 1, 'live')
	path = await ex.download_audio(track.webpage_url, 0)
	_assert_playable(path)


async def test_search_downloads_a_playable_file():
	track = await ex.extract_track('daft punk around the world', 1, 'live')
	assert ex._is_youtube_url(track.webpage_url)
	path = await ex.download_audio(track.webpage_url, 0)
	_assert_playable(path)
