from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0027_prayercompanion"),
    ]

    operations = [
        migrations.AddField(
            model_name="prayerrequest",
            name="scripture_recommendations",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
