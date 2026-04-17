import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.core.files.base import ContentFile
from django.utils import timezone

from core.models import SermonAudioClip, SermonAudioClipKind, SermonSummary


OPENAI_TTS_URL = "https://api.openai.com/v1/audio/speech"
DEFAULT_TTS_MODEL = os.environ.get("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
DEFAULT_TTS_VOICE = os.environ.get("OPENAI_TTS_VOICE", "alloy")
DEFAULT_TTS_FORMAT = "mp3"


class SermonAudioGenerationError(Exception):
    pass


def _clean_text(value):
    return re.sub(r"\s+", " ", (value or "")).strip()


def _shorten(value, max_length):
    cleaned = _clean_text(value)
    if len(cleaned) <= max_length:
        return cleaned
    return cleaned[: max_length - 1].rstrip() + "…"


def _build_weekly_summary_script(sermon, summary, daily_engagements, highlight_choice):
    summary_lines = [
        _clean_text(getattr(summary, "summary_line1", "")),
        _clean_text(getattr(summary, "summary_line2", "")),
        _clean_text(getattr(summary, "summary_line3", "")),
    ]
    summary_lines = [line for line in summary_lines if line]
    routine_lines = [
        f"Day {daily.day_number}. {_shorten(daily.title, 40)}."
        for daily in daily_engagements
    ]

    parts = [
        f"이번 주 설교 요약을 들려드립니다. 설교 제목은 {_clean_text(sermon.title)}입니다.",
    ]

    if summary and summary.overview:
        parts.append(_shorten(summary.overview, 380))

    if summary_lines:
        parts.append("이번 주 한눈에 보기는 다음과 같습니다. " + " ".join(summary_lines))

    if highlight_choice and highlight_choice.text:
        parts.append(f"이번 주 가장 마음에 남는 말씀은 {_shorten(highlight_choice.text, 120)} 입니다.")

    if routine_lines:
        parts.append("5일 루틴은 다음과 같습니다. " + " ".join(routine_lines))

    return " ".join(part for part in parts if part).strip()


def _build_daily_script(sermon, summary, daily_engagement):
    outline_points = getattr(daily_engagement, "focus_outline_points", None) or []
    if not outline_points:
        stored_outline = list(getattr(summary, "outline_points", []) or [])
        outline_points = stored_outline[(daily_engagement.day_number - 1) :: 5][:2]

    daily_key_point = getattr(daily_engagement, "focus_key_point", "") or ""
    if not daily_key_point:
        key_points = [
            _clean_text(getattr(summary, "key_point1", "")),
            _clean_text(getattr(summary, "key_point2", "")),
            _clean_text(getattr(summary, "key_point3", "")),
        ]
        key_points = [point for point in key_points if point]
        if key_points:
            daily_key_point = key_points[(daily_engagement.day_number - 1) % len(key_points)]

    parts = [
        f"오늘 내용 듣기입니다. 설교 제목은 {_clean_text(sermon.title)}입니다.",
        f"오늘은 Day {daily_engagement.day_number}. {_shorten(daily_engagement.title, 80)}.",
        _shorten(daily_engagement.intro, 260),
    ]

    if outline_points:
        parts.append("오늘의 설교 흐름은 " + " ".join(_shorten(point, 90) for point in outline_points if point))

    if daily_key_point:
        parts.append(f"오늘의 핵심 메시지는 {_shorten(daily_key_point, 180)}")

    if daily_engagement.reflection_question:
        parts.append(f"오늘의 묵상 질문은 {_shorten(daily_engagement.reflection_question, 180)}")

    if daily_engagement.mission_title or daily_engagement.mission_description:
        mission_bits = [
            _shorten(daily_engagement.mission_title, 80),
            _shorten(daily_engagement.mission_description, 180),
        ]
        mission_text = " ".join(bit for bit in mission_bits if bit)
        if mission_text:
            parts.append(f"오늘의 미션은 {mission_text}")

    return " ".join(part for part in parts if part).strip()


def _request_speech_bytes(script):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise SermonAudioGenerationError("OPENAI_API_KEY is not configured.")

    body = {
        "model": DEFAULT_TTS_MODEL,
        "voice": DEFAULT_TTS_VOICE,
        "input": script,
        "format": DEFAULT_TTS_FORMAT,
        "instructions": "Warm, calm, clear Korean church reading voice for older listeners.",
    }
    request = Request(
        OPENAI_TTS_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=300) as response:
            return response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise SermonAudioGenerationError(f"OpenAI TTS request failed: {detail}") from exc
    except URLError as exc:
        raise SermonAudioGenerationError(f"OpenAI TTS request failed: {exc}") from exc


def _save_clip_file(clip, audio_bytes, filename):
    if clip.file:
        clip.file.delete(save=False)
    clip.file.save(filename, ContentFile(audio_bytes), save=False)


def generate_sermon_audio_package(sermon, challenge=None):
    challenge = challenge or sermon.weekly_challenges.order_by("-week_start", "-id").first()
    if not challenge:
        raise SermonAudioGenerationError("Weekly challenge is missing for audio generation.")

    try:
        summary = sermon.summary
    except SermonSummary.DoesNotExist:
        summary = None
    if summary is None:
        raise SermonAudioGenerationError("Sermon summary is missing for audio generation.")

    approved_days = list(challenge.daily_engagements.filter(approved=True).order_by("day_number"))
    if not approved_days:
        raise SermonAudioGenerationError("Approved daily content is missing for audio generation.")

    highlight_choice = sermon.highlight_choices.order_by("order", "id").first()
    weekly_script = _build_weekly_summary_script(sermon, summary, approved_days, highlight_choice)
    if not weekly_script:
        raise SermonAudioGenerationError("Weekly summary script could not be created.")

    weekly_clip, _ = SermonAudioClip.objects.get_or_create(
        sermon=sermon,
        kind=SermonAudioClipKind.WEEKLY_SUMMARY,
        day_number=0,
    )
    weekly_clip.title = "이번 주 요약 듣기"
    weekly_clip.script = weekly_script
    weekly_clip.voice = DEFAULT_TTS_VOICE
    weekly_clip.error = ""
    weekly_audio = _request_speech_bytes(weekly_script)
    _save_clip_file(weekly_clip, weekly_audio, f"weekly-summary-{sermon.pk}.{DEFAULT_TTS_FORMAT}")
    weekly_clip.save()

    for daily in approved_days:
        daily_script = _build_daily_script(sermon, summary, daily)
        if not daily_script:
            continue
        clip, _ = SermonAudioClip.objects.get_or_create(
            sermon=sermon,
            kind=SermonAudioClipKind.DAILY_CONTENT,
            day_number=daily.day_number,
        )
        clip.title = f"Day {daily.day_number} 오늘 내용 듣기"
        clip.script = daily_script
        clip.voice = DEFAULT_TTS_VOICE
        clip.error = ""
        clip_bytes = _request_speech_bytes(daily_script)
        _save_clip_file(
            clip,
            clip_bytes,
            f"daily-content-{sermon.pk}-day-{daily.day_number}.{DEFAULT_TTS_FORMAT}",
        )
        clip.save()

    sermon.audio_error = ""
    sermon.last_audio_generated_at = timezone.now()
    sermon.save(update_fields=["audio_error", "last_audio_generated_at", "updated_at"])

    return sermon
