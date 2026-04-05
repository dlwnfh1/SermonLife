from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0012_sermon_source_media_path"),
    ]

    operations = [
        migrations.CreateModel(
            name="SourceMediaAsset",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("file", models.FileField(upload_to="sermons/source/")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "verbose_name": "원본 파일",
                "verbose_name_plural": "원본 파일",
                "ordering": ["-created_at", "-id"],
            },
        ),
        migrations.AddField(
            model_name="sermon",
            name="source_media_asset",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="sermons", to="core.sourcemediaasset"),
        ),
    ]
