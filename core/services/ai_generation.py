import json
import os
from dataclasses import dataclass
from typing import Dict, List
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.db import transaction
from django.utils import timezone

from core.models import (
    DailyEngagement,
    Sermon,
    SermonHighlightChoice,
    SermonStatus,
    SermonSummary,
)
from core.services.sermon_importer import create_or_update_weekly_challenge


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.4")


SERMON_CONTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "bible_passage": {"type": "string"},
        "overview": {"type": "string"},
        "outline_points": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 8,
            "maxItems": 10,
        },
        "summary_3lines": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 3,
            "maxItems": 3,
        },
        "key_points": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 3,
            "maxItems": 3,
        },
        "highlight_quotes": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 3,
            "maxItems": 3,
        },
        "daily_engagements": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "day_number": {"type": "integer", "minimum": 1, "maximum": 5},
                    "title": {"type": "string"},
                    "intro": {"type": "string"},
                    "quiz": {
                        "type": "object",
                        "properties": {
                            "question": {"type": "string"},
                            "choices": {
                                "type": "array",
                                "items": {"type": "string"},
                                "minItems": 4,
                                "maxItems": 4,
                            },
                            "answer": {"type": "string"},
                            "explanation": {"type": "string"},
                        },
                        "required": ["question", "choices", "answer", "explanation"],
                        "additionalProperties": False,
                    },
                    "reflection_question": {"type": "string"},
                    "mission": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "description": {"type": "string"},
                        },
                        "required": ["title", "description"],
                        "additionalProperties": False,
                    },
                },
                "required": [
                    "day_number",
                    "title",
                    "intro",
                    "quiz",
                    "reflection_question",
                    "mission",
                ],
                "additionalProperties": False,
            },
            "minItems": 5,
            "maxItems": 5,
        },
    },
    "required": [
        "title",
        "bible_passage",
        "overview",
        "outline_points",
        "summary_3lines",
        "key_points",
        "highlight_quotes",
        "daily_engagements",
    ],
    "additionalProperties": False,
}


class AIContentGenerationError(Exception):
    pass


@dataclass
class GeneratedSermonContent:
    title: str
    bible_passage: str
    overview: str
    outline_points: List[str]
    summary_3lines: List[str]
    key_points: List[str]
    highlight_quotes: List[str]
    daily_engagements: List[Dict]


def build_user_prompt(sermon: Sermon) -> str:
    source_text = sermon.transcript.strip()
    if not source_text:
        raise AIContentGenerationError("Transcript is empty. AI generation requires a transcript.")

    return (
        "\uc544\ub798 \uc124\uad50 \ub0b4\uc6a9\uc744 \ubc14\ud0d5\uc73c\ub85c "
        "\uad50\ud68c \uc571\uc5d0\uc11c \uc0ac\uc6a9\ud560 JSON \ucf58\ud150\uce20\ub97c \uc0dd\uc131\ud558\uc138\uc694.\n\n"
        "\ubc18\ub4dc\uc2dc \uc9c0\ud0ac \uc870\uac74:\n"
        "1. \ud55c\uad6d\uc5b4\ub85c \uc791\uc131\n"
        "2. \ucd08\uc2e0\uc790\ub3c4 \uc774\ud574\ud560 \uc218 \uc788\uac8c \uc9e7\uace0 \uc27d\uac8c \uc791\uc131\n"
        "3. \uc124\uad50 \ub0b4\uc6a9\uc744 \uc65c\uace1\ud558\uac70\ub098 \ucd94\uce21\ud558\uc9c0 \ub9d0 \uac83\n"
        "4. \uc804\uccb4 \uc124\uad50 \uc694\uc57d\uc740 1~2\ubb38\ub2e8 \uc815\ub3c4\ub85c \uc791\uc131\n"
        "5. \uc124\uad50 \ud750\ub984 \uc815\ub9ac\ub294 8~10\uac1c \ubb38\uc7a5\uc73c\ub85c \uc21c\uc11c\ub300\ub85c \uc815\ub9ac\n"
        "6. \ud575\uc2ec \uba54\uc2dc\uc9c0 3\uac1c\ub294 \uac01\uac01 1~2\ubb38\uc7a5, \uac00\uae09\uc801 \uac04\uacb0\ud558\uac8c \uc791\uc131\n"
        "7. \uc124\uad50 \ub9d0\uc500 \uc911 \uad50\uc778\ub4e4\uc774 \uacf5\uac10\ud560 \ub9cc\ud55c \uc778\uc0c1 \ubb38\uc7a5 3\uac1c\ub97c highlight_quotes\ub85c \ubf51\uc744 \uac83\n"
        "8. \ud654\uc694\uc77c\ubd80\ud130 \ud1a0\uc694\uc77c\uae4c\uc9c0 5\uc77c\uc740 \uac01\uac01 'quiz 1\uac1c + reflection question 1\uac1c + mission 1\uac1c' \uc138\ud2b8\ub97c \ub9cc\ub4e4 \uac83\n"
        "9. daily_engagements\uc758 day_number\ub294 1~5\ub97c \uc21c\uc11c\ub300\ub85c \uc0ac\uc6a9\ud560 \uac83\n"
        "10. \ubcc4\ub3c4\uc758 \uc8fc\uac04 \ud034\uc988 \ubb36\uc74c\uacfc \uc8fc\uac04 \ubbf8\uc158 \ubb36\uc74c\uc740 \ub9cc\ub4e4\uc9c0 \ub9d0 \uac83\n"
        "11. \ucd9c\ub825\uc740 JSON\ub9cc \ubc18\ud658\n\n"
        f"\uc124\uad50 \uc815\ubcf4:\n{source_text}"
    )


def extract_output_text(response_data: dict) -> str:
    chunks = []
    for output_item in response_data.get("output", []):
        for content_item in output_item.get("content", []):
            if content_item.get("type") in {"output_text", "text"}:
                text = content_item.get("text")
                if text:
                    chunks.append(text)
    if not chunks:
        raise AIContentGenerationError("OpenAI response did not contain text output.")
    return "".join(chunks)


def parse_generated_content(payload: str) -> GeneratedSermonContent:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise AIContentGenerationError("OpenAI response was not valid JSON.") from exc

    return GeneratedSermonContent(
        title=data["title"].strip(),
        bible_passage=data["bible_passage"].strip(),
        overview=data["overview"].strip(),
        outline_points=[item.strip() for item in data["outline_points"][:10]],
        summary_3lines=[item.strip() for item in data["summary_3lines"][:3]],
        key_points=[item.strip() for item in data["key_points"][:3]],
        highlight_quotes=[item.strip() for item in data["highlight_quotes"][:3]],
        daily_engagements=data["daily_engagements"][:5],
    )


def request_ai_generated_content(sermon: Sermon) -> GeneratedSermonContent:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise AIContentGenerationError("OPENAI_API_KEY is not configured.")

    body = {
        "model": DEFAULT_MODEL,
        "input": build_user_prompt(sermon),
        "text": {
            "format": {
                "type": "json_schema",
                "name": "sermon_content",
                "strict": True,
                "schema": SERMON_CONTENT_SCHEMA,
            }
        },
    }
    request = Request(
        OPENAI_RESPONSES_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=300) as response:
            response_data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise AIContentGenerationError(f"OpenAI API request failed: {detail}") from exc
    except URLError as exc:
        raise AIContentGenerationError(f"OpenAI API request failed: {exc}") from exc

    return parse_generated_content(extract_output_text(response_data))


@transaction.atomic
def apply_generated_content(sermon: Sermon, generated: GeneratedSermonContent) -> Sermon:
    if not sermon.title:
        sermon.title = generated.title
    sermon.bible_passage = generated.bible_passage or sermon.bible_passage
    sermon.ai_generated = True
    sermon.ai_error = ""
    sermon.status = SermonStatus.GENERATED
    sermon.last_ai_generated_at = timezone.now()
    sermon.save(
        update_fields=[
            "title",
            "bible_passage",
            "ai_generated",
            "ai_error",
            "status",
            "last_ai_generated_at",
            "updated_at",
        ]
    )

    SermonSummary.objects.update_or_create(
        sermon=sermon,
        defaults={
            "overview": generated.overview,
            "outline_points": generated.outline_points,
            "summary_line1": generated.summary_3lines[0],
            "summary_line2": generated.summary_3lines[1],
            "summary_line3": generated.summary_3lines[2],
            "key_point1": generated.key_points[0],
            "key_point2": generated.key_points[1],
            "key_point3": generated.key_points[2],
            "ai_generated": True,
            "approved": False,
        },
    )

    sermon.highlight_choices.all().delete()
    for index, quote in enumerate(generated.highlight_quotes, start=1):
        if quote.strip():
            SermonHighlightChoice.objects.create(
                sermon=sermon,
                text=quote.strip(),
                order=index,
                ai_generated=True,
            )

    sermon.quizzes.all().delete()
    sermon.missions.all().delete()

    latest_challenge = sermon.weekly_challenges.order_by("-week_start", "-id").first()
    if latest_challenge is None:
        latest_challenge = create_or_update_weekly_challenge(sermon)

    if latest_challenge:
        latest_challenge.daily_engagements.all().delete()
        for item in generated.daily_engagements:
            quiz = item["quiz"]
            mission = item["mission"]
            choices = quiz["choices"][:4]
            DailyEngagement.objects.create(
                sermon=sermon,
                challenge=latest_challenge,
                day_number=item["day_number"],
                title=item["title"].strip(),
                intro=item["intro"].strip(),
                quiz_question=quiz["question"].strip(),
                quiz_choice1=choices[0].strip(),
                quiz_choice2=choices[1].strip(),
                quiz_choice3=choices[2].strip(),
                quiz_choice4=choices[3].strip(),
                quiz_answer=quiz["answer"].strip(),
                quiz_explanation=quiz["explanation"].strip(),
                reflection_question=item["reflection_question"].strip(),
                mission_title=mission["title"].strip(),
                mission_description=mission["description"].strip(),
                ai_generated=True,
                approved=False,
            )

    return sermon


def generate_sermon_content(sermon: Sermon) -> Sermon:
    generated = request_ai_generated_content(sermon)
    return apply_generated_content(sermon, generated)
