from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0010_alter_userprofile_member_role"),
        ("reports", "0002_weeklyparticipationreport_delete_reportmenu"),
    ]

    operations = [
        migrations.CreateModel(
            name="SermonParticipationReport",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=255)),
                ("sermon_date", models.DateField()),
                ("participant_count", models.PositiveIntegerField(default=0)),
                ("total_points", models.PositiveIntegerField(default=0)),
                ("average_points_per_participant", models.DecimalField(decimal_places=1, default=0, max_digits=8)),
                ("quiz_participant_count", models.PositiveIntegerField(default=0)),
                ("reflection_participant_count", models.PositiveIntegerField(default=0)),
                ("mission_participant_count", models.PositiveIntegerField(default=0)),
                ("weekly_completer_count", models.PositiveIntegerField(default=0)),
                ("weekly_completion_rate", models.DecimalField(decimal_places=1, default=0, max_digits=5)),
                ("action_rows", models.JSONField(blank=True, default=list)),
                ("generated_at", models.DateTimeField(auto_now=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "primary_challenge",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=models.deletion.SET_NULL,
                        related_name="sermon_participation_reports",
                        to="core.weeklychallenge",
                    ),
                ),
                (
                    "sermon",
                    models.OneToOneField(
                        on_delete=models.deletion.CASCADE,
                        related_name="sermon_participation_report",
                        to="core.sermon",
                    ),
                ),
            ],
            options={
                "verbose_name": "설교별 참여",
                "verbose_name_plural": "설교별 참여",
                "ordering": ["-sermon_date", "-id"],
            },
        ),
    ]
