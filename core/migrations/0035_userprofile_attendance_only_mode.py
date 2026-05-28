from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0034_userprofile_can_manage_attendance"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="attendance_only_mode",
            field=models.BooleanField(default=False),
        ),
    ]
