from datetime import datetime, time, timedelta
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


def get_current_public_sermon_id():
    active_challenge = WeeklyChallenge.get_current_public_challenge()
    if active_challenge and active_challenge.sermon_id:
        return active_challenge.sermon_id
    return None


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
    bible_text = models.TextField(blank=True)
    ai_generated = models.BooleanField(default=False)
    import_error = models.TextField(blank=True)
    ai_error = models.TextField(blank=True)
    audio_error = models.TextField(blank=True)
    pastor_review_requested = models.BooleanField(default=False)
    pastor_review_requested_at = models.DateTimeField(null=True, blank=True)
    pastor_publication_requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="publication_requested_sermons",
    )
    pastor_publication_requested_at = models.DateTimeField(null=True, blank=True)
    force_public_visibility = models.BooleanField(default=False)
    scheduled_publish_at = models.DateTimeField(null=True, blank=True)
    last_imported_at = models.DateTimeField(null=True, blank=True)
    last_ai_generated_at = models.DateTimeField(null=True, blank=True)
    last_audio_generated_at = models.DateTimeField(null=True, blank=True)
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
        if self.audio_file:
            audio_suffix = Path(self.audio_file.name).suffix.lower()
            if audio_suffix in {".mp4", ".webm", ".ogv"}:
                try:
                    return self.audio_file.url
                except Exception:
                    return ""
        return ""

    @property
    def hosted_video_inline_supported(self):
        if self.source_media_asset and self.source_media_asset.file:
            if Path(self.source_media_asset.file.name).suffix.lower() in {".mp4", ".webm", ".ogv"}:
                return True
        if self.audio_file and Path(self.audio_file.name).suffix.lower() in {".mp4", ".webm", ".ogv"}:
            return True
        return False

    @property
    def hosted_video_mime_type(self):
        if self.source_media_asset and self.source_media_asset.file:
            suffix = Path(self.source_media_asset.file.name).suffix.lower()
        elif self.audio_file:
            suffix = Path(self.audio_file.name).suffix.lower()
        else:
            suffix = ""
        return {
            ".mp4": "video/mp4",
            ".mov": "video/quicktime",
            ".m4v": "video/x-m4v",
            ".webm": "video/webm",
            ".ogv": "video/ogg",
        }.get(suffix, "video/mp4")

    def _get_public_release_at(self):
        self.sync_weekly_challenge_schedule()
        latest_challenge = self.weekly_challenges.order_by("-week_start", "-id").first()
        if latest_challenge is None:
            return timezone.now()
        release_date = latest_challenge.release_date_for_day(1)
        return timezone.make_aware(
            datetime.combine(release_date, time.min),
            timezone.get_current_timezone(),
        )

    def sync_weekly_challenge_schedule(self):
        week_start = self.sermon_date + timedelta(days=1)
        week_end = week_start + timedelta(days=6)
        title = f"{week_start.strftime('%m/%d')} Weekly Sermon Challenge"
        challenge, _ = WeeklyChallenge.objects.update_or_create(
            sermon=self,
            defaults={
                "title": title,
                "week_start": week_start,
                "week_end": week_end,
            },
        )
        return challenge

    def schedule_or_publish(self, now=None):
        now = now or timezone.now()
        release_at = self._get_public_release_at()
        if now < release_at:
            self.schedule_publication(release_at)
            return "scheduled", release_at
        self.publish(published_at=now)
        return "published", now

    def schedule_publication(self, scheduled_at):
        self.approve_generated_content()
        self.is_published = False
        self.status = SermonStatus.APPROVED
        self.scheduled_publish_at = scheduled_at
        self.pastor_review_requested = True
        if self.pastor_review_requested_at is None:
            self.pastor_review_requested_at = timezone.now()
        self.save(
            update_fields=[
                "is_published",
                "status",
                "scheduled_publish_at",
                "pastor_review_requested",
                "pastor_review_requested_at",
                "updated_at",
            ]
        )
        self.weekly_challenges.update(is_active=False)

    def publish(self, published_at=None):
        self.approve_generated_content()
        self.is_published = True
        self.status = SermonStatus.PUBLISHED
        self.published_at = published_at or timezone.now()
        self.scheduled_publish_at = None
        self.force_public_visibility = False
        self.pastor_review_requested = True
        if self.pastor_review_requested_at is None:
            self.pastor_review_requested_at = timezone.now()
        self.save(
            update_fields=[
                "is_published",
                "status",
                "published_at",
                "scheduled_publish_at",
                "force_public_visibility",
                "pastor_review_requested",
                "pastor_review_requested_at",
                "updated_at",
            ]
        )
        latest_challenge = self.weekly_challenges.order_by("-week_start", "-id").first()
        if latest_challenge:
            latest_challenge.activate()

    def unpublish(self):
        self.is_published = False
        self.status = SermonStatus.APPROVED
        self.scheduled_publish_at = None
        self.force_public_visibility = False
        self.save(update_fields=["is_published", "status", "scheduled_publish_at", "force_public_visibility", "updated_at"])
        self.weekly_challenges.update(is_active=False)

    def approve_generated_content(self):
        self.status = SermonStatus.PUBLISHED if self.is_published else SermonStatus.APPROVED
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
        if self.audio_file:
            try:
                return self.audio_file.path
            except Exception:
                return ""
        return self.source_media_path

    def mark_ready_for_pastor_review(self):
        type(self).objects.exclude(pk=self.pk).filter(pastor_review_requested=True).update(
            pastor_review_requested=False
        )
        self.pastor_review_requested = True
        self.pastor_review_requested_at = timezone.now()
        self.save(update_fields=["pastor_review_requested", "pastor_review_requested_at", "updated_at"])

    def mark_pastor_publication_requested(self, user, requested_at=None):
        self.pastor_publication_requested_by = user
        self.pastor_publication_requested_at = requested_at or timezone.now()
        self.save(
            update_fields=[
                "pastor_publication_requested_by",
                "pastor_publication_requested_at",
                "updated_at",
            ]
        )

    def force_publish(self, published_at=None):
        self.approve_generated_content()
        self.is_published = True
        self.status = SermonStatus.PUBLISHED
        self.published_at = published_at or self.published_at or timezone.now()
        self.scheduled_publish_at = None
        self.force_public_visibility = True
        self.save(
            update_fields=[
                "is_published",
                "status",
                "published_at",
                "scheduled_publish_at",
                "force_public_visibility",
                "updated_at",
            ]
        )
        latest_challenge = self.weekly_challenges.order_by("-week_start", "-id").first()
        if latest_challenge:
            latest_challenge.activate()

    def clear_force_publish(self):
        self.force_public_visibility = False
        self.save(update_fields=["force_public_visibility", "updated_at"])
        latest_challenge = self.weekly_challenges.order_by("-week_start", "-id").first()
        if latest_challenge and not latest_challenge.is_public_window_open():
            latest_challenge.is_active = False
            latest_challenge.save(update_fields=["is_active"])

    @classmethod
    def release_due_publications(cls, now=None):
        now = now or timezone.now()
        due_sermons = list(
            cls.objects.filter(
                is_published=False,
                scheduled_publish_at__isnull=False,
                scheduled_publish_at__lte=now,
            ).order_by("scheduled_publish_at", "id")
        )
        released_ids = []
        for sermon in due_sermons:
            sermon.publish(published_at=sermon.scheduled_publish_at or now)
            released_ids.append(sermon.pk)
        return released_ids


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


class SermonHighlightChoice(models.Model):
    sermon = models.ForeignKey(Sermon, on_delete=models.CASCADE, related_name="highlight_choices")
    text = models.TextField()
    order = models.PositiveSmallIntegerField(default=1)
    ai_generated = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "id"]
        verbose_name = "설교 인상 문장"
        verbose_name_plural = "설교 인상 문장"

    def __str__(self):
        return f"{self.sermon.title} - {self.order}"


class SermonHighlightVote(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    sermon = models.ForeignKey(Sermon, on_delete=models.CASCADE, related_name="highlight_votes")
    choice = models.ForeignKey(
        SermonHighlightChoice,
        on_delete=models.CASCADE,
        related_name="votes",
    )
    voted_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "sermon"],
                name="unique_sermon_highlight_vote_per_user",
            )
        ]
        ordering = ["-voted_at", "-id"]


class SermonAudioClipKind(models.TextChoices):
    WEEKLY_SUMMARY = "weekly_summary", "주간 요약 듣기"
    DAILY_CONTENT = "daily_content", "오늘 내용 듣기"


class SermonAudioClip(models.Model):
    sermon = models.ForeignKey(Sermon, on_delete=models.CASCADE, related_name="audio_clips")
    kind = models.CharField(max_length=30, choices=SermonAudioClipKind.choices)
    day_number = models.PositiveSmallIntegerField(default=0)
    title = models.CharField(max_length=120, blank=True)
    script = models.TextField(blank=True)
    voice = models.CharField(max_length=50, blank=True)
    file = models.FileField(upload_to="sermons/audio/generated/", blank=True, null=True)
    error = models.TextField(blank=True)
    generated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["kind", "day_number", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["sermon", "kind", "day_number"],
                name="unique_sermon_audio_clip_per_kind_day",
            )
        ]

    def __str__(self):
        if self.kind == SermonAudioClipKind.WEEKLY_SUMMARY:
            return f"{self.sermon.title} - 주간 요약 듣기"
        return f"{self.sermon.title} - Day {self.day_number} 듣기"


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


class PrayerRequestStatus(models.TextChoices):
    PRAYING = "praying", "기도중"
    ANSWERED = "answered", "응답받음"
    ON_HOLD = "on_hold", "보류"


class PrayerRequestVisibility(models.TextChoices):
    PRIVATE = "private", "혼자 기도할게요"
    PUBLIC = "public", "함께 기도 부탁드려요"
    ANONYMOUS = "anonymous", "이름은 숨기고 함께 기도 부탁드려요"


class PrayerRequest(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="prayer_requests")
    title = models.CharField(max_length=120)
    content = models.TextField()
    status = models.CharField(max_length=20, choices=PrayerRequestStatus.choices, default=PrayerRequestStatus.PRAYING)
    is_public = models.BooleanField(default=False)
    visibility = models.CharField(
        max_length=20,
        choices=PrayerRequestVisibility.choices,
        default=PrayerRequestVisibility.PRIVATE,
    )
    testimony_note = models.TextField(blank=True)
    scripture_recommendations = models.JSONField(default=list, blank=True)
    answered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-created_at", "-id"]

    def __str__(self):
        return f"{self.user.get_username()} - {self.title}"

    def save(self, *args, **kwargs):
        self.title = self._build_title_from_content()
        self.is_public = self.visibility in {
            PrayerRequestVisibility.PUBLIC,
            PrayerRequestVisibility.ANONYMOUS,
        }
        if self.status == PrayerRequestStatus.ANSWERED and self.answered_at is None:
            self.answered_at = timezone.now()
        elif self.status != PrayerRequestStatus.ANSWERED:
            self.answered_at = None
        super().save(*args, **kwargs)

    def _build_title_from_content(self):
        source = (self.content or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not source:
            return self.title or "기도제목"
        first_line = next((line.strip() for line in source.split("\n") if line.strip()), "")
        compact = first_line or source
        return compact[:40]

    @property
    def is_publicly_shared(self):
        return self.visibility in {PrayerRequestVisibility.PUBLIC, PrayerRequestVisibility.ANONYMOUS}

    @property
    def is_anonymous_public(self):
        return self.visibility == PrayerRequestVisibility.ANONYMOUS


class PrayerCompanion(models.Model):
    prayer_request = models.ForeignKey(
        PrayerRequest,
        on_delete=models.CASCADE,
        related_name="companions",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="prayer_companions",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["prayer_request", "user"],
                name="unique_prayer_companion_per_user",
            )
        ]
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.user.get_username()} -> {self.prayer_request.title}"


class PastorNotificationRecipient(models.Model):
    name = models.CharField(max_length=100, blank=True)
    email = models.EmailField(unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "email"]
        verbose_name = "목회자 공지 수신자"
        verbose_name_plural = "목회자 공지 수신자"

    def __str__(self):
        return self.name or self.email


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

    def public_start_date(self):
        return self.release_date_for_day(1)

    def public_end_date(self):
        return self.release_date_for_day(5)

    def is_public_window_open(self, today=None):
        today = today or timezone.localdate()
        return self.public_start_date() <= today <= self.public_end_date()

    @classmethod
    def get_current_public_challenge(cls, today=None):
        Sermon.release_due_publications()
        today = today or timezone.localdate()
        candidates = list(
            cls.objects.filter(
                is_active=True,
                sermon__is_published=True,
                sermon__status=SermonStatus.PUBLISHED,
            )
            .select_related("sermon")
            .order_by("-week_start", "-id")
        )
        stale_ids = []
        for challenge in candidates:
            if challenge.sermon.force_public_visibility:
                if stale_ids:
                    cls.objects.filter(pk__in=stale_ids).update(is_active=False)
                return challenge
            if challenge.is_public_window_open(today):
                if stale_ids:
                    cls.objects.filter(pk__in=stale_ids).update(is_active=False)
                return challenge
            stale_ids.append(challenge.pk)
        if stale_ids:
            cls.objects.filter(pk__in=stale_ids).update(is_active=False)
        return None

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
