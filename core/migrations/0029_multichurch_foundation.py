from django.db import migrations, models
import django.db.models.deletion


def seed_churches_and_assign_fgmc(apps, schema_editor):
    Church = apps.get_model("core", "Church")
    Sermon = apps.get_model("core", "Sermon")
    UserProfile = apps.get_model("core", "UserProfile")

    fgmc, _ = Church.objects.get_or_create(
        slug="fgmc",
        defaults={"name": "FGMC", "is_default": True},
    )
    Church.objects.filter(pk=fgmc.pk).update(is_default=True)
    Church.objects.exclude(pk=fgmc.pk).filter(is_default=True).update(is_default=False)

    Church.objects.get_or_create(
        slug="bcpc",
        defaults={"name": "BCPC", "is_default": False},
    )

    Sermon.objects.filter(church__isnull=True).update(church=fgmc)
    UserProfile.objects.filter(church__isnull=True).update(church=fgmc)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0028_prayerrequest_scripture_recommendations"),
    ]

    operations = [
        migrations.CreateModel(
            name="Church",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120, unique=True)),
                ("slug", models.SlugField(max_length=40, unique=True)),
                ("is_default", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "교회",
                "verbose_name_plural": "교회",
                "ordering": ["name", "id"],
            },
        ),
        migrations.AddField(
            model_name="sermon",
            name="church",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="sermons", to="core.church"),
        ),
        migrations.AddField(
            model_name="userprofile",
            name="church",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="user_profiles", to="core.church"),
        ),
        migrations.RunPython(seed_churches_and_assign_fgmc, migrations.RunPython.noop),
    ]
