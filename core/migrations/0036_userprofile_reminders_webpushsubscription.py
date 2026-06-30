from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0035_userprofile_attendance_only_mode"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="reminder_enabled",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="reminder_hour",
            field=models.PositiveSmallIntegerField(
                choices=[
                    (8, "오전 8시"),
                    (9, "오전 9시"),
                    (10, "오전 10시"),
                    (11, "오전 11시"),
                    (12, "오후 12시"),
                    (13, "오후 1시"),
                    (14, "오후 2시"),
                    (15, "오후 3시"),
                    (16, "오후 4시"),
                    (17, "오후 5시"),
                    (18, "오후 6시"),
                    (19, "오후 7시"),
                    (20, "오후 8시"),
                    (21, "오후 9시"),
                    (22, "오후 10시"),
                ],
                default=19,
            ),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="reminder_last_sent_on",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="WebPushSubscription",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("endpoint", models.TextField(unique=True)),
                ("auth_key", models.CharField(max_length=255)),
                ("p256dh_key", models.CharField(max_length=255)),
                ("expiration_time", models.BigIntegerField(blank=True, null=True)),
                ("user_agent", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("last_seen_at", models.DateTimeField(auto_now=True)),
                (
                    "church",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="web_push_subscriptions",
                        to="core.church",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="web_push_subscriptions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["-updated_at", "-id"]},
        ),
    ]
