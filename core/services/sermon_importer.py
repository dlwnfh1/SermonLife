import re
from dataclasses import dataclass
from datetime import date, timedelta
from html import unescape
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from django.utils import timezone

from core.models import Sermon, SermonStatus, WeeklyChallenge
from core.services.transcript_service import TranscriptFetchError, build_watch_url, fetch_youtube_transcript


SERMON_LIST_URL = "https://crownweb2.com/"
USER_AGENT = "Mozilla/5.0 (compatible; SermonLifeBot/0.1)"
TITLE_PATTERN = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)
TEXT_CLEAN_PATTERN = re.compile(r"<[^>]+>")
DATE_PATTERN = re.compile(r"(20\d{2})[./-]\s*(\d{1,2})[./-]\s*(\d{1,2})")
SERMON_LINK_PATTERN = re.compile(
    r"""<a[^>]+href=["'](?P<href>[^"']+)["'][^>]*>(?P<label>.*?)</a>""",
    re.IGNORECASE | re.DOTALL,
)
SERMON_VIDEO_PATTERN = re.compile(r'data-video=["\'](?P<video>[^"\']+)["\']', re.IGNORECASE)
EMBED_URL_PATTERN = re.compile(
    r"https?://(?:www\.)?youtube\.com/embed/(?P<video_id>[\w-]{11})",
    re.IGNORECASE,
)
WATCH_URL_PATTERN = re.compile(
    r"https?://(?:www\.)?youtube\.com/watch\?v=(?P<video_id>[\w-]{11})",
    re.IGNORECASE,
)
SHORT_URL_PATTERN = re.compile(
    r"https?://youtu\.be/(?P<video_id>[\w-]{11})",
    re.IGNORECASE,
)
SERMON_KEYWORDS = [
    "sermon",
    "worship",
    "video",
    "\uc124\uad50",
    "\uc601\uc0c1",
]


class SermonImportError(Exception):
    pass


@dataclass
class ImportedSermonData:
    source_url: str
    title: str
    sermon_date: date
    youtube_url: str = ""
    transcript: str = ""
    bible_passage: str = ""
    preacher: str = ""
    import_error: str = ""


def fetch_html(url: str) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=20) as response:
            return response.read().decode("utf-8", errors="ignore")
    except (HTTPError, URLError) as exc:
        raise SermonImportError(f"Failed to fetch {url}: {exc}") from exc


def clean_html_text(value: str) -> str:
    text = TEXT_CLEAN_PATTERN.sub(" ", value)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def extract_latest_sermon_link(list_html: str) -> str:
    candidates = []
    for match in SERMON_LINK_PATTERN.finditer(list_html):
        href = match.group("href")
        label = clean_html_text(match.group("label"))
        haystack = f"{href} {label}".lower()
        if any(keyword in haystack for keyword in SERMON_KEYWORDS):
            candidates.append(urljoin(SERMON_LIST_URL, href))
    if not candidates:
        raise SermonImportError("Could not find a sermon link on the source page.")
    return candidates[0]


def extract_youtube_url(html: str) -> str:
    video_match = SERMON_VIDEO_PATTERN.search(html)
    if video_match:
        video_value = video_match.group("video")
        for pattern in (EMBED_URL_PATTERN, WATCH_URL_PATTERN, SHORT_URL_PATTERN):
            match = pattern.search(video_value)
            if match:
                return build_watch_url(match.group("video_id"))

    for pattern in (EMBED_URL_PATTERN, WATCH_URL_PATTERN, SHORT_URL_PATTERN):
        match = pattern.search(html)
        if match:
            return build_watch_url(match.group("video_id"))
    return ""


def parse_sermon_page(url: str, html: str) -> ImportedSermonData:
    page_title = ""
    title_match = TITLE_PATTERN.search(html)
    if title_match:
        page_title = clean_html_text(title_match.group(1))

    sermon_date = timezone.localdate()
    date_match = DATE_PATTERN.search(html)
    if date_match:
        sermon_date = date(int(date_match.group(1)), int(date_match.group(2)), int(date_match.group(3)))

    youtube_url = extract_youtube_url(html)
    imported = ImportedSermonData(
        source_url=url,
        title=page_title or f"{sermon_date} Weekly Sermon",
        sermon_date=sermon_date,
        youtube_url=youtube_url,
    )

    if youtube_url:
        try:
            imported.transcript = fetch_youtube_transcript(youtube_url, languages=["ko", "en"])
        except TranscriptFetchError as exc:
            imported.import_error = str(exc)
    else:
        imported.import_error = "YouTube video URL could not be extracted from the sermon page."

    return imported


def create_or_update_weekly_challenge(sermon: Sermon) -> WeeklyChallenge:
    week_start = sermon.sermon_date + timedelta(days=1)
    week_end = week_start + timedelta(days=6)
    challenge, _ = WeeklyChallenge.objects.update_or_create(
        sermon=sermon,
        defaults={
            "title": f"{week_start.strftime('%m/%d')} Weekly Sermon Challenge",
            "week_start": week_start,
            "week_end": week_end,
        },
    )
    return challenge


def import_latest_sermon() -> Sermon:
    list_html = fetch_html(SERMON_LIST_URL)
    sermon_url = extract_latest_sermon_link(list_html)
    sermon_html = fetch_html(sermon_url)
    imported = parse_sermon_page(sermon_url, sermon_html)

    sermon, _ = Sermon.objects.update_or_create(
        youtube_url=imported.youtube_url or imported.source_url,
        defaults={
            "title": imported.title,
            "preacher": imported.preacher,
            "sermon_date": imported.sermon_date,
            "youtube_url": imported.youtube_url or imported.source_url,
            "transcript": imported.transcript,
            "bible_passage": imported.bible_passage,
            "ai_generated": False,
            "import_error": imported.import_error,
            "ai_error": "",
            "last_imported_at": timezone.now(),
            "status": SermonStatus.DRAFT,
            "is_published": False,
        },
    )
    create_or_update_weekly_challenge(sermon)
    return sermon
