import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import NoTranscriptFound, TranscriptsDisabled, VideoUnavailable
from yt_dlp import YoutubeDL


OPENAI_AUDIO_TRANSCRIPTIONS_URL = "https://api.openai.com/v1/audio/transcriptions"
DEFAULT_TRANSCRIPTION_MODEL = os.environ.get("OPENAI_TRANSCRIPTION_MODEL", "gpt-4o-mini-transcribe")
VTT_TIMESTAMP_PATTERN = re.compile(r"^\d{2}:\d{2}:\d{2}\.\d{3}\s+-->\s+\d{2}:\d{2}:\d{2}\.\d{3}$")


class TranscriptFetchError(Exception):
    pass


def build_watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def extract_video_id(youtube_url: str) -> str:
    parsed = urlparse(youtube_url)

    if parsed.netloc.endswith("youtu.be"):
        return parsed.path.strip("/")

    if parsed.path == "/watch":
        return parse_qs(parsed.query).get("v", [""])[0]

    if parsed.path.startswith("/embed/"):
        return parsed.path.split("/embed/")[-1].split("/")[0]

    if parsed.path.startswith("/shorts/"):
        return parsed.path.split("/shorts/")[-1].split("/")[0]

    raise TranscriptFetchError("Could not determine the YouTube video ID.")


def _normalize_transcript_lines(lines):
    cleaned = []
    for line in lines:
        value = line.strip()
        if not value:
            continue
        if value == "WEBVTT":
            continue
        if value.isdigit():
            continue
        if VTT_TIMESTAMP_PATTERN.match(value):
            continue
        if value.startswith("Kind:") or value.startswith("Language:"):
            continue
        cleaned.append(value)
    return "\n".join(cleaned)


def _fetch_transcript_from_youtube_api(video_id: str, languages):
    api = YouTubeTranscriptApi()
    fetched = api.fetch(video_id, languages=languages, preserve_formatting=False)
    transcript_text = "\n".join(item.text.strip() for item in fetched if item.text.strip())
    if not transcript_text:
        raise TranscriptFetchError(f"Transcript for video {video_id} was empty.")
    return transcript_text


def _download_subtitles_with_ytdlp(youtube_url: str, languages):
    video_id = extract_video_id(youtube_url)
    with tempfile.TemporaryDirectory() as tmpdir:
        output_template = str(Path(tmpdir) / "%(id)s.%(ext)s")
        ydl_opts = {
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": languages,
            "outtmpl": output_template,
            "quiet": True,
            "no_warnings": True,
            "extractor_args": {"youtube": {"player_client": ["web", "ios"]}},
        }
        with YoutubeDL(ydl_opts) as ydl:
            ydl.download([youtube_url])

        subtitle_files = sorted(Path(tmpdir).glob(f"{video_id}*.vtt"))
        if not subtitle_files:
            raise TranscriptFetchError(f"yt-dlp could not download subtitles for video {video_id}.")

        merged = []
        for subtitle_file in subtitle_files:
            merged.append(_normalize_transcript_lines(subtitle_file.read_text(encoding="utf-8", errors="ignore").splitlines()))
        transcript_text = "\n".join(part for part in merged if part.strip()).strip()
        if not transcript_text:
            raise TranscriptFetchError(f"yt-dlp subtitle files for video {video_id} were empty.")
        return transcript_text


def _download_audio_with_ytdlp(youtube_url: str):
    video_id = extract_video_id(youtube_url)
    tmpdir = tempfile.mkdtemp()
    output_template = str(Path(tmpdir) / "%(id)s.%(ext)s")
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": output_template,
        "quiet": True,
        "no_warnings": True,
        "extractor_args": {"youtube": {"player_client": ["ios", "mweb"]}},
    }
    with YoutubeDL(ydl_opts) as ydl:
        ydl.download([youtube_url])

    files = sorted(Path(tmpdir).glob(f"{video_id}.*"))
    audio_files = [path for path in files if path.suffix.lower() in {".m4a", ".mp3", ".webm", ".mp4", ".wav"}]
    if not audio_files:
        raise TranscriptFetchError(f"yt-dlp could not download audio for video {video_id}.")
    return audio_files[0]


def _transcribe_audio_with_openai(audio_path):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise TranscriptFetchError("OPENAI_API_KEY is not configured for audio transcription.")

    with open(audio_path, "rb") as audio_file:
        response = requests.post(
            OPENAI_AUDIO_TRANSCRIPTIONS_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            data={"model": DEFAULT_TRANSCRIPTION_MODEL},
            files={"file": (audio_path.name, audio_file, "application/octet-stream")},
            timeout=300,
        )

    if not response.ok:
        raise TranscriptFetchError(f"OpenAI transcription failed: {response.text}")

    data = response.json()
    transcript_text = data.get("text", "").strip()
    if not transcript_text:
        raise TranscriptFetchError("OpenAI transcription returned an empty transcript.")
    return transcript_text


def fetch_youtube_transcript(youtube_url: str, languages=None) -> str:
    languages = languages or ["ko", "en"]
    video_id = extract_video_id(youtube_url)
    failures = []

    try:
        return _fetch_transcript_from_youtube_api(video_id, languages)
    except (TranscriptsDisabled, NoTranscriptFound, VideoUnavailable, TranscriptFetchError) as exc:
        failures.append(f"youtube-transcript-api: {exc}")
    except Exception as exc:
        failures.append(f"youtube-transcript-api unexpected failure: {exc}")

    try:
        return _download_subtitles_with_ytdlp(youtube_url, languages)
    except Exception as exc:
        failures.append(f"yt-dlp subtitles: {exc}")

    try:
        audio_path = _download_audio_with_ytdlp(youtube_url)
        return _transcribe_audio_with_openai(audio_path)
    except Exception as exc:
        failures.append(f"audio transcription: {exc}")

    raise TranscriptFetchError(
        f"Transcript fetch failed for video {video_id}. Attempts: {' | '.join(failures)}"
    )
