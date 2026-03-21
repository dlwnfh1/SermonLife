from django.conf import settings
from django.db import models
from django.utils import timezone


class SermonStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    GENERATED = "generated", "Generated"
    APPROVED = "approved", "Approved"
    PUBLISHED = "published", "Published"


class PointSource(models.TextChoices):
    SUMMARY = "summary", "Summary Read"
    QUIZ = "quiz", "Quiz Correct"
    QUIZ_BONUS = "quiz_bonus", "Quiz Completion Bonus"
    MISSION = "mission", "Mission Completion"
    STREAK = "streak", "Streak Bonus"


class Sermon(models.Model):
    title = models.CharField(max_length=255)
    preacher = models.CharField(max_length=100, blank=True)
    sermon_date = models.DateField()
    youtube_url = models.URLField(blank=True)
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

    def publish(self):
        self.is_published = True
        self.status = SermonStatus.PUBLISHED
        self.published_at = timezone.now()
        self.save(update_fields=["is_published", "status", "published_at", "updated_at"])


class SermonSummary(models.Model):
    sermon = models.OneToOneField(Sermon, on_delete=models.CASCADE, related_name="summary")
    summary_line1 = models.CharField(max_length=255, blank=True)
    summary_line2 = models.CharField(max_length=255, blank=True)
    summary_line3 = models.CharField(max_length=255, blank=True)
    key_point1 = models.CharField(max_length=255, blank=True)
    key_point2 = models.CharField(max_length=255, blank=True)
    key_point3 = models.CharField(max_length=255, blank=True)
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
    church_group = models.CharField(max_length=100, blank=True)
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
