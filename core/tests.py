from datetime import date
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse

from .models import Sermon, SermonMission, SermonQuiz, SermonStatus, SermonSummary, WeeklyChallenge
from .services.ai_generation import GeneratedSermonContent, apply_generated_content
from .services.transcript_service import extract_video_id


class HomeViewTests(TestCase):
    def test_home_page_loads_without_published_sermon(self):
        response = self.client.get(reverse("core:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "이번 주 설교가 아직 공개되지 않았습니다.")

    def test_home_page_shows_published_sermon_content(self):
        sermon = Sermon.objects.create(
            title="새 마음을 주시는 하나님",
            preacher="김다니엘 목사",
            sermon_date=date(2026, 3, 15),
            bible_passage="에스겔 36:26",
            status=SermonStatus.PUBLISHED,
            is_published=True,
        )
        SermonSummary.objects.create(
            sermon=sermon,
            summary_line1="하나님은 우리의 굳은 마음을 만지신다.",
            summary_line2="은혜는 삶의 방향을 새롭게 한다.",
            summary_line3="새 마음은 순종으로 이어진다.",
            key_point1="은혜는 변화의 시작이다.",
            key_point2="복음은 삶을 새롭게 한다.",
            key_point3="순종은 작은 결단에서 시작된다.",
            approved=True,
        )
        SermonQuiz.objects.create(
            sermon=sermon,
            question="하나님이 주시겠다고 하신 것은 무엇인가요?",
            choice1="새 집",
            choice2="새 마음",
            choice3="새 옷",
            choice4="새 계획",
            correct_answer="새 마음",
            explanation="본문은 새 영과 새 마음을 약속합니다.",
            order=1,
            approved=True,
        )
        SermonMission.objects.create(
            sermon=sermon,
            title="오늘의 순종 적기",
            description="오늘 실천할 순종 한 가지를 기록하세요.",
            order=1,
            approved=True,
        )
        WeeklyChallenge.objects.create(
            sermon=sermon,
            title="3월 셋째 주 설교 챌린지",
            week_start=date(2026, 3, 16),
            week_end=date(2026, 3, 22),
            is_active=True,
        )

        response = self.client.get(reverse("core:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "새 마음을 주시는 하나님")
        self.assertContains(response, "하나님은 우리의 굳은 마음을 만지신다.")
        self.assertContains(response, "3월 셋째 주 설교 챌린지")


class AIContentTests(TestCase):
    def test_apply_generated_content_updates_related_models(self):
        sermon = Sermon.objects.create(
            title="원본 설교",
            sermon_date=date(2026, 3, 15),
            transcript="설교 자막 예시",
        )
        generated = GeneratedSermonContent(
            title="AI 생성 제목",
            bible_passage="요한복음 3:16",
            summary_3lines=["요약1", "요약2", "요약3"],
            key_points=["핵심1", "핵심2", "핵심3"],
            quizzes=[
                {"question": "질문1", "choices": ["A", "B", "C", "D"], "answer": "A", "explanation": "해설1"},
                {"question": "질문2", "choices": ["A", "B", "C", "D"], "answer": "B", "explanation": "해설2"},
                {"question": "질문3", "choices": ["A", "B", "C", "D"], "answer": "C", "explanation": "해설3"},
            ],
            missions=[
                {"title": "미션1", "description": "설명1"},
                {"title": "미션2", "description": "설명2"},
            ],
        )

        apply_generated_content(sermon, generated)
        sermon.refresh_from_db()

        self.assertEqual(sermon.title, "AI 생성 제목")
        self.assertEqual(sermon.status, SermonStatus.GENERATED)
        self.assertTrue(sermon.ai_generated)
        self.assertEqual(sermon.summary.summary_line1, "요약1")
        self.assertEqual(sermon.quizzes.count(), 3)
        self.assertEqual(sermon.missions.count(), 2)
        self.assertFalse(sermon.ai_error)
        self.assertIsNotNone(sermon.last_ai_generated_at)

    @patch("core.management.commands.import_latest_sermon.generate_sermon_content")
    @patch("core.management.commands.import_latest_sermon.import_latest_sermon")
    def test_import_command_runs_ai_generation_after_import(self, mock_import, mock_generate):
        sermon = Sermon.objects.create(
            title="가져온 설교",
            sermon_date=date(2026, 3, 15),
            transcript="설교 자막",
        )
        mock_import.return_value = sermon

        call_command("import_latest_sermon")

        mock_import.assert_called_once()
        mock_generate.assert_called_once_with(sermon)

    @patch("core.management.commands.import_latest_sermon.import_latest_sermon")
    def test_import_command_stops_when_transcript_fetch_failed(self, mock_import):
        sermon = Sermon.objects.create(
            title="가져온 설교",
            sermon_date=date(2026, 3, 15),
            import_error="Transcript fetch failed",
        )
        mock_import.return_value = sermon

        with self.assertRaises(CommandError):
            call_command("import_latest_sermon")


class TranscriptServiceTests(TestCase):
    def test_extract_video_id_from_watch_url(self):
        self.assertEqual(extract_video_id("https://www.youtube.com/watch?v=FQnUuUWGuWE"), "FQnUuUWGuWE")
