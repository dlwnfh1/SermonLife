from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0010_alter_userprofile_member_role"),
        ("reports", "0005_userparticipationreport"),
    ]

    operations = [
        migrations.CreateModel(
            name="ContentQualityReport",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=255)),
                ("week_start", models.DateField()),
                ("week_end", models.DateField()),
                ("participant_count", models.PositiveIntegerField(default=0)),
                ("lowest_quiz_accuracy_label", models.CharField(blank=True, max_length=255)),
                ("lowest_quiz_accuracy_rate", models.DecimalField(decimal_places=1, default=0, max_digits=5)),
                ("lowest_reflection_label", models.CharField(blank=True, max_length=255)),
                ("lowest_reflection_rate", models.DecimalField(decimal_places=1, default=0, max_digits=5)),
                ("lowest_mission_label", models.CharField(blank=True, max_length=255)),
                ("lowest_mission_rate", models.DecimalField(decimal_places=1, default=0, max_digits=5)),
                ("issue_count", models.PositiveIntegerField(default=0)),
                ("quality_rows", models.JSONField(blank=True, default=list)),
                ("generated_at", models.DateTimeField(auto_now=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "challenge",
                    models.OneToOneField(
                        on_delete=models.deletion.CASCADE,
                        related_name="content_quality_report",
                        to="core.weeklychallenge",
                    ),
                ),
                (
                    "sermon",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="content_quality_reports",
                        to="core.sermon",
                    ),
                ),
            ],
            options={
                "verbose_name": "콘텐츠 품질",
                "verbose_name_plural": "콘텐츠 품질",
                "ordering": ["-week_start", "-id"],
            },
        ),
    ]
