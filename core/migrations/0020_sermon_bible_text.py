from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0019_sermon_audio_error_sermon_last_audio_generated_at_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="sermon",
            name="bible_text",
            field=models.TextField(blank=True),
        ),
    ]
