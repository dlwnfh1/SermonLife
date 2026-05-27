from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0003_attendancecontrol"),
    ]

    operations = [
        migrations.AddField(
            model_name="attendancegroup",
            name="guide",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="guided_groups",
                to="attendance.attendancemember",
                verbose_name="인도자",
            ),
        ),
    ]
