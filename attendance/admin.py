from django.contrib import admin
from django.db.models import Count

from .models import (
    AttendanceControl,
    AttendanceDistrict,
    AttendanceDistrictLeader,
    AttendanceGroup,
    AttendanceMember,
    AttendanceRecord,
    AttendanceSession,
)


@admin.register(AttendanceControl)
class AttendanceControlAdmin(admin.ModelAdmin):
    list_display = ("church", "force_open", "updated_by", "updated_at")
    list_filter = ("force_open", "church")
    autocomplete_fields = ("updated_by",)


class AttendanceDistrictLeaderInline(admin.TabularInline):
    model = AttendanceDistrictLeader
    extra = 1
    autocomplete_fields = ("linked_user",)


@admin.register(AttendanceDistrict)
class AttendanceDistrictAdmin(admin.ModelAdmin):
    list_display = ("name", "church", "leader_names", "is_active", "sort_order", "group_count", "updated_at")
    list_filter = ("church", "is_active")
    search_fields = ("name", "church__name")
    list_editable = ("is_active", "sort_order")
    inlines = [AttendanceDistrictLeaderInline]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(group_total=Count("groups", distinct=True))

    @admin.display(description="속 수")
    def group_count(self, obj):
        return obj.group_total

    @admin.display(description="교구장")
    def leader_names(self, obj):
        return ", ".join(obj.leaders.values_list("name", flat=True)) or "-"


@admin.register(AttendanceGroup)
class AttendanceGroupAdmin(admin.ModelAdmin):
    list_display = ("name", "church", "district", "group_leader", "is_active", "sort_order", "member_count")
    list_filter = ("church", "district", "is_active")
    search_fields = ("name", "district__name", "leader__name", "church__name")
    list_editable = ("is_active", "sort_order")
    autocomplete_fields = ("leader",)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("church", "district", "leader").annotate(
            member_total=Count("members", distinct=True)
        )

    @admin.display(description="속장", ordering="leader__name")
    def group_leader(self, obj):
        return obj.leader.name if obj.leader else "-"

    @admin.display(description="속원 수")
    def member_count(self, obj):
        return obj.member_total


@admin.register(AttendanceMember)
class AttendanceMemberAdmin(admin.ModelAdmin):
    list_display = ("name", "church", "group", "linked_user", "phone", "is_active", "sort_order", "updated_at")
    list_filter = ("church", "group__district", "group", "is_active")
    search_fields = ("name", "phone", "group__name", "group__district__name", "church__name")
    list_editable = ("is_active", "sort_order")
    autocomplete_fields = ("linked_user",)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("church", "group", "group__district", "linked_user")


class AttendanceRecordInline(admin.TabularInline):
    model = AttendanceRecord
    extra = 0
    autocomplete_fields = ("member", "marked_by")


@admin.register(AttendanceSession)
class AttendanceSessionAdmin(admin.ModelAdmin):
    list_display = ("title", "church", "worship_date", "is_locked", "record_count", "updated_at")
    list_filter = ("church", "is_locked", "worship_date")
    search_fields = ("title", "church__name")
    list_editable = ("is_locked",)
    inlines = [AttendanceRecordInline]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("church").annotate(record_total=Count("records", distinct=True))

    @admin.display(description="기록 수")
    def record_count(self, obj):
        return obj.record_total


@admin.register(AttendanceRecord)
class AttendanceRecordAdmin(admin.ModelAdmin):
    list_display = ("session", "member", "status", "marked_by", "marked_at")
    list_filter = ("status", "session__church", "session__worship_date", "member__group__district", "member__group")
    search_fields = ("member__name", "member__group__name", "session__title", "note")
    autocomplete_fields = ("member", "marked_by", "session")
