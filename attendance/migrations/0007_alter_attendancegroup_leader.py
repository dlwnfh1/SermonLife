from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0006_clear_group_guide_values"),
    ]

    operations = [
        migrations.AlterField(
            model_name="attendancegroup",
            name="leader",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="led_attendance_groups",
                to="attendance.attendancemember",
                verbose_name="속장",
            ),
        ),
    ]
