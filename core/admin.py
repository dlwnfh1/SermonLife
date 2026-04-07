from datetime import timedelta
from pathlib import Path
from time import perf_counter

from django import forms
from django.contrib import admin, messages
from django.db import models as dj_models
from django.http import HttpResponseRedirect
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html
from django.conf import settings

from .models import (
    DailyEngagement,
    get_source_media_root,
    MediaStorageSetting,
    PointLedger,
    SourceMediaAsset,
    Sermon,
    SermonStatus,
    SermonSummary,
    UserProfile,
)
from .services.ai_generation import AIContentGenerationError, generate_sermon_content
from .services.transcript_service import (
    TranscriptFetchError,
    transcribe_audio_file,
)

admin.site.site_header = "SERMON LIFE 관리하기"
admin.site.site_title = "SERMON LIFE 관리하기"
admin.site.index_title = "SERMON LIFE 관리하기"
PointLedger._meta.verbose_name = "달란트 내역"
PointLedger._meta.verbose_name_plural = "달란트 내역"


def _clean_sermon_title_from_filename(value):
    if not value:
        return ""
    cleaned = Path(value).stem if "." in str(value) else str(value)
    return " ".join(cleaned.replace("_", " ").split())

class SermonSummaryInline(admin.StackedInline):
    model = SermonSummary
    extra = 0
    exclude = ("ai_generated", "approved", "updated_at")
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
        ("day_number",),
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
    exclude = ("approved", "ai_generated", "created_at", "updated_at", "challenge")
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
            started_at = perf_counter()
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
    updated = 0
    latest_published = None
    for sermon in queryset.order_by("sermon_date", "id"):
        sermon.publish()
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


class SourceMediaAssetAdminForm(forms.ModelForm):
    class Meta:
        model = SourceMediaAsset
        fields = "__all__"

    def clean_file(self):
        uploaded = self.cleaned_data["file"]
        relative_name = f"{get_source_media_root().name}/{uploaded.name}"
        if SourceMediaAsset.objects.exclude(pk=self.instance.pk).filter(file=relative_name).exists():
            raise forms.ValidationError("같은 이름의 원본 파일이 이미 있습니다. 기존 파일을 선택하거나 먼저 삭제해 주세요.")
        if (Path(settings.MEDIA_ROOT) / relative_name).exists() and not self.instance.pk:
            raise forms.ValidationError("같은 이름의 파일이 업로드 폴더에 이미 있습니다. 기존 파일을 사용해 주세요.")
        return uploaded


class MediaStorageSettingAdminForm(forms.ModelForm):
    class Meta:
        model = MediaStorageSetting
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["source_media_subdir"].label = "원본 파일 폴더 경로"
        self.fields["source_media_subdir"].help_text = (
            "전체 경로가 아니라 uploads 아래 하위 폴더 경로를 입력합니다. 예: sermons 또는 sermons/2026/april"
        )


class SermonAdminForm(forms.ModelForm):
    transcript = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={
                "rows": 18,
                "cols": 140,
                "style": "min-height: 360px; width: 100%; max-width: none;",
            }
        ),
    )

    class Meta:
        model = Sermon
        fields = "__all__"


def sync_source_media_assets():
    root = get_source_media_root()
    root.mkdir(parents=True, exist_ok=True)
    relative_prefix = root.relative_to(Path(settings.MEDIA_ROOT)).as_posix()

    disk_relative_paths = set()
    for path in root.iterdir():
        if path.is_file():
            disk_relative_paths.add(f"{relative_prefix}/{path.name}")

    existing_assets = {asset.file.name: asset for asset in SourceMediaAsset.objects.all()}

    for relative_path in sorted(disk_relative_paths):
        if relative_path not in existing_assets:
            SourceMediaAsset.objects.create(file=relative_path)

    for relative_path, asset in existing_assets.items():
        if relative_path not in disk_relative_paths:
            asset.delete()


@admin.register(SourceMediaAsset)
class SourceMediaAssetAdmin(admin.ModelAdmin):
    form = SourceMediaAssetAdminForm
    list_display = ("display_name", "file", "usage_status", "created_at", "delete_action")
    search_fields = ("file",)
    actions = ["delete_selected"]
    change_list_template = "admin/core/sourcemediaasset/change_list.html"
    ordering = ("file",)

    def changelist_view(self, request, extra_context=None):
        sync_source_media_assets()
        extra_context = extra_context or {}
        extra_context["source_media_root"] = str(get_source_media_root())
        return super().changelist_view(request, extra_context=extra_context)

    def delete_model(self, request, obj):
        file_path = Path(obj.file.path) if obj.file else None
        super().delete_model(request, obj)
        if file_path and file_path.exists():
            file_path.unlink()

    def delete_queryset(self, request, queryset):
        file_paths = [Path(obj.file.path) for obj in queryset if obj.file]
        super().delete_queryset(request, queryset)
        for file_path in file_paths:
            if file_path.exists():
                file_path.unlink()

    def display_name(self, obj):
        return _clean_sermon_title_from_filename(obj.file.name) or obj.file.name
    display_name.short_description = "파일 이름"


    def usage_status(self, obj):
        count = obj.sermons.count()
        if count:
            return f"사용 중 ({count})"
        return "미사용"
    usage_status.short_description = "사용 상태"

    def delete_action(self, obj):
        delete_url = reverse("admin:core_sourcemediaasset_delete", args=[obj.pk])
        return format_html('<a class="deletelink" href="{}">삭제</a>', delete_url)
    delete_action.short_description = "삭제"

@admin.register(MediaStorageSetting)
class MediaStorageSettingAdmin(admin.ModelAdmin):
    form = MediaStorageSettingAdminForm
    list_display = ("source_media_subdir", "updated_at")
    readonly_fields = ("effective_source_media_root",)
    fields = ("source_media_subdir", "effective_source_media_root")
    actions = None

    def has_add_permission(self, request):
        return MediaStorageSetting.objects.count() == 0

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        setting = MediaStorageSetting.objects.order_by("-id").first()
        if setting:
            return HttpResponseRedirect(reverse("admin:core_mediastoragesetting_change", args=[setting.pk]))
        return HttpResponseRedirect(reverse("admin:core_mediastoragesetting_add"))

    def response_add(self, request, obj, post_url_continue=None):
        self.message_user(
            request,
            f"원본 파일 폴더가 '{get_source_media_root()}'로 설정되었습니다.",
            level=messages.SUCCESS,
        )
        return HttpResponseRedirect(reverse("admin:core_mediastoragesetting_change", args=[obj.pk]))

    def response_change(self, request, obj):
        self.message_user(
            request,
            f"원본 파일 폴더가 '{get_source_media_root()}'로 변경되었습니다.",
            level=messages.SUCCESS,
        )
        return HttpResponseRedirect(reverse("admin:core_mediastoragesetting_change", args=[obj.pk]))

    def effective_source_media_root(self, obj):
        return str(get_source_media_root())

    effective_source_media_root.short_description = "실제 저장 경로"


@admin.register(Sermon)
class SermonAdmin(admin.ModelAdmin):
    form = SermonAdminForm
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
    actions = None
    readonly_fields = (
        "published_at",
        "last_imported_at",
        "last_ai_generated_at",
        "import_error",
        "ai_error",
    )
    change_form_template = "admin/core/sermon/change_form.html"
    fieldsets = (
        (
            "기본 정보",
            {
                "fields": (
                    ("title", "preacher"),
                    ("sermon_date", "bible_passage"),
                )
            },
        ),
        (
            "재생 및 원본 파일 선택",
            {
                "fields": (
                    "youtube_url",
                    "audio_file",
                    "source_media_asset",
                )
            },
        ),
        (
            "자막 및 원문",
            {
                "classes": ("collapse",),
                "fields": ("transcript",),
            },
        ),
    )
    formfield_overrides = {
        dj_models.TextField: {
            "widget": forms.Textarea(
                attrs={
                    "rows": 8,
                    "cols": 140,
                    "style": "min-height: 160px; width: 100%; max-width: none;",
                }
            )
        },
    }

    class Media:
        css = {"all": ("core/admin-compact.css",)}
        js = ("core/admin-inline-toggle.js", "core/admin-sermon-defaults.js")

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "source_media_asset":
            sync_source_media_assets()
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        today = timezone.localdate()
        days_since_sunday = (today.weekday() + 1) % 7
        if days_since_sunday == 0:
            days_since_sunday = 7
        initial.setdefault("sermon_date", today - timedelta(days=days_since_sunday))
        initial.setdefault("preacher", "Pastor Kim")
        return initial

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "<path:object_id>/regenerate-ai/",
                self.admin_site.admin_view(self.regenerate_ai_view),
                name="core_sermon_regenerate_ai",
            ),
            path(
                "<path:object_id>/transcribe-and-regenerate-ai/",
                self.admin_site.admin_view(self.transcribe_and_regenerate_ai_view),
                name="core_sermon_transcribe_and_regenerate_ai",
            ),
            path(
                "<path:object_id>/publish/",
                self.admin_site.admin_view(self.publish_single_view),
                name="core_sermon_publish",
            ),
            path(
                "<path:object_id>/unpublish/",
                self.admin_site.admin_view(self.unpublish_single_view),
                name="core_sermon_unpublish",
            ),
            path(
                "<path:object_id>/delete-source-media/",
                self.admin_site.admin_view(self.delete_source_media_view),
                name="core_sermon_delete_source_media",
            ),
        ]
        return custom_urls + urls

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        extra_context = extra_context or {}
        extra_context["show_prepare_button"] = True
        if object_id:
            extra_context["publish_url"] = reverse("admin:core_sermon_publish", args=[object_id])
            extra_context["unpublish_url"] = reverse("admin:core_sermon_unpublish", args=[object_id])
            extra_context["regenerate_ai_url"] = reverse("admin:core_sermon_regenerate_ai", args=[object_id])
            extra_context["transcribe_ai_url"] = reverse(
                "admin:core_sermon_transcribe_and_regenerate_ai",
                args=[object_id],
            )
            extra_context["delete_source_media_url"] = reverse(
                "admin:core_sermon_delete_source_media",
                args=[object_id],
            )
            extra_context["delete_url"] = reverse("admin:core_sermon_delete", args=[object_id])
        return super().changeform_view(request, object_id, form_url, extra_context=extra_context)

    def response_add(self, request, obj, post_url_continue=None):
        if "_save_and_prepare" in request.POST:
            if not obj.resolved_source_media_path:
                self.message_user(
                    request,
                    "먼저 '원본 파일'에서 설교 파일을 선택해 주세요.",
                    level=messages.WARNING,
                )
            else:
                self.message_user(
                    request,
                    "설교를 저장했습니다. 수정 화면에서 '설교 내용 정리'를 눌러 계속 진행해 주세요.",
                    level=messages.INFO,
                )
            return HttpResponseRedirect(reverse("admin:core_sermon_change", args=[obj.pk]))
        if "_save" in request.POST:
            self.message_user(
                request,
                "설교를 저장했습니다. 다음 단계로 '1. 설교 내용 정리'를 눌러 AI 초안을 먼저 만들어 주세요.",
                level=messages.SUCCESS,
            )
            return HttpResponseRedirect(reverse("admin:core_sermon_change", args=[obj.pk]))
        return super().response_add(request, obj, post_url_continue)

    def response_change(self, request, obj):
        if "_save" in request.POST:
            self.message_user(
                request,
                "수정 내용을 저장했습니다. 계속 검토하거나 바로 공개할 수 있습니다.",
                level=messages.SUCCESS,
            )
            return HttpResponseRedirect(reverse("admin:core_sermon_change", args=[obj.pk]))

        if "_save_and_transcribe" in request.POST:
            if not obj.resolved_source_media_path:
                self.message_user(
                    request,
                    "먼저 '원본 파일'에서 설교 파일을 선택해 주세요.",
                    level=messages.WARNING,
                )
                return HttpResponseRedirect(reverse("admin:core_sermon_change", args=[obj.pk]))
            return HttpResponseRedirect(
                reverse("admin:core_sermon_transcribe_and_regenerate_ai", args=[obj.pk])
            )

        if "_save_and_regenerate" in request.POST:
            return HttpResponseRedirect(
                reverse("admin:core_sermon_regenerate_ai", args=[obj.pk])
            )

        if "_save_and_publish" in request.POST:
            return HttpResponseRedirect(
                reverse("admin:core_sermon_publish", args=[obj.pk])
            )

        return super().response_change(request, obj)

    def regenerate_ai_view(self, request, object_id):
        sermon = self.get_object(request, object_id)
        if sermon is None:
            self.message_user(request, "설교를 찾을 수 없습니다.", level=messages.ERROR)
            return HttpResponseRedirect(reverse("admin:core_sermon_changelist"))

        try:
            generate_sermon_content(sermon)
        except AIContentGenerationError as exc:
            self.message_user(request, f"AI 생성 실패: {exc}", level=messages.ERROR)
        else:
            self.message_user(request, f"'{sermon.title}' 설교 내용을 AI로 다시 생성했습니다.", level=messages.SUCCESS)
        return HttpResponseRedirect(reverse("admin:core_sermon_change", args=[sermon.pk]))

    def transcribe_and_regenerate_ai_view(self, request, object_id):
        sermon = self.get_object(request, object_id)
        if sermon is None:
            self.message_user(request, "설교를 찾을 수 없습니다.", level=messages.ERROR)
            return HttpResponseRedirect(reverse("admin:core_sermon_changelist"))

        media_path = sermon.resolved_source_media_path
        if not media_path:
            self.message_user(
                request,
                "먼저 '원본 파일'에서 AI 작업용 설교 파일을 선택해 주세요.",
                level=messages.ERROR,
            )
            return HttpResponseRedirect(reverse("admin:core_sermon_change", args=[sermon.pk]))

        try:
            transcript = transcribe_audio_file(media_path)
            sermon.transcript = transcript
            sermon.import_error = ""
            sermon.save(update_fields=["transcript", "import_error", "updated_at"])
            generate_sermon_content(sermon)
        except (TranscriptFetchError, AIContentGenerationError) as exc:
            if isinstance(exc, TranscriptFetchError):
                sermon.import_error = str(exc)
                sermon.save(update_fields=["import_error", "updated_at"])
                self.message_user(request, f"전사 실패: {exc}", level=messages.ERROR)
            else:
                sermon.ai_error = str(exc)
                sermon.save(update_fields=["ai_error", "updated_at"])
                self.message_user(request, f"AI 생성 실패: {exc}", level=messages.ERROR)
        else:
            self.message_user(
                request,
                f"'{sermon.title}' 설교를 전사하고 AI 내용까지 정리했습니다.",
                level=messages.SUCCESS,
            )
        return HttpResponseRedirect(reverse("admin:core_sermon_change", args=[sermon.pk]))

    def publish_single_view(self, request, object_id):
        sermon = self.get_object(request, object_id)
        if sermon is None:
            self.message_user(request, "설교를 찾을 수 없습니다.", level=messages.ERROR)
            return HttpResponseRedirect(reverse("admin:core_sermon_changelist"))

        sermon.publish()
        self.message_user(request, f"'{sermon.title}' 설교를 바로 공개했습니다.", level=messages.SUCCESS)
        return HttpResponseRedirect(reverse("admin:core_sermon_change", args=[sermon.pk]))

    def unpublish_single_view(self, request, object_id):
        sermon = self.get_object(request, object_id)
        if sermon is None:
            self.message_user(request, "설교를 찾을 수 없습니다.", level=messages.ERROR)
            return HttpResponseRedirect(reverse("admin:core_sermon_changelist"))

        sermon.unpublish()
        self.message_user(
            request,
            f"'{sermon.title}' 설교 공개를 해제했습니다. 사용자 화면에는 공개 전 안내 화면이 다시 표시됩니다.",
            level=messages.SUCCESS,
        )
        return HttpResponseRedirect(reverse("admin:core_sermon_change", args=[sermon.pk]))

    def delete_source_media_view(self, request, object_id):
        sermon = self.get_object(request, object_id)
        if sermon is None:
            self.message_user(request, "설교를 찾을 수 없습니다.", level=messages.ERROR)
            return HttpResponseRedirect(reverse("admin:core_sermon_changelist"))

        media_path = sermon.resolved_source_media_path
        if not media_path:
            self.message_user(request, "삭제할 원본 미디어 경로가 없습니다.", level=messages.WARNING)
            return HttpResponseRedirect(reverse("admin:core_sermon_change", args=[sermon.pk]))

        allowed_root = Path(settings.MEDIA_ROOT).resolve()
        try:
            resolved_media_path = Path(media_path).resolve(strict=False)
            resolved_media_path.relative_to(allowed_root)
        except Exception:
            self.message_user(
                request,
                "uploads 폴더 아래의 원본 미디어 파일만 삭제할 수 있습니다.",
                level=messages.ERROR,
            )
            return HttpResponseRedirect(reverse("admin:core_sermon_change", args=[sermon.pk]))

        file_deleted = False
        if resolved_media_path.exists() and resolved_media_path.is_file():
            resolved_media_path.unlink()
            file_deleted = True

        if sermon.source_media_asset_id:
            sermon.source_media_asset.delete()
            sermon.source_media_asset = None
        sermon.source_media_path = ""
        sermon.save(update_fields=["source_media_asset", "source_media_path", "updated_at"])

        if file_deleted:
            self.message_user(request, "원본 미디어 파일을 삭제하고 경로를 비웠습니다.", level=messages.SUCCESS)
        else:
            self.message_user(
                request,
                "원본 미디어 파일은 이미 없었고 경로만 비웠습니다.",
                level=messages.WARNING,
            )
        return HttpResponseRedirect(reverse("admin:core_sermon_change", args=[sermon.pk]))

    def save_model(self, request, obj, form, change):
        if obj.source_media_asset_id and not form.cleaned_data.get("title"):
            obj.title = _clean_sermon_title_from_filename(obj.source_media_asset.file.name)

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



