from datetime import date
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse

from .models import DailyEngagement, Sermon, SermonStatus, SermonSummary, WeeklyChallenge
from .services.ai_generation import GeneratedSermonContent, apply_generated_content
from .services.transcript_service import extract_video_id


class HomeViewTests(TestCase):
    def test_home_page_loads_without_published_sermon(self):
        response = self.client.get(reverse("core:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "이번 주 설교가 아직 공개되지 않았습니다.")

    def test_home_page_shows_daily_engagement_for_active_challenge(self):
        sermon = Sermon.objects.create(
            title="하나님은 마음을 보십니다",
            preacher="김목사",
            sermon_date=date(2026, 3, 15),
            bible_passage="사도행전 5:1-11",
            status=SermonStatus.PUBLISHED,
            is_published=True,
        )
        SermonSummary.objects.create(
            sermon=sermon,
            overview="설교 개요",
            outline_points=["흐름 1", "흐름 2", "흐름 3", "흐름 4", "흐름 5", "흐름 6", "흐름 7", "흐름 8"],
            key_point1="핵심 1",
            key_point2="핵심 2",
            key_point3="핵심 3",
            approved=True,
        )
        challenge = WeeklyChallenge.objects.create(
            sermon=sermon,
            title="3월 셋째 주 설교 챌린지",
            week_start=date(2026, 3, 16),
            week_end=date(2026, 3, 22),
            is_active=True,
        )
        for day_number in range(1, 6):
            DailyEngagement.objects.create(
                challenge=challenge,
                day_number=day_number,
                title=f"Day {day_number}",
                intro=f"Intro {day_number}",
                quiz_question=f"Quiz {day_number}",
                quiz_choice1="A",
                quiz_choice2="B",
                quiz_choice3="C",
                quiz_choice4="D",
                quiz_answer="A",
                quiz_explanation="Because",
                reflection_question=f"Reflect {day_number}",
                mission_title=f"Mission {day_number}",
                mission_description="Do it this week.",
                approved=True,
            )

        response = self.client.get(reverse("core:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "이번 주 5일 루틴")
        self.assertContains(response, "오늘의 세트")
        self.assertContains(response, "설교 개요")
        self.assertContains(response, "Day 5")


class AIContentTests(TestCase):
    def test_apply_generated_content_creates_daily_engagements(self):
        sermon = Sermon.objects.create(
            title="원본 설교",
            sermon_date=date(2026, 3, 15),
            transcript="설교 원문 예시",
        )
        challenge = WeeklyChallenge.objects.create(
            sermon=sermon,
            title="3월 셋째 주 설교 챌린지",
            week_start=date(2026, 3, 16),
            week_end=date(2026, 3, 22),
        )
        generated = GeneratedSermonContent(
            title="AI 생성 제목",
            bible_passage="사도행전 5:1-11",
            overview="설교 전체 개요",
            outline_points=[
                "흐름 1",
                "흐름 2",
                "흐름 3",
                "흐름 4",
                "흐름 5",
                "흐름 6",
                "흐름 7",
                "흐름 8",
            ],
            summary_3lines=["요약 1", "요약 2", "요약 3"],
            key_points=["핵심 1", "핵심 2", "핵심 3"],
            daily_engagements=[
                {
                    "day_number": day_number,
                    "title": f"Day {day_number}",
                    "intro": f"Daily intro {day_number}",
                    "quiz": {
                        "question": f"Daily quiz {day_number}",
                        "choices": ["A", "B", "C", "D"],
                        "answer": "A",
                        "explanation": "Daily explanation",
                    },
                    "reflection_question": f"Daily reflection {day_number}",
                    "mission": {
                        "title": f"Daily mission {day_number}",
                        "description": "Daily mission description",
                    },
                }
                for day_number in range(1, 6)
            ],
        )

        apply_generated_content(sermon, generated)
        sermon.refresh_from_db()
        challenge.refresh_from_db()

        self.assertEqual(sermon.title, "AI 생성 제목")
        self.assertEqual(sermon.status, SermonStatus.GENERATED)
        self.assertTrue(sermon.ai_generated)
        self.assertEqual(len(sermon.summary.outline_points), 8)
        self.assertEqual(challenge.daily_engagements.count(), 5)
        self.assertEqual(challenge.daily_engagements.get(day_number=3).quiz_question, "Daily quiz 3")
        self.assertEqual(sermon.quizzes.count(), 0)
        self.assertEqual(sermon.missions.count(), 0)

    def test_approve_generated_content_approves_all_related_content(self):
        sermon = Sermon.objects.create(
            title="승인 테스트 설교",
            sermon_date=date(2026, 3, 15),
        )
        SermonSummary.objects.create(
            sermon=sermon,
            overview="개요",
            approved=False,
        )
        challenge = WeeklyChallenge.objects.create(
            sermon=sermon,
            title="주간 챌린지",
            week_start=date(2026, 3, 16),
            week_end=date(2026, 3, 22),
        )
        DailyEngagement.objects.create(
            challenge=challenge,
            day_number=1,
            title="Day 1",
            intro="Intro",
            quiz_question="Daily quiz",
            quiz_choice1="A",
            quiz_choice2="B",
            quiz_choice3="C",
            quiz_choice4="D",
            quiz_answer="A",
            quiz_explanation="Because",
            reflection_question="Reflect",
            mission_title="Mission",
            mission_description="Do it",
            approved=False,
        )

        sermon.approve_generated_content()
        sermon.refresh_from_db()

        self.assertEqual(sermon.status, SermonStatus.APPROVED)
        self.assertTrue(sermon.summary.approved)
        self.assertTrue(challenge.daily_engagements.first().approved)

    @patch("core.management.commands.import_latest_sermon.generate_sermon_content")
    @patch("core.management.commands.import_latest_sermon.import_latest_sermon")
    def test_import_command_runs_ai_generation_after_import(self, mock_import, mock_generate):
        sermon = Sermon.objects.create(
            title="가져온 설교",
            sermon_date=date(2026, 3, 15),
            transcript="설교 원문",
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

    @patch("core.management.commands.transcribe_sermon_audio.generate_sermon_content")
    @patch("core.management.commands.transcribe_sermon_audio.transcribe_audio_file")
    def test_transcribe_audio_command_saves_transcript_and_runs_ai(self, mock_transcribe, mock_generate):
        sermon = Sermon.objects.create(
            title="오디오 설교",
            sermon_date=date(2026, 3, 15),
        )
        mock_transcribe.return_value = "전사된 설교 본문"

        call_command(
            "transcribe_sermon_audio",
            str(sermon.id),
            r"C:\projects\SermonLife\uploads\sermons\2026-03-15-sermon.mp3.mp3",
        )

        sermon.refresh_from_db()
        self.assertEqual(sermon.transcript, "전사된 설교 본문")
        mock_generate.assert_called_once_with(sermon)
