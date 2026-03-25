from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="ReportMenu",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(default="Reports", max_length=100)),
            ],
            options={
                "verbose_name": "Report Hub",
                "verbose_name_plural": "Report Hub",
            },
        ),
    ]
