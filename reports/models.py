from django.db import models


class WeeklyParticipationReport(models.Model):
    challenge = models.OneToOneField(
        "core.WeeklyChallenge",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="weekly_participation_report",
    )
    sermon = models.ForeignKey(
        "core.Sermon",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="weekly_participation_reports",
    )
    title = models.CharField(max_length=255)
    week_start = models.DateField()
    week_end = models.DateField()
    participant_count = models.PositiveIntegerField(default=0)
    total_points = models.PositiveIntegerField(default=0)
    most_completed_day_label = models.CharField(max_length=255, blank=True)
    most_completed_day_count = models.PositiveIntegerField(default=0)
    most_completed_day_rate = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    least_completed_day_label = models.CharField(max_length=255, blank=True)
    least_completed_day_count = models.PositiveIntegerField(default=0)
    least_completed_day_rate = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    day_rows = models.JSONField(default=list, blank=True)
    generated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-week_start", "-id"]
        verbose_name = "주간 참여"
        verbose_name_plural = "주간 참여"

    def __str__(self):
        return f"{self.week_start} - {self.title}"


class SermonParticipationReport(models.Model):
    sermon = models.OneToOneField(
        "core.Sermon",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sermon_participation_report",
    )
    primary_challenge = models.ForeignKey(
        "core.WeeklyChallenge",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sermon_participation_reports",
    )
    title = models.CharField(max_length=255)
    sermon_date = models.DateField()
    participant_count = models.PositiveIntegerField(default=0)
    total_points = models.PositiveIntegerField(default=0)
    average_points_per_participant = models.DecimalField(max_digits=8, decimal_places=1, default=0)
    quiz_participant_count = models.PositiveIntegerField(default=0)
    reflection_participant_count = models.PositiveIntegerField(default=0)
    mission_participant_count = models.PositiveIntegerField(default=0)
    weekly_completer_count = models.PositiveIntegerField(default=0)
    weekly_completion_rate = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    action_rows = models.JSONField(default=list, blank=True)
    generated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-sermon_date", "-id"]
        verbose_name = "설교별 참여"
        verbose_name_plural = "설교별 참여"

    def __str__(self):
        return f"{self.sermon_date} - {self.title}"


class DailyActionReport(models.Model):
    challenge = models.OneToOneField(
        "core.WeeklyChallenge",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="daily_action_report",
    )
    sermon = models.ForeignKey(
        "core.Sermon",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="daily_action_reports",
    )
    title = models.CharField(max_length=255)
    week_start = models.DateField()
    week_end = models.DateField()
    participant_count = models.PositiveIntegerField(default=0)
    day_rows = models.JSONField(default=list, blank=True)
    strongest_day_label = models.CharField(max_length=255, blank=True)
    weakest_day_label = models.CharField(max_length=255, blank=True)
    generated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-week_start", "-id"]
        verbose_name = "일자별 행동"
        verbose_name_plural = "일자별 행동"

    def __str__(self):
        return f"{self.week_start} - {self.title}"


class UserParticipationReport(models.Model):
    user = models.OneToOneField("auth.User", on_delete=models.CASCADE, related_name="participation_report")
    username = models.CharField(max_length=150)
    display_name = models.CharField(max_length=150, blank=True)
    member_role = models.CharField(max_length=50, blank=True)
    total_points = models.PositiveIntegerField(default=0)
    streak_days = models.PositiveIntegerField(default=0)
    weekly_completer_count = models.PositiveIntegerField(default=0)
    active_this_week = models.BooleanField(default=False)
    recent_two_week_streak = models.BooleanField(default=False)
    inactive_for_two_weeks = models.BooleanField(default=False)
    last_activity_at = models.DateTimeField(null=True, blank=True)
    recent_week_rows = models.JSONField(default=list, blank=True)
    generated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-total_points", "username"]
        verbose_name = "사용자 참여"
        verbose_name_plural = "사용자 참여"

    def __str__(self):
        return self.display_name or self.username


class ContentQualityReport(models.Model):
    challenge = models.OneToOneField(
        "core.WeeklyChallenge",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="content_quality_report",
    )
    sermon = models.ForeignKey(
        "core.Sermon",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="content_quality_reports",
    )
    title = models.CharField(max_length=255)
    week_start = models.DateField()
    week_end = models.DateField()
    participant_count = models.PositiveIntegerField(default=0)
    lowest_quiz_accuracy_label = models.CharField(max_length=255, blank=True)
    lowest_quiz_accuracy_rate = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    lowest_reflection_label = models.CharField(max_length=255, blank=True)
    lowest_reflection_rate = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    lowest_mission_label = models.CharField(max_length=255, blank=True)
    lowest_mission_rate = models.DecimalField(max_digits=5, decimal_places=1, default=0)
    issue_count = models.PositiveIntegerField(default=0)
    quality_rows = models.JSONField(default=list, blank=True)
    generated_at = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-week_start", "-id"]
        verbose_name = "콘텐츠 품질"
        verbose_name_plural = "콘텐츠 품질"

    def __str__(self):
        return f"{self.week_start} - {self.title}"
