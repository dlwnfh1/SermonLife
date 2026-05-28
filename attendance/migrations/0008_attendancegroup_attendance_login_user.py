from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("attendance", "0007_alter_attendancegroup_leader"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="attendancegroup",
            name="attendance_login_user",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.SET_NULL,
                related_name="attendance_login_groups",
                to=settings.AUTH_USER_MODEL,
                verbose_name="출석 전용 로그인",
            ),
        ),
    ]
