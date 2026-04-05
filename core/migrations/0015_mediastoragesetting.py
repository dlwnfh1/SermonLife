from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0014_alter_sourcemediaasset_file"),
    ]

    operations = [
        migrations.CreateModel(
            name="MediaStorageSetting",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("source_media_subdir", models.CharField(default="sermons", max_length=120)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "파일 저장 위치",
                "verbose_name_plural": "파일 저장 위치",
            },
        ),
    ]
