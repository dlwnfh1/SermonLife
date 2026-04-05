from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0016_alter_mediastoragesetting_source_media_subdir_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="sermon",
            name="playback_video_file",
            field=models.FileField(blank=True, null=True, upload_to="sermons/playback/"),
        ),
    ]
