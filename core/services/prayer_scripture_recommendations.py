import json
import os
import re
from functools import lru_cache
from html import unescape
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
            "minItems": 3,
            "maxItems": 3,
        }
    },
    "required": ["recommendations"],
    "additionalProperties": False,
}


class PrayerScriptureRecommendationError(Exception):
    pass


PUBLIC_DOMAIN_KOREAN_BIBLE_BASE_URL = "https://ebible.org/kor/{book_code}{chapter:02d}.htm"
PUBLIC_DOMAIN_TRANSLATION_LABEL = "공개 한국어 성경(1910 번역)"

KOREAN_BIBLE_BOOK_CODES = {
    "창세기": "GEN",
    "출애굽기": "EXO",
    "레위기": "LEV",
    "민수기": "NUM",
    "신명기": "DEU",
    "여호수아": "JOS",
    "사사기": "JDG",
    "룻기": "RUT",
    "사무엘상": "1SA",
    "사무엘하": "2SA",
    "열왕기상": "1KI",
    "열왕기하": "2KI",
    "역대상": "1CH",
    "역대하": "2CH",
    "에스라": "EZR",
    "느헤미야": "NEH",
    "에스더": "EST",
    "욥기": "JOB",
    "시편": "PSA",
    "잠언": "PRO",
    "전도서": "ECC",
    "아가": "SNG",
    "이사야": "ISA",
    "예레미야": "JER",
    "예레미야애가": "LAM",
    "에스겔": "EZK",
    "다니엘": "DAN",
    "호세아": "HOS",
    "요엘": "JOL",
    "아모스": "AMO",
    "오바댜": "OBA",
    "요나": "JON",
    "미가": "MIC",
    "나훔": "NAH",
    "하박국": "HAB",
    "스바냐": "ZEP",
    "학개": "HAG",
    "스가랴": "ZEC",
    "말라기": "MAL",
    "마태복음": "MAT",
    "마가복음": "MRK",
    "누가복음": "LUK",
    "요한복음": "JHN",
    "사도행전": "ACT",
    "로마서": "ROM",
    "고린도전서": "1CO",
    "고린도후서": "2CO",
    "갈라디아서": "GAL",
    "에베소서": "EPH",
    "빌립보서": "PHP",
    "골로새서": "COL",
    "데살로니가전서": "1TH",
    "데살로니가후서": "2TH",
    "디모데전서": "1TI",
    "디모데후서": "2TI",
    "디도서": "TIT",
    "빌레몬서": "PHM",
    "히브리서": "HEB",
    "야고보서": "JAS",
    "베드로전서": "1PE",
    "베드로후서": "2PE",
    "요한일서": "1JN",
    "요한이서": "2JN",
    "요한삼서": "3JN",
    "유다서": "JUD",
    "요한계시록": "REV",
}


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
        reference = _normalize_reference_to_single_verse((item.get("reference") or "").strip())
        reason = (item.get("reason") or "").strip()
        if not reference or not reason or reference in seen_references:
            continue
        seen_references.add(reference)
        recommendations.append({"reference": reference, "reason": reason})

    if not recommendations:
        raise PrayerScriptureRecommendationError("No scripture recommendations were returned.")
    return recommendations


def _normalize_book_name(book_name: str) -> str:
    return re.sub(r"\s+", "", (book_name or "").strip())


def _normalize_reference_to_single_verse(reference: str) -> str:
    reference = (reference or "").strip()
    match = re.match(
        r"^\s*(?P<book>.+?)\s+(?P<chapter>\d+):(?P<verse>\d+)(?:\s*[-–~]\s*\d+)?\s*$",
        reference,
    )
    if not match:
        return reference
    book = " ".join(match.group("book").split()).strip()
    chapter = match.group("chapter")
    verse = match.group("verse")
    return f"{book} {chapter}:{verse}"


def _parse_reference(reference: str):
    match = re.match(r"^\s*(?P<book>.+?)\s+(?P<chapter>\d+):(?P<verse>\d+)", (reference or "").strip())
    if not match:
        return None
    book_name = _normalize_book_name(match.group("book"))
    chapter = int(match.group("chapter"))
    verse = int(match.group("verse"))
    book_code = KOREAN_BIBLE_BOOK_CODES.get(book_name)
    if not book_code:
        return None
    return book_code, chapter, verse


@lru_cache(maxsize=256)
def _fetch_public_domain_chapter_text(book_code: str, chapter: int) -> str:
    url = PUBLIC_DOMAIN_KOREAN_BIBLE_BASE_URL.format(book_code=book_code, chapter=chapter)
    request = Request(url, headers={"User-Agent": "WordAndLife/1.0"})
    with urlopen(request, timeout=15) as response:
        html = response.read().decode("utf-8", errors="ignore")
    html = re.sub(r"(?is)<script.*?>.*?</script>", "", html)
    html = re.sub(r"(?is)<style.*?>.*?</style>", "", html)
    html = re.sub(r"(?<!\d)(\d{1,3})\xa0", r"\n\1 ", html)
    text = re.sub(r"(?i)<br\s*/?>", "\n", html)
    text = re.sub(r"(?i)</p\s*>", "\n", text)
    text = re.sub(r"(?i)</li\s*>", "\n", text)
    text = re.sub(r"(?i)</div\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text).replace("\xa0", " ")
    return text


def _lookup_public_domain_verse_text(reference: str) -> str:
    parsed = _parse_reference(reference)
    if not parsed:
        return ""
    book_code, chapter, verse = parsed
    try:
        chapter_text = _fetch_public_domain_chapter_text(book_code, chapter)
    except Exception:
        return ""

    verse_pattern = re.compile(rf"(?m)^\s*{verse}\s+(?P<text>.+)$")
    lines = chapter_text.splitlines()
    capture = False
    collected = []
    for raw_line in lines:
        line = " ".join(raw_line.split()).strip()
        if not line:
            continue
        verse_match = re.match(r"^(?P<number>\d+)\s+(?P<text>.+)$", line)
        if verse_match:
            number = int(verse_match.group("number"))
            if capture and number != verse:
                break
            if number == verse:
                capture = True
                collected.append(verse_match.group("text").strip())
                continue
        elif capture:
            if re.match(r"^\d+$", line) or line in {"<", ">"}:
                break
            collected.append(line)
    if not collected:
        return ""
    best = " ".join(collected).strip()
    if best in {"<", ">"}:
        return ""
    return best


def enrich_prayer_scripture_recommendations(recommendations: list[dict]) -> tuple[list[dict], bool]:
    enriched = []
    changed = False
    for item in recommendations or []:
        normalized = {
            "reference": (item.get("reference") or "").strip(),
            "reason": (item.get("reason") or "").strip(),
        }
        previous_verse_text = (item.get("verse_text") or "").strip()
        verse_text = ""
        if normalized["reference"]:
            verse_text = _lookup_public_domain_verse_text(normalized["reference"])
        if verse_text != previous_verse_text:
            changed = True
        if verse_text:
            normalized["verse_text"] = verse_text
            normalized["translation"] = PUBLIC_DOMAIN_TRANSLATION_LABEL
        elif item.get("verse_text") or item.get("translation"):
            changed = True
        enriched.append(normalized)
    return enriched, changed


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

    recommendations = _parse_recommendations(_extract_output_text(response_data))
    enriched, _ = enrich_prayer_scripture_recommendations(recommendations)
    return enriched
