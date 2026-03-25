from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("auth", "0012_alter_user_first_name_max_length"),
        ("reports", "0004_dailyactionreport"),
    ]

    operations = [
        migrations.CreateModel(
            name="UserParticipationReport",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("username", models.CharField(max_length=150)),
                ("display_name", models.CharField(blank=True, max_length=150)),
                ("member_role", models.CharField(blank=True, max_length=50)),
                ("total_points", models.PositiveIntegerField(default=0)),
                ("streak_days", models.PositiveIntegerField(default=0)),
                ("weekly_completer_count", models.PositiveIntegerField(default=0)),
                ("active_this_week", models.BooleanField(default=False)),
                ("recent_two_week_streak", models.BooleanField(default=False)),
                ("inactive_for_two_weeks", models.BooleanField(default=False)),
                ("last_activity_at", models.DateTimeField(blank=True, null=True)),
                ("recent_week_rows", models.JSONField(blank=True, default=list)),
                ("generated_at", models.DateTimeField(auto_now=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=models.deletion.CASCADE,
                        related_name="participation_report",
                        to="auth.user",
                    ),
                ),
            ],
            options={
                "verbose_name": "사용자 참여",
                "verbose_name_plural": "사용자 참여",
                "ordering": ["-total_points", "username"],
            },
        ),
    ]
