from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from django.apps import apps as django_apps
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.utils import OperationalError, ProgrammingError
from django.utils import timezone


class SermonStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    GENERATED = "generated", "Generated"
    APPROVED = "approved", "Approved"
    PUBLISHED = "published", "Published"


class PointSource(models.TextChoices):
    SUMMARY = "summary", "Summary Read"
    QUIZ = "quiz", "Quiz Correct"
    REFLECTION = "reflection", "Reflection Response"
    MISSION = "mission", "Mission Completion"
    DAILY_BONUS = "daily_bonus", "Daily Completion Bonus"
    WEEKLY_BONUS = "weekly_bonus", "Weekly Completion Bonus"


class MemberRole(models.TextChoices):
    PASTOR = "pastor", "목회자"
    MEMBER = "member", "교인"
    DEACON = "deacon", "집사"
    KWONSA = "kwonsa", "권사"
    ELDER = "elder", "장로"
    OTHER = "other", "기타"


class MediaStorageSetting(models.Model):
    source_media_subdir = models.CharField(
        max_length=255,
        default="sermons",
        help_text="uploads 아래에서 사용할 폴더 경로입니다. 예: sermons 또는 sermons/2026/april",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "파일 저장 위치"
        verbose_name_plural = "파일 저장 위치"

    def __str__(self):
        return self.source_media_subdir

    def clean(self):
        super().clean()
        normalized = (self.source_media_subdir or "").replace("\\", "/").strip().strip("/")
        if not normalized:
            normalized = "sermons"
        if ":" in normalized or normalized.startswith("/"):
            raise ValidationError(
                {"source_media_subdir": "드라이브 문자나 절대 경로 없이 uploads 아래 폴더 경로만 입력해 주세요."}
            )
        self.source_media_subdir = normalized

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
        type(self).objects.exclude(pk=self.pk).delete()


def get_source_media_subdir():
    default_subdir = getattr(settings, "SOURCE_MEDIA_UPLOAD_SUBDIR", "sermons")
    try:
        model = django_apps.get_model("core", "MediaStorageSetting")
        setting = model.objects.order_by("-id").first()
    except (LookupError, OperationalError, ProgrammingError):
        setting = None

    chosen = setting.source_media_subdir if setting and setting.source_media_subdir else default_subdir
    return chosen.strip("/\\") or "sermons"


def get_source_media_root():
    return Path(settings.MEDIA_ROOT) / get_source_media_subdir()


def source_media_upload_to(instance, filename):
    subdir = get_source_media_subdir()
    return f"{subdir}/{filename}"


class SourceMediaAsset(models.Model):
    file = models.FileField(upload_to=source_media_upload_to)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "원본 파일"
        verbose_name_plural = "원본 파일"

    def __str__(self):
        return Path(self.file.name).stem or self.file.name


class Sermon(models.Model):
    title = models.CharField(max_length=255)
    preacher = models.CharField(max_length=100, blank=True)
    sermon_date = models.DateField()
    youtube_url = models.URLField(blank=True)
    audio_file = models.FileField(upload_to="sermons/audio/", blank=True, null=True)
    playback_video_file = models.FileField(upload_to="sermons/playback/", blank=True, null=True)
    source_media_asset = models.ForeignKey(
        SourceMediaAsset,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sermons",
    )
    source_media_path = models.CharField(max_length=500, blank=True)
    transcript = models.TextField(blank=True)
    bible_passage = models.CharField(max_length=255, blank=True)
    ai_generated = models.BooleanField(default=False)
    import_error = models.TextField(blank=True)
    ai_error = models.TextField(blank=True)
    last_imported_at = models.DateTimeField(null=True, blank=True)
    last_ai_generated_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=SermonStatus.choices,
        default=SermonStatus.DRAFT,
    )
    is_published = models.BooleanField(default=False)
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-sermon_date", "-created_at"]

    def __str__(self):
        return f"{self.sermon_date} - {self.title}"

    @property
    def youtube_embed_url(self):
        if not self.youtube_url:
            return ""

        parsed = urlparse(self.youtube_url)
        host = parsed.netloc.lower()
        video_id = ""

        if "youtu.be" in host:
            video_id = parsed.path.lstrip("/")
        elif "youtube.com" in host:
            if parsed.path == "/watch":
                video_id = parse_qs(parsed.query).get("v", [""])[0]
            elif parsed.path.startswith("/embed/"):
                return self.youtube_url
            elif parsed.path.startswith("/shorts/"):
                video_id = parsed.path.split("/shorts/", 1)[1].split("/", 1)[0]

        if not video_id:
            return ""

        return f"https://www.youtube.com/embed/{video_id}"

    @property
    def hosted_video_url(self):
        if self.source_media_asset and self.source_media_asset.file:
            source_suffix = Path(self.source_media_asset.file.name).suffix.lower()
            if source_suffix in {".mp4", ".webm", ".ogv"}:
                try:
                    return self.source_media_asset.file.url
                except Exception:
                    return ""
        return ""

    @property
    def hosted_video_inline_supported(self):
        if self.source_media_asset and self.source_media_asset.file:
            if Path(self.source_media_asset.file.name).suffix.lower() in {".mp4", ".webm", ".ogv"}:
                return True
        return False

    @property
    def hosted_video_mime_type(self):
        suffix = Path(self.source_media_asset.file.name).suffix.lower() if self.source_media_asset and self.source_media_asset.file else ""
        return {
            ".mp4": "video/mp4",
            ".mov": "video/quicktime",
            ".m4v": "video/x-m4v",
            ".webm": "video/webm",
            ".ogv": "video/ogg",
        }.get(suffix, "video/mp4")

    def publish(self):
        self.approve_generated_content()
        self.is_published = True
        self.status = SermonStatus.PUBLISHED
        self.published_at = timezone.now()
        self.save(update_fields=["is_published", "status", "published_at", "updated_at"])
        latest_challenge = self.weekly_challenges.order_by("-week_start", "-id").first()
        if latest_challenge:
            latest_challenge.activate()

    def unpublish(self):
        self.is_published = False
        self.status = SermonStatus.APPROVED
        self.save(update_fields=["is_published", "status", "updated_at"])
        self.weekly_challenges.update(is_active=False)

    def approve_generated_content(self):
        self.status = SermonStatus.APPROVED
        self.save(update_fields=["status", "updated_at"])

        SermonSummary.objects.filter(sermon=self).update(approved=True)
        self.quizzes.all().delete()
        self.missions.all().delete()
        DailyEngagement.objects.filter(sermon=self).update(approved=True)

    @property
    def resolved_source_media_path(self):
        if self.source_media_asset and self.source_media_asset.file:
            try:
                return self.source_media_asset.file.path
            except Exception:
                return ""
        return self.source_media_path


class SermonSummary(models.Model):
    sermon = models.OneToOneField(Sermon, on_delete=models.CASCADE, related_name="summary")
    overview = models.TextField(blank=True)
    outline_points = models.JSONField(default=list, blank=True)
    summary_line1 = models.CharField(max_length=255, blank=True)
    summary_line2 = models.CharField(max_length=255, blank=True)
    summary_line3 = models.CharField(max_length=255, blank=True)
    key_point1 = models.TextField(blank=True)
    key_point2 = models.TextField(blank=True)
    key_point3 = models.TextField(blank=True)
    ai_generated = models.BooleanField(default=True)
    approved = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Summary - {self.sermon.title}"


class SermonQuiz(models.Model):
    sermon = models.ForeignKey(Sermon, on_delete=models.CASCADE, related_name="quizzes")
    question = models.CharField(max_length=255)
    choice1 = models.CharField(max_length=255)
    choice2 = models.CharField(max_length=255)
    choice3 = models.CharField(max_length=255)
    choice4 = models.CharField(max_length=255)
    correct_answer = models.CharField(max_length=255)
    explanation = models.TextField(blank=True)
    order = models.PositiveIntegerField(default=1)
    ai_generated = models.BooleanField(default=True)
    approved = models.BooleanField(default=False)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.question


class SermonMission(models.Model):
    sermon = models.ForeignKey(Sermon, on_delete=models.CASCADE, related_name="missions")
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    order = models.PositiveIntegerField(default=1)
    ai_generated = models.BooleanField(default=True)
    approved = models.BooleanField(default=False)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return self.title


class UserProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    member_role = models.CharField(
        max_length=20,
        choices=MemberRole.choices,
        default=MemberRole.MEMBER,
    )
    points = models.IntegerField(default=0)
    streak_days = models.IntegerField(default=0)

    def __str__(self):
        return self.user.get_username()


class QuizAttempt(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    sermon = models.ForeignKey(Sermon, on_delete=models.CASCADE)
    quiz = models.ForeignKey(SermonQuiz, on_delete=models.CASCADE)
    selected_answer = models.CharField(max_length=255, blank=True)
    is_correct = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class MissionCompletion(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    sermon = models.ForeignKey(Sermon, on_delete=models.CASCADE)
    mission = models.ForeignKey(SermonMission, on_delete=models.CASCADE)
    completed = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-completed_at", "-id"]


class WeeklyChallenge(models.Model):
    sermon = models.ForeignKey(Sermon, on_delete=models.CASCADE, related_name="weekly_challenges")
    title = models.CharField(max_length=255)
    week_start = models.DateField()
    week_end = models.DateField()
    is_active = models.BooleanField(default=False)
    reset_points_on_start = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-week_start", "-id"]

    def __str__(self):
        return f"{self.week_start} - {self.title}"

    def activate(self):
        with transaction.atomic():
            WeeklyChallenge.objects.exclude(pk=self.pk).update(is_active=False)
            if not self.is_active:
                self.is_active = True
                self.save(update_fields=["is_active"])

    def release_date_for_day(self, day_number):
        return self.week_start + timedelta(days=day_number)

    def current_day_number(self, today=None):
        today = today or timezone.localdate()
        for day_number in range(1, 6):
            if today == self.release_date_for_day(day_number):
                return day_number
        if today > self.release_date_for_day(5):
            return 5
        return 1


class DailyEngagement(models.Model):
    sermon = models.ForeignKey(
        Sermon,
        on_delete=models.CASCADE,
        related_name="daily_engagements",
        null=True,
        blank=True,
    )
    challenge = models.ForeignKey(
        WeeklyChallenge,
        on_delete=models.CASCADE,
        related_name="daily_engagements",
    )
    day_number = models.PositiveSmallIntegerField()
    title = models.CharField(max_length=255)
    intro = models.TextField(blank=True)
    quiz_question = models.CharField(max_length=255)
    quiz_choice1 = models.CharField(max_length=255)
    quiz_choice2 = models.CharField(max_length=255)
    quiz_choice3 = models.CharField(max_length=255)
    quiz_choice4 = models.CharField(max_length=255)
    quiz_answer = models.CharField(max_length=255)
    quiz_explanation = models.TextField(blank=True)
    reflection_question = models.TextField()
    mission_title = models.CharField(max_length=255)
    mission_description = models.TextField(blank=True)
    ai_generated = models.BooleanField(default=True)
    approved = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["day_number", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["challenge", "day_number"],
                name="unique_daily_engagement_per_challenge_day",
            )
        ]

    def __str__(self):
        return f"Day {self.day_number} - {self.title}"

    def save(self, *args, **kwargs):
        if self.challenge_id and self.sermon_id is None:
            self.sermon = self.challenge.sermon
        super().save(*args, **kwargs)

    @property
    def choices(self):
        return [
            self.quiz_choice1,
            self.quiz_choice2,
            self.quiz_choice3,
            self.quiz_choice4,
        ]


class DailyQuizAttempt(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    challenge = models.ForeignKey(WeeklyChallenge, on_delete=models.CASCADE)
    daily_engagement = models.ForeignKey(
        DailyEngagement,
        on_delete=models.CASCADE,
        related_name="quiz_attempts",
    )
    selected_answer = models.CharField(max_length=255)
    is_correct = models.BooleanField(default=False)
    submitted_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "daily_engagement"],
                name="unique_daily_quiz_attempt_per_user",
            )
        ]


class DailyReflectionResponse(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    challenge = models.ForeignKey(WeeklyChallenge, on_delete=models.CASCADE)
    daily_engagement = models.ForeignKey(
        DailyEngagement,
        on_delete=models.CASCADE,
        related_name="reflection_responses",
    )
    response_text = models.TextField()
    submitted_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "daily_engagement"],
                name="unique_daily_reflection_per_user",
            )
        ]


class DailyMissionCompletion(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    challenge = models.ForeignKey(WeeklyChallenge, on_delete=models.CASCADE)
    daily_engagement = models.ForeignKey(
        DailyEngagement,
        on_delete=models.CASCADE,
        related_name="mission_completions",
    )
    completed = models.BooleanField(default=False)
    note = models.CharField(max_length=255, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "daily_engagement"],
                name="unique_daily_mission_completion_per_user",
            )
        ]


class PointLedger(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    challenge = models.ForeignKey(
        WeeklyChallenge,
        on_delete=models.CASCADE,
        related_name="point_entries",
    )
    sermon = models.ForeignKey(Sermon, on_delete=models.CASCADE)
    source = models.CharField(max_length=20, choices=PointSource.choices)
    points = models.IntegerField()
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
