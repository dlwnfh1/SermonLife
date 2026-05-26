from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("attendance", "0002_attendancemember_linked_user_and_leader_updates"),
    ]

    operations = [
        migrations.CreateModel(
            name="AttendanceControl",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("force_open", models.BooleanField(default=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("church", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="attendance_control", to="core.church")),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="attendance_control_updates",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "출석 제어",
                "verbose_name_plural": "출석 제어",
            },
        ),
    ]
