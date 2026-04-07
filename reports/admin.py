from django.contrib import admin

from .models import ContentQualityReport, DailyActionReport, SermonParticipationReport, UserParticipationReport, WeeklyParticipationReport
from .services import (
    sync_all_content_quality_reports,
    sync_all_daily_action_reports,
    sync_all_sermon_participation_reports,
    sync_all_user_participation_reports,
    sync_all_weekly_participation_reports,
    sync_content_quality_report,
    sync_daily_action_report,
    sync_sermon_participation_report,
    sync_user_participation_report,
    sync_weekly_participation_report,
)


class BaseReportAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        # Allow related report rows to be deleted when a sermon is deleted.
        return request.user.is_active and request.user.is_staff


@admin.register(WeeklyParticipationReport)
class WeeklyParticipationReportAdmin(BaseReportAdmin):
    list_display = ("title", "week_start", "week_end", "participant_count", "total_points", "generated_at")
    search_fields = ("title", "sermon__title", "challenge__title")
    readonly_fields = (
        "challenge", "sermon", "title", "week_start", "week_end", "participant_count", "total_points",
        "most_completed_day_label", "most_completed_day_count", "most_completed_day_rate",
        "least_completed_day_label", "least_completed_day_count", "least_completed_day_rate",
        "generated_at", "created_at",
    )
    change_list_template = "admin/reports/weeklyparticipationreport/change_list.html"
    change_form_template = "admin/reports/weeklyparticipationreport/change_form.html"

    def changelist_view(self, request, extra_context=None):
        sync_all_weekly_participation_reports()
        extra_context = extra_context or {}
        extra_context["report_description"] = "공개된 주간 챌린지별 참여 현황을 자동으로 모아 보여줍니다. 새 설교가 Publish 되면 새 리포트가 누적됩니다."
        return super().changelist_view(request, extra_context=extra_context)

    def change_view(self, request, object_id, form_url="", extra_context=None):
        obj = self.get_object(request, object_id)
        if obj is not None and obj.challenge:
            obj = sync_weekly_participation_report(obj.challenge)
        extra_context = extra_context or {}
        extra_context["report"] = obj
        extra_context["title"] = obj.title if obj else "주간 참여 리포트"
        return super().change_view(request, object_id, form_url, extra_context=extra_context)


@admin.register(SermonParticipationReport)
class SermonParticipationReportAdmin(BaseReportAdmin):
    list_display = ("title", "sermon_date", "participant_count", "average_points_per_participant", "weekly_completer_count", "generated_at")
    search_fields = ("title", "sermon__title")
    readonly_fields = (
        "sermon", "primary_challenge", "title", "sermon_date", "participant_count", "total_points",
        "average_points_per_participant", "quiz_participant_count", "reflection_participant_count",
        "mission_participant_count", "weekly_completer_count", "weekly_completion_rate",
        "generated_at", "created_at",
    )
    change_list_template = "admin/reports/sermonparticipationreport/change_list.html"
    change_form_template = "admin/reports/sermonparticipationreport/change_form.html"

    def changelist_view(self, request, extra_context=None):
        sync_all_sermon_participation_reports()
        extra_context = extra_context or {}
        extra_context["report_description"] = "공개된 설교별로 전체 참여 인원, 평균 점수, 퀴즈/묵상/미션 참여율과 5일 완주율을 누적해서 보여줍니다."
        return super().changelist_view(request, extra_context=extra_context)

    def change_view(self, request, object_id, form_url="", extra_context=None):
        obj = self.get_object(request, object_id)
        if obj is not None and obj.sermon:
            obj = sync_sermon_participation_report(obj.sermon)
        extra_context = extra_context or {}
        extra_context["report"] = obj
        extra_context["title"] = obj.title if obj else "설교별 참여 리포트"
        return super().change_view(request, object_id, form_url, extra_context=extra_context)


@admin.register(DailyActionReport)
class DailyActionReportAdmin(BaseReportAdmin):
    list_display = ("title", "week_start", "week_end", "participant_count", "generated_at")
    search_fields = ("title", "sermon__title", "challenge__title")
    readonly_fields = (
        "challenge", "sermon", "title", "week_start", "week_end", "participant_count",
        "strongest_day_label", "weakest_day_label", "generated_at", "created_at",
    )
    change_list_template = "admin/reports/dailyactionreport/change_list.html"
    change_form_template = "admin/reports/dailyactionreport/change_form.html"

    def changelist_view(self, request, extra_context=None):
        sync_all_daily_action_reports()
        extra_context = extra_context or {}
        extra_context["report_description"] = "주차 안에서 Day 1~5별로 퀴즈 제출, 묵상 저장, 미션 완료, 일일 보너스 획득이 어떻게 분포되는지 보여줍니다."
        return super().changelist_view(request, extra_context=extra_context)

    def change_view(self, request, object_id, form_url="", extra_context=None):
        obj = self.get_object(request, object_id)
        if obj is not None and obj.challenge:
            obj = sync_daily_action_report(obj.challenge)
        extra_context = extra_context or {}
        extra_context["report"] = obj
        extra_context["title"] = obj.title if obj else "일자별 행동 리포트"
        return super().change_view(request, object_id, form_url, extra_context=extra_context)


@admin.register(UserParticipationReport)
class UserParticipationReportAdmin(BaseReportAdmin):
    list_display = ("username", "display_name", "member_role", "total_points", "weekly_completer_count", "active_this_week", "last_activity_at")
    list_filter = ("member_role", "active_this_week", "recent_two_week_streak", "inactive_for_two_weeks")
    search_fields = ("username", "display_name")
    readonly_fields = (
        "user", "username", "display_name", "member_role", "total_points", "streak_days",
        "weekly_completer_count", "active_this_week", "recent_two_week_streak",
        "inactive_for_two_weeks", "last_activity_at", "generated_at", "created_at",
    )
    change_list_template = "admin/reports/userparticipationreport/change_list.html"
    change_form_template = "admin/reports/userparticipationreport/change_form.html"

    def changelist_view(self, request, extra_context=None):
        sync_all_user_participation_reports()
        extra_context = extra_context or {}
        extra_context["report_description"] = "사용자별 총점, 최근 활동, 5일 완주 횟수, 현재 주차 참여 여부를 모아 보여줍니다."
        return super().changelist_view(request, extra_context=extra_context)

    def change_view(self, request, object_id, form_url="", extra_context=None):
        obj = self.get_object(request, object_id)
        if obj is not None and obj.user:
            obj = sync_user_participation_report(obj.user)
        extra_context = extra_context or {}
        extra_context["report"] = obj
        extra_context["title"] = obj.username if obj else "사용자 참여 리포트"
        return super().change_view(request, object_id, form_url, extra_context=extra_context)


@admin.register(ContentQualityReport)
class ContentQualityReportAdmin(BaseReportAdmin):
    list_display = ("title", "week_start", "week_end", "participant_count", "issue_count", "generated_at")
    search_fields = ("title", "sermon__title", "challenge__title")
    readonly_fields = (
        "challenge", "sermon", "title", "week_start", "week_end", "participant_count",
        "lowest_quiz_accuracy_label", "lowest_quiz_accuracy_rate",
        "lowest_reflection_label", "lowest_reflection_rate",
        "lowest_mission_label", "lowest_mission_rate",
        "issue_count", "generated_at", "created_at",
    )
    change_list_template = "admin/reports/contentqualityreport/change_list.html"
    change_form_template = "admin/reports/contentqualityreport/change_form.html"

    def changelist_view(self, request, extra_context=None):
        sync_all_content_quality_reports()
        extra_context = extra_context or {}
        extra_context["report_description"] = "각 Day의 퀴즈 정답률, 묵상 반응, 미션 반응을 비교해 품질 점검이 필요한 콘텐츠를 보여줍니다."
        return super().changelist_view(request, extra_context=extra_context)

    def change_view(self, request, object_id, form_url="", extra_context=None):
        obj = self.get_object(request, object_id)
        if obj is not None and obj.challenge:
            obj = sync_content_quality_report(obj.challenge)
        extra_context = extra_context or {}
        extra_context["report"] = obj
        extra_context["title"] = obj.title if obj else "콘텐츠 품질 리포트"
        return super().change_view(request, object_id, form_url, extra_context=extra_context)
