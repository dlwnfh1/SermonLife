from django.db import migrations


def clear_group_guides(apps, schema_editor):
    AttendanceGroup = apps.get_model("attendance", "AttendanceGroup")
    AttendanceGroup.objects.exclude(guide_id=None).update(guide_id=None)


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0005_alter_attendancegroup_guide"),
    ]

    operations = [
        migrations.RunPython(clear_group_guides, migrations.RunPython.noop),
    ]
