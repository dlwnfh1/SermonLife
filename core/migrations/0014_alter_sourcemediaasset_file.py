import core.models
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0013_sourcemediaasset_sermon_source_media_asset"),
    ]

    operations = [
        migrations.AlterField(
            model_name="sourcemediaasset",
            name="file",
            field=models.FileField(upload_to=core.models.source_media_upload_to),
        ),
    ]
