from datetime import date
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.models import Sum
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse

from .models import DailyEngagement, DailyQuizAttempt, MediaStorageSetting, PointLedger, PointSource, Sermon, SermonStatus, SermonSummary, SourceMediaAsset, UserProfile, WeeklyChallenge, get_source_media_subdir
from .services.ai_generation import GeneratedSermonContent, apply_generated_content
from .services.engagement import DAILY_COMPLETION_POINTS, MISSION_POINTS, QUIZ_POINTS, REFLECTION_POINTS, WEEKLY_COMPLETION_POINTS
from .services.transcript_service import extract_video_id
from reports.services import (
    sync_content_quality_report,
    sync_daily_action_report,
    sync_sermon_participation_report,
    sync_user_participation_report,
    sync_weekly_participation_report,
)


User = get_user_model()


class HomeViewTests(TestCase):
    def test_source_media_subdir_uses_admin_setting_when_present(self):
        MediaStorageSetting.objects.create(source_media_subdir="weekly_sermons")

        self.assertEqual(get_source_media_subdir(), "weekly_sermons")

    def test_sermon_prefers_hosted_video_url_when_source_media_asset_is_video(self):
        asset = SourceMediaAsset.objects.create(file="sermons/current-week.mp4")
        sermon = Sermon.objects.create(
            title="영상 재생 설교",
            sermon_date=date(2026, 3, 15),
            source_media_asset=asset,
        )

        self.assertEqual(sermon.hosted_video_url, "/media/sermons/current-week.mp4")
        self.assertEqual(sermon.hosted_video_mime_type, "video/mp4")
        self.assertTrue(sermon.hosted_video_inline_supported)

    def test_source_media_admin_syncs_existing_files_from_folder(self):
        admin_user = User.objects.create_superuser(username="syncadmin", password="1234", email="sync@example.com")
        self.client.force_login(admin_user)
        with tempfile.TemporaryDirectory() as temp_dir:
            with override_settings(MEDIA_ROOT=Path(temp_dir), SOURCE_MEDIA_UPLOAD_SUBDIR="sermons"):
                uploads_dir = Path(temp_dir) / "sermons"
                uploads_dir.mkdir(parents=True, exist_ok=True)
                existing_file = uploads_dir / "already-there.mov"
                existing_file.write_text("temp", encoding="utf-8")

                response = self.client.get("/admin/core/sourcemediaasset/")

                self.assertEqual(response.status_code, 200)
                self.assertTrue(SourceMediaAsset.objects.filter(file="sermons/already-there.mov").exists())

    def test_home_redirects_to_login_when_logged_out(self):
        response = self.client.get(reverse("core:home"))

        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse("core:login"))

    def test_home_page_shows_daily_engagement_for_active_challenge(self):
        user = User.objects.create_user(username="viewer", password="1234")
        self.client.force_login(user)
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
                sermon=sermon,
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

    def test_daily_actions_award_points_up_to_twenty(self):
        user = User.objects.create_user(username="member", password="pw")
        self.client.force_login(user)
        sermon = Sermon.objects.create(
            title="점수 테스트 설교",
            sermon_date=date(2026, 3, 15),
            status=SermonStatus.PUBLISHED,
            is_published=True,
        )
        SermonSummary.objects.create(sermon=sermon, overview="개요", approved=True)
        challenge = WeeklyChallenge.objects.create(
            sermon=sermon,
            title="점수 주간 챌린지",
            week_start=date(2026, 3, 16),
            week_end=date(2026, 3, 22),
            is_active=True,
        )
        daily = DailyEngagement.objects.create(
            sermon=sermon,
            challenge=challenge,
            day_number=5,
            title="Day 5",
            intro="Intro",
            quiz_question="Quiz",
            quiz_choice1="A",
            quiz_choice2="B",
            quiz_choice3="C",
            quiz_choice4="D",
            quiz_answer="A",
            quiz_explanation="Because",
            reflection_question="Reflect",
            mission_title="Mission",
            mission_description="Do it",
            approved=True,
        )

        self.client.post(reverse("core:submit_daily_quiz", args=[daily.id]), {"selected_answer": "A"})
        self.client.post(reverse("core:submit_reflection", args=[daily.id]), {"response_text": "충분히 긴 묵상 답변입니다."})
        self.client.post(reverse("core:complete_mission", args=[daily.id]), {"mission_note": "실천 완료"})

        total = PointLedger.objects.filter(user=user, challenge=challenge).aggregate(total=Sum("points"))["total"]
        self.assertEqual(total, QUIZ_POINTS + REFLECTION_POINTS + MISSION_POINTS + DAILY_COMPLETION_POINTS)

    def test_second_quiz_submission_does_not_change_wrong_first_attempt_or_award_points(self):
        user = User.objects.create_user(username="quizlock", password="1234")
        sermon = Sermon.objects.create(
            title="퀴즈 잠금 설교",
            sermon_date=date(2026, 3, 15),
            status=SermonStatus.PUBLISHED,
            is_published=True,
        )
        challenge = WeeklyChallenge.objects.create(
            sermon=sermon,
            title="퀴즈 잠금 주간",
            week_start=date(2026, 3, 16),
            week_end=date(2026, 3, 22),
            is_active=True,
        )
        daily = DailyEngagement.objects.create(
            sermon=sermon,
            challenge=challenge,
            day_number=1,
            title="Day 1",
            intro="Intro",
            quiz_question="Quiz",
            quiz_choice1="A",
            quiz_choice2="B",
            quiz_choice3="C",
            quiz_choice4="D",
            quiz_answer="A",
            quiz_explanation="Because",
            reflection_question="Reflect",
            mission_title="Mission",
            mission_description="Do it",
            approved=True,
        )

        from .services.engagement import submit_daily_quiz

        first_result = submit_daily_quiz(user=user, daily_engagement=daily, selected_answer="B")
        second_result = submit_daily_quiz(user=user, daily_engagement=daily, selected_answer="A")

        self.assertFalse(first_result["attempt"].is_correct)
        self.assertTrue(second_result["is_update"])
        self.assertFalse(second_result["attempt"].is_correct)
        self.assertFalse(second_result["points_awarded"])
        self.assertEqual(DailyQuizAttempt.objects.filter(user=user, daily_engagement=daily).count(), 1)
        self.assertFalse(
            PointLedger.objects.filter(
                user=user,
                challenge=challenge,
                source=PointSource.QUIZ,
                note="day:1",
            ).exists()
        )

    def test_signup_creates_user_and_profile(self):
        response = self.client.post(
            reverse("core:signup"),
            {
                "username": "newmember",
                "first_name": "홍길동",
                "member_role": "elder",
                "password1": "StrongPassword123!",
                "password2": "StrongPassword123!",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(User.objects.filter(username="newmember").exists())
        user = User.objects.get(username="newmember")
        self.assertTrue(UserProfile.objects.filter(user=user, member_role="elder").exists())

    def test_signup_shows_message_when_username_is_duplicated(self):
        User.objects.create_user(username="duplicate", password="1234")

        response = self.client.post(
            reverse("core:signup"),
            {
                "username": "duplicate",
                "first_name": "홍길동",
                "member_role": "member",
                "password1": "1234",
                "password2": "1234",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "이미 사용 중인 아이디입니다.")

    def test_logged_in_user_can_change_password(self):
        user = User.objects.create_user(username="pwuser", password="1234")
        self.client.force_login(user)

        response = self.client.post(
            reverse("core:password_change"),
            {
                "old_password": "1234",
                "new_password1": "5678",
                "new_password2": "5678",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertTrue(user.check_password("5678"))

    def test_admin_weekly_report_page_loads(self):
        admin_user = User.objects.create_superuser(username="admin", password="1234", email="admin@example.com")
        sermon = Sermon.objects.create(
            title="리포트 테스트 설교",
            sermon_date=date(2026, 3, 15),
            status=SermonStatus.PUBLISHED,
            is_published=True,
        )
        challenge = WeeklyChallenge.objects.create(
            sermon=sermon,
            title="주간 참여 리포트",
            week_start=date(2026, 3, 16),
            week_end=date(2026, 3, 22),
            is_active=True,
        )
        report = sync_weekly_participation_report(challenge)
        self.client.force_login(admin_user)
        list_response = self.client.get("/admin/reports/weeklyparticipationreport/")
        detail_response = self.client.get(f"/admin/reports/weeklyparticipationreport/{report.pk}/change/")
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(detail_response.status_code, 200)

    def test_admin_sermon_report_page_loads(self):
        admin_user = User.objects.create_superuser(username="reportadmin", password="1234", email="report@example.com")
        sermon = Sermon.objects.create(
            title="설교별 리포트 테스트",
            sermon_date=date(2026, 3, 15),
            status=SermonStatus.PUBLISHED,
            is_published=True,
        )
        report = sync_sermon_participation_report(sermon)
        self.client.force_login(admin_user)

        list_response = self.client.get("/admin/reports/sermonparticipationreport/")
        detail_response = self.client.get(f"/admin/reports/sermonparticipationreport/{report.pk}/change/")

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(detail_response.status_code, 200)

    def test_admin_daily_action_report_page_loads(self):
        admin_user = User.objects.create_superuser(username="dayadmin", password="1234", email="day@example.com")
        sermon = Sermon.objects.create(
            title="일자별 행동 테스트",
            sermon_date=date(2026, 3, 15),
            status=SermonStatus.PUBLISHED,
            is_published=True,
        )
        challenge = WeeklyChallenge.objects.create(
            sermon=sermon,
            title="일자별 행동 주간",
            week_start=date(2026, 3, 16),
            week_end=date(2026, 3, 22),
            is_active=True,
        )
        report = sync_daily_action_report(challenge)
        self.client.force_login(admin_user)

        list_response = self.client.get("/admin/reports/dailyactionreport/")
        detail_response = self.client.get(f"/admin/reports/dailyactionreport/{report.pk}/change/")

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(detail_response.status_code, 200)

    def test_admin_user_participation_report_page_loads(self):
        admin_user = User.objects.create_superuser(username="useradmin", password="1234", email="user@example.com")
        member = User.objects.create_user(username="member1", password="1234", first_name="홍길동")
        UserProfile.objects.create(user=member, member_role="member", points=10, streak_days=2)
        report = sync_user_participation_report(member)
        self.client.force_login(admin_user)

        list_response = self.client.get("/admin/reports/userparticipationreport/")
        detail_response = self.client.get(f"/admin/reports/userparticipationreport/{report.pk}/change/")

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(detail_response.status_code, 200)

    def test_admin_content_quality_report_page_loads(self):
        admin_user = User.objects.create_superuser(username="qualityadmin", password="1234", email="quality@example.com")
        sermon = Sermon.objects.create(
            title="콘텐츠 품질 테스트",
            sermon_date=date(2026, 3, 15),
            status=SermonStatus.PUBLISHED,
            is_published=True,
        )
        challenge = WeeklyChallenge.objects.create(
            sermon=sermon,
            title="콘텐츠 품질 주간",
            week_start=date(2026, 3, 16),
            week_end=date(2026, 3, 22),
            is_active=True,
        )
        DailyEngagement.objects.create(
            sermon=sermon,
            challenge=challenge,
            day_number=1,
            title="Day 1",
            intro="Intro",
            quiz_question="Quiz",
            quiz_choice1="A",
            quiz_choice2="B",
            quiz_choice3="C",
            quiz_choice4="D",
            quiz_answer="A",
            quiz_explanation="Because",
            reflection_question="Reflect",
            mission_title="Mission",
            mission_description="Do it",
            approved=True,
        )
        report = sync_content_quality_report(challenge)
        self.client.force_login(admin_user)

        list_response = self.client.get("/admin/reports/contentqualityreport/")
        detail_response = self.client.get(f"/admin/reports/contentqualityreport/{report.pk}/change/")

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(detail_response.status_code, 200)

    @patch("core.admin.generate_sermon_content")
    @patch("core.admin.transcribe_audio_file")
    def test_admin_transcribe_and_regenerate_view_runs_for_source_media_path(self, mock_transcribe, mock_generate):
        admin_user = User.objects.create_superuser(username="mediaadmin", password="1234", email="media@example.com")
        with tempfile.TemporaryDirectory() as temp_dir:
            with override_settings(MEDIA_ROOT=Path(temp_dir), SOURCE_MEDIA_UPLOAD_SUBDIR="sermons"):
                uploads_dir = Path(temp_dir) / "sermons"
                uploads_dir.mkdir(parents=True, exist_ok=True)
                actual_file = uploads_dir / "sample.mp4"
                actual_file.write_text("temp", encoding="utf-8")
                asset = SourceMediaAsset.objects.create(file="sermons/sample.mp4")
                sermon = Sermon.objects.create(
                    title="편집본 설교",
                    sermon_date=date(2026, 3, 15),
                    source_media_asset=asset,
                )
                mock_transcribe.return_value = "편집본 전사"
                self.client.force_login(admin_user)

                response = self.client.get(f"/admin/core/sermon/{sermon.pk}/transcribe-and-regenerate-ai/", follow=True)

                self.assertEqual(response.status_code, 200)
                sermon.refresh_from_db()
                self.assertEqual(sermon.transcript, "편집본 전사")
                mock_transcribe.assert_called_once_with(sermon.source_media_asset.file.path)
                mock_generate.assert_called_once_with(sermon)

    def test_admin_delete_source_media_view_removes_file_and_clears_path(self):
        admin_user = User.objects.create_superuser(username="deleteadmin", password="1234", email="delete@example.com")
        with tempfile.TemporaryDirectory() as temp_dir:
            with override_settings(MEDIA_ROOT=Path(temp_dir), SOURCE_MEDIA_UPLOAD_SUBDIR="sermons"):
                uploads_dir = Path(temp_dir) / "sermons"
                uploads_dir.mkdir(parents=True, exist_ok=True)
                temp_file = uploads_dir / "delete-me-test.mov"
                temp_file.write_text("temp", encoding="utf-8")
                sermon = Sermon.objects.create(
                    title="삭제 테스트 설교",
                    sermon_date=date(2026, 3, 15),
                    source_media_path=str(temp_file),
                )
                self.client.force_login(admin_user)

                response = self.client.get(f"/admin/core/sermon/{sermon.pk}/delete-source-media/", follow=True)

                self.assertEqual(response.status_code, 200)
                sermon.refresh_from_db()
                self.assertEqual(sermon.source_media_path, "")
                self.assertFalse(temp_file.exists())

    def test_admin_add_sermon_with_save_and_prepare_redirects_to_transcribe_flow(self):
        admin_user = User.objects.create_superuser(username="prepareadmin", password="1234", email="prepare@example.com")
        self.client.force_login(admin_user)
        with tempfile.TemporaryDirectory() as temp_dir:
            with override_settings(MEDIA_ROOT=Path(temp_dir), SOURCE_MEDIA_UPLOAD_SUBDIR="sermons"):
                uploads_dir = Path(temp_dir) / "sermons"
                uploads_dir.mkdir(parents=True, exist_ok=True)
                actual_file = uploads_dir / "prepare.mov"
                actual_file.write_text("temp", encoding="utf-8")
                asset = SourceMediaAsset.objects.create(file="sermons/prepare.mov")

                response = self.client.post(
                    "/admin/core/sermon/add/",
                    {
                        "title": "새 설교",
                        "preacher": "Pastor Kim",
                        "sermon_date": "2026-03-29",
                        "bible_passage": "",
                        "youtube_url": "",
                        "audio_file": "",
                        "source_media_asset": str(asset.pk),
                        "transcript": "",
                        "summary-TOTAL_FORMS": "0",
                        "summary-INITIAL_FORMS": "0",
                        "summary-MIN_NUM_FORMS": "0",
                        "summary-MAX_NUM_FORMS": "1",
                        "daily_engagements-TOTAL_FORMS": "0",
                        "daily_engagements-INITIAL_FORMS": "0",
                        "daily_engagements-MIN_NUM_FORMS": "0",
                        "daily_engagements-MAX_NUM_FORMS": "1000",
                        "_save_and_prepare": "1",
                    },
                )

                created = Sermon.objects.get(title="새 설교")
                self.assertEqual(response.status_code, 302)
                self.assertEqual(
                    response.url,
                    f"/admin/core/sermon/{created.pk}/change/",
                )


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
            outline_points=["흐름 1", "흐름 2", "흐름 3", "흐름 4", "흐름 5", "흐름 6", "흐름 7", "흐름 8"],
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

        self.assertEqual(sermon.title, "원본 설교")
        self.assertEqual(sermon.status, SermonStatus.GENERATED)
        self.assertTrue(sermon.ai_generated)
        self.assertEqual(len(sermon.summary.outline_points), 8)
        self.assertEqual(challenge.daily_engagements.count(), 5)
        self.assertEqual(challenge.daily_engagements.get(day_number=3).quiz_question, "Daily quiz 3")
        self.assertEqual(sermon.quizzes.count(), 0)
        self.assertEqual(sermon.missions.count(), 0)

    def test_apply_generated_content_creates_weekly_challenge_when_missing(self):
        sermon = Sermon.objects.create(
            title="챌린지 없는 설교",
            sermon_date=date(2026, 3, 29),
            transcript="설교 원문 예시",
        )
        generated = GeneratedSermonContent(
            title="AI 생성 제목",
            bible_passage="사도행전 5:1-11",
            overview="설교 전체 개요",
            outline_points=["흐름 1", "흐름 2", "흐름 3", "흐름 4", "흐름 5", "흐름 6", "흐름 7", "흐름 8"],
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

        self.assertEqual(sermon.weekly_challenges.count(), 1)
        challenge = sermon.weekly_challenges.first()
        self.assertIsNotNone(challenge)
        self.assertEqual(challenge.daily_engagements.count(), 5)

    def test_apply_generated_content_keeps_existing_sermon_title(self):
        sermon = Sermon.objects.create(
            title="파일명 기준 설교 제목",
            sermon_date=date(2026, 3, 29),
            transcript="설교 원문 예시",
        )
        generated = GeneratedSermonContent(
            title="AI가 바꾼 제목",
            bible_passage="사도행전 5:1-11",
            overview="설교 전체 개요",
            outline_points=["흐름 1", "흐름 2", "흐름 3", "흐름 4", "흐름 5", "흐름 6", "흐름 7", "흐름 8"],
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

        self.assertEqual(sermon.title, "파일명 기준 설교 제목")

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
            sermon=sermon,
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

    def test_weekly_completion_awards_bonus_once(self):
        user = User.objects.create_user(username="finisher", password="pw")
        sermon = Sermon.objects.create(
            title="완주 설교",
            sermon_date=date(2026, 3, 15),
        )
        challenge = WeeklyChallenge.objects.create(
            sermon=sermon,
            title="완주 챌린지",
            week_start=date(2026, 3, 16),
            week_end=date(2026, 3, 22),
        )
        from .services.engagement import complete_mission, submit_daily_quiz, submit_reflection

        for day_number in range(1, 6):
            daily = DailyEngagement.objects.create(
                sermon=sermon,
                challenge=challenge,
                day_number=day_number,
                title=f"Day {day_number}",
                intro="Intro",
                quiz_question="Quiz",
                quiz_choice1="A",
                quiz_choice2="B",
                quiz_choice3="C",
                quiz_choice4="D",
                quiz_answer="A",
                quiz_explanation="Because",
                reflection_question="Reflect",
                mission_title="Mission",
                mission_description="Do it",
                approved=True,
            )
            submit_daily_quiz(user=user, daily_engagement=daily, selected_answer="A")
            submit_reflection(user=user, daily_engagement=daily, response_text="충분히 긴 묵상 답변입니다.")
            complete_mission(user=user, daily_engagement=daily, note="done")

        weekly_bonus_count = PointLedger.objects.filter(
            user=user,
            challenge=challenge,
            source=PointSource.WEEKLY_BONUS,
            note="week_complete",
        ).count()
        self.assertEqual(weekly_bonus_count, 1)
        self.assertEqual(
            PointLedger.objects.filter(user=user, challenge=challenge).aggregate(total=Sum("points"))["total"],
            (QUIZ_POINTS + REFLECTION_POINTS + MISSION_POINTS + DAILY_COMPLETION_POINTS) * 5 + WEEKLY_COMPLETION_POINTS,
        )

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

    @patch("core.services.transcript_service._transcribe_in_chunks")
    @patch("core.services.transcript_service._extract_audio_track")
    def test_transcribe_audio_file_extracts_audio_from_video(self, mock_extract_audio, mock_transcribe_chunks):
        with tempfile.NamedTemporaryFile(suffix=".mov") as media_file, tempfile.NamedTemporaryFile(suffix=".mp3") as audio_file:
            mock_extract_audio.return_value = audio_file.name
            mock_transcribe_chunks.return_value = "영상에서 추출한 전사"

            from .services.transcript_service import transcribe_audio_file

            transcript = transcribe_audio_file(media_file.name)

        self.assertEqual(transcript, "영상에서 추출한 전사")
        mock_extract_audio.assert_called_once()
        mock_transcribe_chunks.assert_called_once()

    @patch("core.services.transcript_service._transcribe_in_chunks")
    def test_transcribe_audio_file_uses_chunk_transcription_for_audio(self, mock_transcribe_chunks):
        with tempfile.NamedTemporaryFile(suffix=".mp3") as audio_file:
            mock_transcribe_chunks.return_value = "오디오 전사"

            from .services.transcript_service import transcribe_audio_file

            transcript = transcribe_audio_file(audio_file.name)

        self.assertEqual(transcript, "오디오 전사")
        mock_transcribe_chunks.assert_called_once()

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

    @patch("core.management.commands.transcribe_sermon_audio.generate_sermon_content")
    @patch("core.management.commands.transcribe_sermon_audio.transcribe_audio_file")
    def test_transcribe_command_accepts_video_media_path(self, mock_transcribe, mock_generate):
        sermon = Sermon.objects.create(
            title="영상 설교",
            sermon_date=date(2026, 3, 22),
        )
        mock_transcribe.return_value = "영상 전사 본문"

        call_command(
            "transcribe_sermon_audio",
            str(sermon.id),
            r"C:\projects\SermonLife\uploads\sermons\순종하는 사람에게 주신 성령.mov",
        )

        sermon.refresh_from_db()
        self.assertEqual(sermon.transcript, "영상 전사 본문")
        mock_transcribe.assert_called_once()
        mock_generate.assert_called_once_with(sermon)
