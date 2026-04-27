from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0020_sermon_bible_text"),
    ]

    operations = [
        migrations.AddField(
            model_name="sermon",
            name="pastor_review_requested",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="sermon",
            name="pastor_review_requested_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="PastorNotificationRecipient",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(blank=True, max_length=100)),
                ("email", models.EmailField(max_length=254, unique=True)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "목회자 공지 수신자",
                "verbose_name_plural": "목회자 공지 수신자",
                "ordering": ["name", "email"],
            },
        ),
    ]
