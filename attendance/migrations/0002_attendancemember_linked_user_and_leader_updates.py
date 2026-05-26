from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def copy_district_leader_names(apps, schema_editor):
    AttendanceDistrictLeader = apps.get_model("attendance", "AttendanceDistrictLeader")
    for leader in AttendanceDistrictLeader.objects.select_related("linked_user").all():
        if leader.linked_user_id and not leader.name:
            full_name = getattr(leader.linked_user, "get_full_name", lambda: "")()
            leader.name = (full_name or getattr(leader.linked_user, "username", "")).strip()
            leader.save(update_fields=["name"])


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("attendance", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="attendancemember",
            name="linked_user",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="attendance_memberships",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RenameField(
            model_name="attendancedistrictleader",
            old_name="user",
            new_name="linked_user",
        ),
        migrations.AddField(
            model_name="attendancedistrictleader",
            name="name",
            field=models.CharField(default="", max_length=120),
            preserve_default=False,
        ),
        migrations.RunPython(copy_district_leader_names, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="attendancedistrictleader",
            name="linked_user",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="attendance_district_roles",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterModelOptions(
            name="attendancedistrictleader",
            options={
                "ordering": ["-is_primary", "name", "id"],
                "verbose_name": "교구장",
                "verbose_name_plural": "교구장",
            },
        ),
        migrations.AlterUniqueTogether(
            name="attendancedistrictleader",
            unique_together={("district", "name")},
        ),
        migrations.AlterField(
            model_name="attendancegroup",
            name="leader",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="led_attendance_groups",
                to="attendance.attendancemember",
            ),
        ),
    ]
