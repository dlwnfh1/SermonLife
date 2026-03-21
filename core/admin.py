from django.contrib import admin, messages
from django.utils import timezone

from .models import (
    MissionCompletion,
    PointLedger,
    QuizAttempt,
    Sermon,
    SermonMission,
    SermonQuiz,
    SermonStatus,
    SermonSummary,
    UserProfile,
    WeeklyChallenge,
)
from .services.ai_generation import AIContentGenerationError, generate_sermon_content


class SermonSummaryInline(admin.StackedInline):
    model = SermonSummary
    extra = 0


class SermonQuizInline(admin.TabularInline):
    model = SermonQuiz
    extra = 0


class SermonMissionInline(admin.TabularInline):
    model = SermonMission
    extra = 0


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
    updated = queryset.update(status=SermonStatus.APPROVED)
    modeladmin.message_user(
        request,
        f"{updated} sermon(s) approved.",
        level=messages.SUCCESS,
    )


@admin.action(description="Publish selected sermons")
def publish_sermons(modeladmin, request, queryset):
    updated = queryset.update(
        status=SermonStatus.PUBLISHED,
        is_published=True,
        published_at=timezone.now(),
    )
    modeladmin.message_user(
        request,
        f"{updated} sermon(s) published.",
        level=messages.SUCCESS,
    )


@admin.action(description="Activate selected weekly challenge")
def activate_weekly_challenges(modeladmin, request, queryset):
    selected_ids = list(queryset.values_list("id", flat=True))
    WeeklyChallenge.objects.exclude(id__in=selected_ids).update(is_active=False)
    updated = queryset.update(is_active=True)
    modeladmin.message_user(
        request,
        f"{updated} weekly challenge(s) activated.",
        level=messages.SUCCESS,
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
    list_filter = ("status", "ai_generated", "is_published", "sermon_date")
    search_fields = ("title", "preacher", "bible_passage", "transcript")
    date_hierarchy = "sermon_date"
    inlines = [SermonSummaryInline, SermonQuizInline, SermonMissionInline]
    actions = [generate_ai_content, mark_ai_generated, approve_sermons, publish_sermons]
    readonly_fields = ("last_imported_at", "last_ai_generated_at", "import_error", "ai_error")


@admin.register(WeeklyChallenge)
class WeeklyChallengeAdmin(admin.ModelAdmin):
    list_display = ("title", "sermon", "week_start", "week_end", "is_active")
    list_filter = ("is_active", "week_start")
    search_fields = ("title", "sermon__title")
    actions = [activate_weekly_challenges]


@admin.register(SermonQuiz)
class SermonQuizAdmin(admin.ModelAdmin):
    list_display = ("question", "sermon", "order", "approved", "ai_generated")
    list_filter = ("approved", "ai_generated")
    search_fields = ("question", "sermon__title")


@admin.register(SermonMission)
class SermonMissionAdmin(admin.ModelAdmin):
    list_display = ("title", "sermon", "order", "approved", "ai_generated")
    list_filter = ("approved", "ai_generated")
    search_fields = ("title", "description", "sermon__title")


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "church_group", "points", "streak_days")
    search_fields = ("user__username", "church_group")


@admin.register(QuizAttempt)
class QuizAttemptAdmin(admin.ModelAdmin):
    list_display = ("user", "sermon", "quiz", "is_correct", "created_at")
    list_filter = ("is_correct", "created_at")
    search_fields = ("user__username", "sermon__title", "quiz__question")


@admin.register(MissionCompletion)
class MissionCompletionAdmin(admin.ModelAdmin):
    list_display = ("user", "sermon", "mission", "completed", "completed_at")
    list_filter = ("completed", "completed_at")
    search_fields = ("user__username", "sermon__title", "mission__title")


@admin.register(PointLedger)
class PointLedgerAdmin(admin.ModelAdmin):
    list_display = ("user", "challenge", "source", "points", "created_at")
    list_filter = ("source", "created_at")
    search_fields = ("user__username", "challenge__title", "sermon__title", "note")
