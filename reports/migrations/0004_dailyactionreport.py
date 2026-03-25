from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0010_alter_userprofile_member_role"),
        ("reports", "0003_sermonparticipationreport"),
    ]

    operations = [
        migrations.CreateModel(
            name="DailyActionReport",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=255)),
                ("week_start", models.DateField()),
                ("week_end", models.DateField()),
                ("participant_count", models.PositiveIntegerField(default=0)),
                ("day_rows", models.JSONField(blank=True, default=list)),
                ("strongest_day_label", models.CharField(blank=True, max_length=255)),
                ("weakest_day_label", models.CharField(blank=True, max_length=255)),
                ("generated_at", models.DateTimeField(auto_now=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "challenge",
                    models.OneToOneField(
                        on_delete=models.deletion.CASCADE,
                        related_name="daily_action_report",
                        to="core.weeklychallenge",
                    ),
                ),
                (
                    "sermon",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="daily_action_reports",
                        to="core.sermon",
                    ),
                ),
            ],
            options={
                "verbose_name": "일자별 행동",
                "verbose_name_plural": "일자별 행동",
                "ordering": ["-week_start", "-id"],
            },
        ),
    ]
