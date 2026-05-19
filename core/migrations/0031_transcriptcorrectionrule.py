from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0030_multichurch_admin_extensions"),
    ]

    operations = [
        migrations.CreateModel(
            name="TranscriptCorrectionRule",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("source_text", models.CharField(max_length=255, unique=True)),
                ("replacement_text", models.CharField(max_length=255)),
                ("sort_order", models.PositiveIntegerField(default=100)),
                ("is_active", models.BooleanField(default=True)),
                ("note", models.CharField(blank=True, max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "전사 교정 사전",
                "verbose_name_plural": "전사 교정 사전",
                "ordering": ["sort_order", "source_text", "id"],
            },
        ),
    ]
