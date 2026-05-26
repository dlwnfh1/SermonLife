from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("core", "0032_alter_transcriptcorrectionrule_options_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="AttendanceDistrict",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120)),
                ("is_active", models.BooleanField(default=True)),
                ("sort_order", models.PositiveIntegerField(default=100)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("church", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="attendance_districts", to="core.church")),
            ],
            options={
                "verbose_name": "교구",
                "verbose_name_plural": "교구",
                "ordering": ["sort_order", "name", "id"],
                "unique_together": {("church", "name")},
            },
        ),
        migrations.CreateModel(
            name="AttendanceSession",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("worship_date", models.DateField()),
                ("title", models.CharField(blank=True, max_length=120)),
                ("is_locked", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("church", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="attendance_sessions", to="core.church")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_attendance_sessions", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "주일 출석표",
                "verbose_name_plural": "주일 출석표",
                "ordering": ["-worship_date", "-id"],
                "unique_together": {("church", "worship_date")},
            },
        ),
        migrations.CreateModel(
            name="AttendanceGroup",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120)),
                ("is_active", models.BooleanField(default=True)),
                ("sort_order", models.PositiveIntegerField(default=100)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("church", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="attendance_groups", to="core.church")),
                ("district", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="groups", to="attendance.attendancedistrict")),
                ("leader", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="led_attendance_groups", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "속",
                "verbose_name_plural": "속",
                "ordering": ["district__sort_order", "sort_order", "name", "id"],
                "unique_together": {("district", "name")},
            },
        ),
        migrations.CreateModel(
            name="AttendanceMember",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120)),
                ("phone", models.CharField(blank=True, max_length=30)),
                ("note", models.CharField(blank=True, max_length=255)),
                ("is_active", models.BooleanField(default=True)),
                ("sort_order", models.PositiveIntegerField(default=100)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("church", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="attendance_members", to="core.church")),
                ("group", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="members", to="attendance.attendancegroup")),
            ],
            options={
                "verbose_name": "속원",
                "verbose_name_plural": "속원",
                "ordering": ["group__sort_order", "sort_order", "name", "id"],
            },
        ),
        migrations.CreateModel(
            name="AttendanceDistrictLeader",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("is_primary", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("district", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="leaders", to="attendance.attendancedistrict")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="attendance_district_roles", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "교구장",
                "verbose_name_plural": "교구장",
                "ordering": ["-is_primary", "id"],
                "unique_together": {("district", "user")},
            },
        ),
        migrations.CreateModel(
            name="AttendanceRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("present", "출석"), ("absent", "결석"), ("online", "온라인"), ("excused", "사유 있음")], default="absent", max_length=20)),
                ("marked_at", models.DateTimeField(blank=True, null=True)),
                ("note", models.CharField(blank=True, max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("marked_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="marked_attendance_records", to=settings.AUTH_USER_MODEL)),
                ("member", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="attendance_records", to="attendance.attendancemember")),
                ("session", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="records", to="attendance.attendancesession")),
            ],
            options={
                "verbose_name": "출석 기록",
                "verbose_name_plural": "출석 기록",
                "ordering": ["member__sort_order", "member__name", "id"],
                "unique_together": {("session", "member")},
            },
        ),
    ]
