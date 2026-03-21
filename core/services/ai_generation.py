import json
import os
from dataclasses import dataclass
from typing import Dict, List
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.db import transaction
from django.utils import timezone

from core.models import Sermon, SermonMission, SermonQuiz, SermonStatus, SermonSummary


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5")


SERMON_CONTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "bible_passage": {"type": "string"},
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
        "quizzes": {
            "type": "array",
            "items": {
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
            "minItems": 3,
            "maxItems": 3,
        },
        "missions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["title", "description"],
                "additionalProperties": False,
            },
            "minItems": 2,
            "maxItems": 2,
        },
    },
    "required": [
        "title",
        "bible_passage",
        "summary_3lines",
        "key_points",
        "quizzes",
        "missions",
    ],
    "additionalProperties": False,
}


class AIContentGenerationError(Exception):
    pass


@dataclass
class GeneratedSermonContent:
    title: str
    bible_passage: str
    summary_3lines: List[str]
    key_points: List[str]
    quizzes: List[Dict]
    missions: List[Dict]


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
        "4. \ud034\uc988\ub294 \uac1d\uad00\uc2dd 3\ubb38\uc81c, \ubcf4\uae30 4\uac1c, \uc815\ub2f5 1\uac1c\n"
        "5. \ubbf8\uc158\uc740 \ubd80\ub2f4\uc2a4\ub7fd\uc9c0 \uc54a\uace0 \uc2e4\ucc9c \uac00\ub2a5\ud55c 2\uac1c\n"
        "6. \ucd9c\ub825\uc740 JSON\ub9cc \ubc18\ud658\n\n"
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
        summary_3lines=[item.strip() for item in data["summary_3lines"][:3]],
        key_points=[item.strip() for item in data["key_points"][:3]],
        quizzes=data["quizzes"][:3],
        missions=data["missions"][:2],
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
        with urlopen(request, timeout=90) as response:
            response_data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise AIContentGenerationError(f"OpenAI API request failed: {detail}") from exc
    except URLError as exc:
        raise AIContentGenerationError(f"OpenAI API request failed: {exc}") from exc

    return parse_generated_content(extract_output_text(response_data))


@transaction.atomic
def apply_generated_content(sermon: Sermon, generated: GeneratedSermonContent) -> Sermon:
    sermon.title = generated.title or sermon.title
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

    sermon.quizzes.all().delete()
    for index, quiz in enumerate(generated.quizzes, start=1):
        choices = quiz["choices"][:4]
        SermonQuiz.objects.create(
            sermon=sermon,
            question=quiz["question"].strip(),
            choice1=choices[0].strip(),
            choice2=choices[1].strip(),
            choice3=choices[2].strip(),
            choice4=choices[3].strip(),
            correct_answer=quiz["answer"].strip(),
            explanation=quiz["explanation"].strip(),
            order=index,
            ai_generated=True,
            approved=False,
        )

    sermon.missions.all().delete()
    for index, mission in enumerate(generated.missions, start=1):
        SermonMission.objects.create(
            sermon=sermon,
            title=mission["title"].strip(),
            description=mission["description"].strip(),
            order=index,
            ai_generated=True,
            approved=False,
        )

    return sermon


def generate_sermon_content(sermon: Sermon) -> Sermon:
    generated = request_ai_generated_content(sermon)
    return apply_generated_content(sermon, generated)
