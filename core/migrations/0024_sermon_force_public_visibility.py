from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0023_sermon_pastor_publication_requested_by_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="sermon",
            name="force_public_visibility",
            field=models.BooleanField(default=False),
        ),
    ]
