from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import Church


class AttendanceStatus(models.TextChoices):
    PRESENT = "present", "출석"
    ABSENT = "absent", "결석"
    ONLINE = "online", "온라인"
    EXCUSED = "excused", "사유 있음"


class AttendanceDistrict(models.Model):
    church = models.ForeignKey(
        Church,
        on_delete=models.PROTECT,
        related_name="attendance_districts",
    )
    name = models.CharField(max_length=120)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=100)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "name", "id"]
        unique_together = [("church", "name")]
        verbose_name = "교구"
        verbose_name_plural = "교구"

    def __str__(self):
        return self.name


class AttendanceGroup(models.Model):
    church = models.ForeignKey(
        Church,
        on_delete=models.PROTECT,
        related_name="attendance_groups",
    )
    district = models.ForeignKey(
        AttendanceDistrict,
        on_delete=models.PROTECT,
        related_name="groups",
    )
    name = models.CharField(max_length=120)
    leader = models.ForeignKey(
        "attendance.AttendanceMember",
        on_delete=models.SET_NULL,
        related_name="led_attendance_groups",
        null=True,
        blank=True,
        verbose_name="속장",
    )
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=100)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["district__sort_order", "sort_order", "name", "id"]
        unique_together = [("district", "name")]
        verbose_name = "속"
        verbose_name_plural = "속"

    def __str__(self):
        return f"{self.district.name} · {self.name}"


class AttendanceMember(models.Model):
    church = models.ForeignKey(
        Church,
        on_delete=models.PROTECT,
        related_name="attendance_members",
    )
    group = models.ForeignKey(
        AttendanceGroup,
        on_delete=models.PROTECT,
        related_name="members",
    )
    linked_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="attendance_memberships",
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=120)
    phone = models.CharField(max_length=30, blank=True)
    note = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=100)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["group__sort_order", "sort_order", "name", "id"]
        verbose_name = "속원"
        verbose_name_plural = "속원"

    def __str__(self):
        return f"{self.name} ({self.group.name})"


class AttendanceDistrictLeader(models.Model):
    district = models.ForeignKey(
        AttendanceDistrict,
        on_delete=models.CASCADE,
        related_name="leaders",
    )
    name = models.CharField(max_length=120)
    linked_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="attendance_district_roles",
        null=True,
        blank=True,
    )
    is_primary = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-is_primary", "name", "id"]
        unique_together = [("district", "name")]
        verbose_name = "교구장"
        verbose_name_plural = "교구장"

    def __str__(self):
        return f"{self.district.name} - {self.name}"


class AttendanceSession(models.Model):
    church = models.ForeignKey(
        Church,
        on_delete=models.PROTECT,
        related_name="attendance_sessions",
    )
    worship_date = models.DateField()
    title = models.CharField(max_length=120, blank=True)
    is_locked = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="created_attendance_sessions",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-worship_date", "-id"]
        unique_together = [("church", "worship_date")]
        verbose_name = "주일 출석표"
        verbose_name_plural = "주일 출석표"

    def __str__(self):
        return self.title or f"{self.worship_date} 주일 출석"

    def save(self, *args, **kwargs):
        if not self.title:
            self.title = f"{self.worship_date.strftime('%Y-%m-%d')} 주일예배 출석"
        super().save(*args, **kwargs)

    @classmethod
    def get_or_create_current(cls, church, user=None, reference_date=None):
        today = reference_date or timezone.localdate()
        session, created = cls.objects.get_or_create(
            church=church,
            worship_date=today,
            defaults={"created_by": user},
        )
        return session, created


class AttendanceControl(models.Model):
    church = models.OneToOneField(
        Church,
        on_delete=models.CASCADE,
        related_name="attendance_control",
    )
    force_open = models.BooleanField(default=False)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="attendance_control_updates",
        null=True,
        blank=True,
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "출석 제어"
        verbose_name_plural = "출석 제어"

    def __str__(self):
        return f"{self.church.name} 출석 제어"

    @classmethod
    def get_or_create_for_church(cls, church):
        return cls.objects.get_or_create(church=church)


class AttendanceRecord(models.Model):
    session = models.ForeignKey(
        AttendanceSession,
        on_delete=models.CASCADE,
        related_name="records",
    )
    member = models.ForeignKey(
        AttendanceMember,
        on_delete=models.CASCADE,
        related_name="attendance_records",
    )
    status = models.CharField(
        max_length=20,
        choices=AttendanceStatus.choices,
        default=AttendanceStatus.ABSENT,
    )
    marked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="marked_attendance_records",
        null=True,
        blank=True,
    )
    marked_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["member__sort_order", "member__name", "id"]
        unique_together = [("session", "member")]
        verbose_name = "출석 기록"
        verbose_name_plural = "출석 기록"

    def __str__(self):
        return f"{self.session.worship_date} · {self.member.name} · {self.get_status_display()}"
