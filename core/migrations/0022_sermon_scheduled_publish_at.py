from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0021_sermon_pastor_review_requested_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="sermon",
            name="scheduled_publish_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
