from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0017_sermon_playback_video_file"),
        ("reports", "0006_contentqualityreport"),
    ]

    operations = [
        migrations.AlterField(
            model_name="weeklyparticipationreport",
            name="challenge",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=models.SET_NULL,
                related_name="weekly_participation_report",
                to="core.weeklychallenge",
            ),
        ),
        migrations.AlterField(
            model_name="weeklyparticipationreport",
            name="sermon",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.SET_NULL,
                related_name="weekly_participation_reports",
                to="core.sermon",
            ),
        ),
        migrations.AlterField(
            model_name="sermonparticipationreport",
            name="sermon",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=models.SET_NULL,
                related_name="sermon_participation_report",
                to="core.sermon",
            ),
        ),
        migrations.AlterField(
            model_name="dailyactionreport",
            name="challenge",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=models.SET_NULL,
                related_name="daily_action_report",
                to="core.weeklychallenge",
            ),
        ),
        migrations.AlterField(
            model_name="dailyactionreport",
            name="sermon",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.SET_NULL,
                related_name="daily_action_reports",
                to="core.sermon",
            ),
        ),
        migrations.AlterField(
            model_name="contentqualityreport",
            name="challenge",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=models.SET_NULL,
                related_name="content_quality_report",
                to="core.weeklychallenge",
            ),
        ),
        migrations.AlterField(
            model_name="contentqualityreport",
            name="sermon",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.SET_NULL,
                related_name="content_quality_reports",
                to="core.sermon",
            ),
        ),
    ]
