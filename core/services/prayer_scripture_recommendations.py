import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from core.models import PrayerRequest


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.4")

PRAYER_SCRIPTURE_SCHEMA = {
    "type": "object",
    "properties": {
        "recommendations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "reference": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["reference", "reason"],
                "additionalProperties": False,
            },
            "minItems": 1,
            "maxItems": 3,
        }
    },
    "required": ["recommendations"],
    "additionalProperties": False,
}


class PrayerScriptureRecommendationError(Exception):
    pass


def _build_prompt(prayer_request: PrayerRequest) -> str:
    content = (prayer_request.content or "").strip()
    if not content:
        raise PrayerScriptureRecommendationError("Prayer request content is empty.")

    return (
        "다음 기도제목을 읽고, 함께 읽어보면 좋은 성경구절 1~3개를 추천해 주세요.\n\n"
        "반드시 지킬 조건:\n"
        "1. 한국어로 작성\n"
        "2. 성경 본문 전체는 적지 말고, 구절 주소(reference)만 적기\n"
        "3. 각 구절마다 왜 이 기도제목에 어울리는지 짧은 이유(reason)를 1문장으로 적기\n"
        "4. 위로와 믿음을 주는 실제 성경구절만 추천하고, 없는 구절을 만들지 말기\n"
        "5. 같은 의미의 구절을 반복하지 말기\n"
        "6. 출력은 JSON만 반환\n\n"
        f"기도제목:\n{content}"
    )


def _extract_output_text(response_data: dict) -> str:
    chunks = []
    for output_item in response_data.get("output", []):
        for content_item in output_item.get("content", []):
            if content_item.get("type") in {"output_text", "text"}:
                text = content_item.get("text")
                if text:
                    chunks.append(text)
    if not chunks:
        raise PrayerScriptureRecommendationError("OpenAI response did not contain text output.")
    return "".join(chunks)


def _parse_recommendations(payload: str) -> list[dict]:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise PrayerScriptureRecommendationError("OpenAI response was not valid JSON.") from exc

    recommendations = []
    seen_references = set()
    for item in data.get("recommendations", [])[:3]:
        reference = (item.get("reference") or "").strip()
        reason = (item.get("reason") or "").strip()
        if not reference or not reason or reference in seen_references:
            continue
        seen_references.add(reference)
        recommendations.append({"reference": reference, "reason": reason})

    if not recommendations:
        raise PrayerScriptureRecommendationError("No scripture recommendations were returned.")
    return recommendations


def request_prayer_scripture_recommendations(prayer_request: PrayerRequest) -> list[dict]:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise PrayerScriptureRecommendationError("OPENAI_API_KEY is not configured.")

    body = {
        "model": DEFAULT_MODEL,
        "input": _build_prompt(prayer_request),
        "text": {
            "format": {
                "type": "json_schema",
                "name": "prayer_scripture_recommendations",
                "strict": True,
                "schema": PRAYER_SCRIPTURE_SCHEMA,
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
        with urlopen(request, timeout=180) as response:
            response_data = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise PrayerScriptureRecommendationError(f"OpenAI API request failed: {detail}") from exc
    except URLError as exc:
        raise PrayerScriptureRecommendationError(f"OpenAI API request failed: {exc}") from exc

    return _parse_recommendations(_extract_output_text(response_data))
