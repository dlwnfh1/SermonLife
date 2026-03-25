from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("reports", "0001_initial"),
    ]

    operations = [
        migrations.DeleteModel(
            name="ReportMenu",
        ),
        migrations.CreateModel(
            name="WeeklyParticipationReport",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=255)),
                ("week_start", models.DateField()),
                ("week_end", models.DateField()),
                ("participant_count", models.PositiveIntegerField(default=0)),
                ("total_points", models.PositiveIntegerField(default=0)),
                ("most_completed_day_label", models.CharField(blank=True, max_length=255)),
                ("most_completed_day_count", models.PositiveIntegerField(default=0)),
                ("most_completed_day_rate", models.DecimalField(decimal_places=1, default=0, max_digits=5)),
                ("least_completed_day_label", models.CharField(blank=True, max_length=255)),
                ("least_completed_day_count", models.PositiveIntegerField(default=0)),
                ("least_completed_day_rate", models.DecimalField(decimal_places=1, default=0, max_digits=5)),
                ("day_rows", models.JSONField(blank=True, default=list)),
                ("generated_at", models.DateTimeField(auto_now=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "challenge",
                    models.OneToOneField(
                        on_delete=models.deletion.CASCADE,
                        related_name="weekly_participation_report",
                        to="core.weeklychallenge",
                    ),
                ),
                (
                    "sermon",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="weekly_participation_reports",
                        to="core.sermon",
                    ),
                ),
            ],
            options={
                "verbose_name": "주간 참여",
                "verbose_name_plural": "주간 참여",
                "ordering": ["-week_start", "-id"],
            },
        ),
    ]
