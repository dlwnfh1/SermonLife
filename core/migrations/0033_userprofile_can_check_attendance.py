from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0032_alter_transcriptcorrectionrule_options_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="can_check_attendance",
            field=models.BooleanField(default=False),
        ),
    ]
