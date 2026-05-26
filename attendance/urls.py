from django.urls import path

from .views import (
    attendance_check_view,
    attendance_dashboard_view,
    attendance_district_manage_view,
    attendance_force_open_toggle_view,
    attendance_group_manage_view,
    attendance_manage_view,
    attendance_reports_view,
)

app_name = "attendance"

urlpatterns = [
    path("", attendance_dashboard_view, name="dashboard"),
    path("check/", attendance_check_view, name="check"),
    path("force-open-toggle/", attendance_force_open_toggle_view, name="force_open_toggle"),
    path("reports/", attendance_reports_view, name="reports"),
    path("manage/", attendance_manage_view, name="manage"),
    path("manage/district/<int:district_id>/", attendance_district_manage_view, name="manage_district"),
    path("manage/group/<int:group_id>/", attendance_group_manage_view, name="manage_group"),
]
