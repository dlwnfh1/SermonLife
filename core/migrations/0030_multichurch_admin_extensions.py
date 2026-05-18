from django.db import migrations, models
import django.db.models.deletion


def assign_default_church(apps, schema_editor):
    Church = apps.get_model("core", "Church")
    SourceMediaAsset = apps.get_model("core", "SourceMediaAsset")
    PastorNotificationRecipient = apps.get_model("core", "PastorNotificationRecipient")

    default_church = Church.objects.order_by("-is_default", "id").first()
    if not default_church:
        return

    SourceMediaAsset.objects.filter(church__isnull=True).update(church=default_church)
    PastorNotificationRecipient.objects.filter(church__isnull=True).update(church=default_church)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0029_multichurch_foundation"),
    ]

    operations = [
        migrations.AddField(
            model_name="pastornotificationrecipient",
            name="church",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="pastor_notification_recipients", to="core.church"),
        ),
        migrations.AddField(
            model_name="sourcemediaasset",
            name="church",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="source_media_assets", to="core.church"),
        ),
        migrations.AlterField(
            model_name="pastornotificationrecipient",
            name="email",
            field=models.EmailField(max_length=254),
        ),
        migrations.RunPython(assign_default_church, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="pastornotificationrecipient",
            constraint=models.UniqueConstraint(fields=("church", "email"), name="unique_pastor_notification_email_per_church"),
        ),
    ]
