from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0033_userprofile_can_check_attendance"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="can_manage_attendance",
            field=models.BooleanField(default=False),
        ),
    ]
