from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0011_sermon_audio_file"),
    ]

    operations = [
        migrations.AddField(
            model_name="sermon",
            name="source_media_path",
            field=models.CharField(blank=True, max_length=500),
        ),
    ]
