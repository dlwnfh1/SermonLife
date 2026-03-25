from django import forms
from django.contrib import admin, messages
from django.db import models as dj_models
from django.utils import timezone

from .models import (
    DailyEngagement,
    PointLedger,
    Sermon,
    SermonStatus,
    SermonSummary,
    UserProfile,
)
from .services.ai_generation import AIContentGenerationError, generate_sermon_content


class SermonSummaryInline(admin.StackedInline):
    model = SermonSummary
    extra = 0
    formfield_overrides = {
        dj_models.TextField: {
            "widget": forms.Textarea(
                attrs={
                    "rows": 6,
                    "cols": 140,
                    "style": "min-height: 140px; width: 100%; max-width: none;",
                }
            )
        },
    }


class DailyEngagementInline(admin.StackedInline):
    model = DailyEngagement
    extra = 0
    fk_name = "sermon"
    fields = (
        ("day_number", "approved", "ai_generated"),
        "title",
        "intro",
        ("quiz_question", "quiz_answer"),
        ("quiz_choice1", "quiz_choice2"),
        ("quiz_choice3", "quiz_choice4"),
        "quiz_explanation",
        "reflection_question",
        "mission_title",
        "mission_description",
    )
    formfield_overrides = {
        dj_models.TextField: {
            "widget": forms.Textarea(
                attrs={
                    "rows": 4,
                    "cols": 140,
                    "style": "min-height: 96px; width: 100%; max-width: none;",
                }
            )
        },
    }


@admin.action(description="Mark selected sermons as AI generated")
def mark_ai_generated(modeladmin, request, queryset):
    updated = queryset.update(ai_generated=True, status=SermonStatus.GENERATED)
    modeladmin.message_user(
        request,
        f"{updated} sermon(s) marked as AI generated.",
        level=messages.SUCCESS,
    )


@admin.action(description="Generate AI content for selected sermons")
def generate_ai_content(modeladmin, request, queryset):
    generated_count = 0
    failures = []
    for sermon in queryset:
        try:
            generate_sermon_content(sermon)
            generated_count += 1
        except AIContentGenerationError as exc:
            failures.append(f"{sermon.title}: {exc}")

    if generated_count:
        modeladmin.message_user(
            request,
            f"{generated_count} sermon(s) generated with AI draft content.",
            level=messages.SUCCESS,
        )
    for failure in failures[:5]:
        modeladmin.message_user(request, failure, level=messages.ERROR)


@admin.action(description="Approve selected sermons")
def approve_sermons(modeladmin, request, queryset):
    updated = 0
    for sermon in queryset:
        sermon.approve_generated_content()
        updated += 1
    modeladmin.message_user(
        request,
        f"{updated} sermon(s) and related content approved.",
        level=messages.SUCCESS,
    )


@admin.action(description="Publish selected sermons")
def publish_sermons(modeladmin, request, queryset):
    from reports.services import (
        sync_content_quality_report,
        sync_daily_action_report,
        sync_sermon_participation_report,
        sync_weekly_participation_report,
    )

    updated = 0
    latest_published = None
    for sermon in queryset.order_by("sermon_date", "id"):
        sermon.publish()
        challenge = sermon.weekly_challenges.order_by("-week_start", "-id").first()
        if challenge:
            sync_content_quality_report(challenge)
            sync_weekly_participation_report(challenge)
            sync_daily_action_report(challenge)
        sync_sermon_participation_report(sermon)
        updated += 1
        latest_published = sermon

    modeladmin.message_user(
        request,
        f"{updated} sermon(s) published.",
        level=messages.SUCCESS,
    )
    if latest_published and latest_published.weekly_challenges.exists():
        modeladmin.message_user(
            request,
            f"Activated weekly challenge for '{latest_published.title}'.",
            level=messages.INFO,
        )


@admin.register(Sermon)
class SermonAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "sermon_date",
        "preacher",
        "status",
        "ai_generated",
        "is_published",
        "last_imported_at",
        "last_ai_generated_at",
    )
    search_fields = ("title", "preacher", "bible_passage", "transcript")
    date_hierarchy = "sermon_date"
    inlines = [SermonSummaryInline, DailyEngagementInline]
    actions = [generate_ai_content, mark_ai_generated, approve_sermons, publish_sermons]
    readonly_fields = ("last_imported_at", "last_ai_generated_at", "import_error", "ai_error")

    class Media:
        css = {"all": ("core/admin-compact.css",)}
        js = ("core/admin-inline-toggle.js",)

    def save_model(self, request, obj, form, change):
        from reports.services import (
            sync_content_quality_report,
            sync_daily_action_report,
            sync_sermon_participation_report,
            sync_weekly_participation_report,
        )

        publish_requested = obj.is_published or obj.status == SermonStatus.PUBLISHED
        approve_requested = obj.status == SermonStatus.APPROVED

        if publish_requested:
            obj.is_published = True
            obj.status = SermonStatus.PUBLISHED
            if obj.published_at is None:
                obj.published_at = timezone.now()
        super().save_model(request, obj, form, change)
        if publish_requested:
            obj.publish()
            challenge = obj.weekly_challenges.order_by("-week_start", "-id").first()
            if challenge:
                sync_content_quality_report(challenge)
                sync_weekly_participation_report(challenge)
                sync_daily_action_report(challenge)
            sync_sermon_participation_report(obj)
        elif approve_requested:
            obj.approve_generated_content()

@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "member_role", "points", "streak_days")
    search_fields = ("user__username", "member_role")


@admin.register(PointLedger)
class PointLedgerAdmin(admin.ModelAdmin):
    list_display = ("user", "challenge", "source", "points", "created_at")
    search_fields = ("user__username", "challenge__title", "sermon__title", "note")
