from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("core", "0022_sermon_scheduled_publish_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="sermon",
            name="pastor_publication_requested_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="sermon",
            name="pastor_publication_requested_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="publication_requested_sermons",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
