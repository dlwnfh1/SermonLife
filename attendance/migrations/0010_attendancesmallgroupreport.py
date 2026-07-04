from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0036_userprofile_reminders_webpushsubscription"),
        ("attendance", "0009_attendancegroup_attendance_pin"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AttendanceSmallGroupReport",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("report_month", models.DateField()),
                ("meeting_date", models.DateField()),
                ("place", models.CharField(blank=True, max_length=255)),
                ("attendee_count", models.PositiveIntegerField(default=0)),
                ("offering_amount", models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ("next_meeting_place", models.CharField(blank=True, max_length=255)),
                ("special_notes", models.TextField(blank=True)),
                ("submitted_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("church", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="attendance_small_group_reports", to="core.church")),
                ("group", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="small_group_reports", to="attendance.attendancegroup")),
                ("submitted_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="submitted_small_group_reports", to=settings.AUTH_USER_MODEL)),
                ("absent_members", models.ManyToManyField(blank=True, related_name="small_group_absence_reports", to="attendance.attendancemember")),
            ],
            options={
                "verbose_name": "속회 보고서",
                "verbose_name_plural": "속회 보고서",
                "ordering": ["-meeting_date", "-id"],
                "unique_together": {("group", "report_month")},
            },
        ),
    ]
