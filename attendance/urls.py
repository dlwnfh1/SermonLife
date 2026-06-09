from django.urls import path

from .views import (
    attendance_check_view,
    attendance_check_qr_print_view,
    attendance_check_qr_svg_view,
    attendance_dashboard_view,
    attendance_district_manage_view,
    attendance_force_open_toggle_view,
    attendance_group_manage_view,
    attendance_manage_view,
    attendance_manual_check_view,
    attendance_report_hub_view,
    attendance_reports_view,
    attendance_pwa_manifest_view,
    attendance_seed_demo_view,
    attendance_service_worker_view,
    attendance_weekly_pdf_email_view,
    attendance_weekly_pdf_view,
)

app_name = "attendance"

urlpatterns = [
    path("", attendance_dashboard_view, name="dashboard"),
    path("check/", attendance_check_view, name="check"),
    path("manual-check/", attendance_manual_check_view, name="manual_check"),
    path("check/qr.svg", attendance_check_qr_svg_view, name="check_qr_svg"),
    path("check/qr-print/", attendance_check_qr_print_view, name="check_qr_print"),
    path("manifest.json", attendance_pwa_manifest_view, name="pwa_manifest"),
    path("sw.js", attendance_service_worker_view, name="service_worker"),
    path("force-open-toggle/", attendance_force_open_toggle_view, name="force_open_toggle"),
    path("seed-demo/", attendance_seed_demo_view, name="seed_demo"),
    path("reports/", attendance_reports_view, name="reports"),
    path("report-hub/", attendance_report_hub_view, name="report_hub"),
    path("report-hub/weekly-pdf/", attendance_weekly_pdf_view, name="weekly_pdf"),
    path("report-hub/weekly-pdf-email/", attendance_weekly_pdf_email_view, name="weekly_pdf_email"),
    path("manage/", attendance_manage_view, name="manage"),
    path("manage/district/<int:district_id>/", attendance_district_manage_view, name="manage_district"),
    path("manage/group/<int:group_id>/", attendance_group_manage_view, name="manage_group"),
]
