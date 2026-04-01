from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0010_alter_userprofile_member_role"),
    ]

    operations = [
        migrations.AddField(
            model_name="sermon",
            name="audio_file",
            field=models.FileField(blank=True, null=True, upload_to="sermons/audio/"),
        ),
    ]
