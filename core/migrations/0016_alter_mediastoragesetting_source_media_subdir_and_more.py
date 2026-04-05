from django.db import migrations, models


def ensure_single_media_storage_setting(apps, schema_editor):
    MediaStorageSetting = apps.get_model("core", "MediaStorageSetting")
    settings = list(MediaStorageSetting.objects.order_by("id"))
    if not settings:
        MediaStorageSetting.objects.create(source_media_subdir="sermons")
        return

    primary = settings[0]
    normalized = (primary.source_media_subdir or "").replace("\\", "/").strip().strip("/") or "sermons"
    if primary.source_media_subdir != normalized:
        primary.source_media_subdir = normalized
        primary.save(update_fields=["source_media_subdir", "updated_at"])

    for extra in settings[1:]:
        extra.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0015_mediastoragesetting"),
    ]

    operations = [
        migrations.AlterField(
            model_name="mediastoragesetting",
            name="source_media_subdir",
            field=models.CharField(
                default="sermons",
                help_text="uploads 아래에서 사용할 폴더 경로입니다. 예: sermons 또는 sermons/2026/april",
                max_length=255,
            ),
        ),
        migrations.RunPython(ensure_single_media_storage_setting, migrations.RunPython.noop),
    ]
