from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0008_attendancegroup_attendance_login_user"),
    ]

    operations = [
        migrations.AddField(
            model_name="attendancegroup",
            name="attendance_pin",
            field=models.CharField(
                blank=True,
                help_text="5자리 숫자 PIN",
                max_length=5,
                verbose_name="출석 PIN",
            ),
        ),
    ]
