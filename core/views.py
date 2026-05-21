import re
import logging
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.forms import AuthenticationForm
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Case, Count, IntegerField, Q, Sum, Value, When
from django.forms import modelformset_factory
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST

from .forms import (
    PastorAudioTranscriptUploadForm,
    PastorDailyEngagementForm,
    PastorSermonEditForm,
    PastorSermonSummaryForm,
    PastorTranscriptCorrectionRuleForm,
    SermonLifeSignUpForm,
)
from .models import (
    Church,
    DailyEngagement,
    DailyMissionCompletion,
    DailyQuizAttempt,
    DailyReflectionResponse,
    PastorAudioTranscript,
    PastorAudioTranscriptStatus,
    PointLedger,
    PointSource,
    PrayerCompanion,
    PrayerRequest,
    PrayerRequestStatus,
    PrayerRequestVisibility,
    SermonAudioClip,
    SermonAudioClipKind,
    Sermon,
    SermonHighlightChoice,
    SermonHighlightVote,
    SermonStatus,
    SermonSummary,
    UserProfile,
    WeeklyChallenge,
    TranscriptCorrectionRule,
    get_current_public_sermon_id,
)
from .services.ai_generation import AIContentGenerationError, generate_sermon_content
from .services.transcript_service import TranscriptFetchError, transcribe_audio_file, transcribe_uploaded_audio
from .services.engagement import (
    DAILY_COMPLETION_POINTS,
    MISSION_POINTS,
    QUIZ_POINTS,
    REFLECTION_POINTS,
    WEEKLY_COMPLETION_POINTS,
    complete_mission,
    submit_daily_quiz,
    submit_reflection,
)
from .services.prayer_scripture_recommendations import (
    PrayerScriptureRecommendationError,
    enrich_prayer_scripture_recommendations,
    request_prayer_scripture_recommendations,
)
from reports.models import (
    ContentQualityReport,
    DailyActionReport,
    SermonParticipationReport,
    UserParticipationReport,
    WeeklyParticipationReport,
)
from reports.services import (
    sync_content_quality_report,
    sync_daily_action_report,
    sync_sermon_participation_report,
    sync_user_participation_report,
    sync_weekly_participation_report,
)


logger = logging.getLogger(__name__)
ACTIVE_CHURCH_SESSION_KEY = "sermonlife_active_church_slug"


def _set_active_church_session(request, church):
    if hasattr(request, "session") and church and church.slug:
        request.session[ACTIVE_CHURCH_SESSION_KEY] = church.slug


def _get_or_create_profile(user, church=None):
    if not user.is_authenticated:
        return None
    default_church = church or Church.get_default()
    profile, _ = UserProfile.objects.get_or_create(
        user=user,
        defaults={"church": default_church},
    )
    if profile.church_id is None:
        default_church = church or Church.get_default()
        if default_church:
            profile.church = default_church
            profile.save(update_fields=["church"])
    return profile


def _get_user_church(user):
    if not user.is_authenticated:
        return None
    profile = UserProfile.objects.filter(user=user).select_related("church").first()
    if profile and profile.church_id:
        return profile.church
    return Church.get_default()


def _resolve_active_church(request, church_slug=None):
    if church_slug:
        church = Church.get_by_slug(church_slug)
        if not church:
            raise Http404
        _set_active_church_session(request, church)
        return church

    if request.user.is_authenticated:
        church = _get_user_church(request.user)
        if church:
            _set_active_church_session(request, church)
            return church

    default_church = Church.get_default()
    if default_church:
        _set_active_church_session(request, default_church)
    return default_church


def _church_home_url(church=None):
    default_church = Church.get_default()
    if not church or (default_church and church.pk == default_church.pk):
        return reverse("core:home")
    return reverse("core:church_home", kwargs={"church_slug": church.slug})


def _church_login_url(church=None):
    default_church = Church.get_default()
    if not church or (default_church and church.pk == default_church.pk):
        return reverse("core:login")
    return reverse("core:church_login", kwargs={"church_slug": church.slug})


def _church_signup_url(church=None):
    default_church = Church.get_default()
    if not church or (default_church and church.pk == default_church.pk):
        return reverse("core:signup")
    return reverse("core:church_signup", kwargs={"church_slug": church.slug})


def _church_logout_url(church=None):
    default_church = Church.get_default()
    if not church or (default_church and church.pk == default_church.pk):
        return reverse("core:logout")
    return reverse("core:church_logout", kwargs={"church_slug": church.slug})


def _church_history_url(church=None):
    default_church = Church.get_default()
    if not church or (default_church and church.pk == default_church.pk):
        return reverse("core:my_history")
    return reverse("core:church_my_history", kwargs={"church_slug": church.slug})


def _church_watch_url(church=None):
    default_church = Church.get_default()
    if not church or (default_church and church.pk == default_church.pk):
        return reverse("core:watch_sermon")
    return reverse("core:church_watch_sermon", kwargs={"church_slug": church.slug})


def _church_read_url(church=None):
    default_church = Church.get_default()
    if not church or (default_church and church.pk == default_church.pk):
        return reverse("core:read_sermon")
    return reverse("core:church_read_sermon", kwargs={"church_slug": church.slug})


def _build_church_nav_context(church=None):
    return {
        "home_url": _church_home_url(church),
        "login_url": _church_login_url(church),
        "signup_url": _church_signup_url(church),
        "logout_url": _church_logout_url(church),
        "history_url": _church_history_url(church),
        "watch_sermon_url": _church_watch_url(church),
        "read_sermon_url": _church_read_url(church),
    }


def _redirect_if_wrong_church_route(request, church_slug=None):
    if not church_slug or not request.user.is_authenticated:
        return None
    user_church = _get_user_church(request.user)
    if user_church and user_church.slug != church_slug:
        return redirect(_church_home_url(user_church))
    return None


def _chunk_outline_points(outline_points, chunk_count=5):
    points = [point for point in outline_points if point]
    if not points:
        return [[] for _ in range(chunk_count)]

    chunks = [[] for _ in range(chunk_count)]
    for index, point in enumerate(points):
        chunks[index % chunk_count].append(point)
    return chunks


def _build_highlight_summary(sermon):
    if not sermon:
        return None

    choices = list(
        SermonHighlightChoice.objects.filter(sermon=sermon)
        .annotate(vote_count=Count("votes"))
        .order_by("-vote_count", "order", "id")
    )
    if not choices:
        return None

    return {
        "choices": choices,
        "top_choice": choices[0],
        "total_votes": sum(choice.vote_count for choice in choices),
    }


def _format_transcript_paragraphs(transcript, sentences_per_paragraph=3):
    if not transcript:
        return []

    normalized = transcript.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"\n{3,}", "\n\n", normalized).strip()
    if not normalized:
        return []

    explicit_blocks = [block.strip() for block in normalized.split("\n\n") if block.strip()]
    paragraphs = []

    for block in explicit_blocks:
        compact = re.sub(r"\s+", " ", block).strip()
        if not compact:
            continue

        sentences = [
            sentence.strip()
            for sentence in re.split(r"(?<=[.!?])\s+", compact)
            if sentence.strip()
        ]

        if len(sentences) <= sentences_per_paragraph:
            paragraphs.append(compact)
            continue

        current = []
        for sentence in sentences:
            current.append(sentence)
            if len(current) >= sentences_per_paragraph:
                paragraphs.append(" ".join(current).strip())
                current = []
        if current:
            paragraphs.append(" ".join(current).strip())

    return paragraphs

def _get_active_challenge(church=None):
    challenge = WeeklyChallenge.get_current_public_challenge(church=church)
    if not challenge and getattr(settings, "SERMONLIFE_ALLOW_PREVIEW_ANYDAY", False):
        queryset = WeeklyChallenge.objects.filter(
                Q(sermon__is_published=True) | Q(sermon__scheduled_publish_at__isnull=False)
            )
        if church is not None:
            queryset = queryset.filter(sermon__church=church)
        challenge = (
            queryset
            .select_related("sermon", "sermon__summary")
            .prefetch_related("daily_engagements")
            .order_by("-week_start", "-id")
            .first()
        )
    if not challenge:
        return None
    return (
        WeeklyChallenge.objects.filter(pk=challenge.pk)
        .select_related("sermon", "sermon__summary")
        .prefetch_related("daily_engagements")
        .first()
    )


def _get_default_pastor_sermon(available_sermons, active_challenge=None):
    if not available_sermons:
        return None

    requested_unpublished = [
        sermon for sermon in available_sermons
        if sermon.pastor_review_requested and not sermon.is_published
    ]
    if requested_unpublished:
        return requested_unpublished[0]

    active_church = active_challenge.sermon.church if active_challenge and active_challenge.sermon_id else None
    current_public_sermon_id = get_current_public_sermon_id(church=active_church)
    current_public = next(
        (sermon for sermon in available_sermons if sermon.pk == current_public_sermon_id),
        None,
    )
    if current_public:
        return current_public

    return available_sermons[0]


def _get_publication_state(sermon, current_public_sermon_id=None):
    if not sermon:
        return {"label": "미공개", "is_current": False}
    if sermon.scheduled_publish_at and not sermon.is_published:
        return {"label": "화요일 예약 공개", "is_current": False}
    current_public_sermon_id = current_public_sermon_id or get_current_public_sermon_id(church=sermon.church)
    if sermon.is_published and sermon.pk == current_public_sermon_id:
        return {"label": "현재 공개 중", "is_current": True}
    if sermon.is_published:
        return {"label": "이전 공개", "is_current": False}
    return {"label": "미공개", "is_current": False}

def _get_summary(sermon):
    if not sermon:
        return None
    try:
        candidate = sermon.summary
    except SermonSummary.DoesNotExist:
        return None
    return candidate if candidate.approved else None



def _get_daily_state(user, current_daily):
    if not user.is_authenticated or not current_daily:
        return None

    quiz_attempt = DailyQuizAttempt.objects.filter(user=user, daily_engagement=current_daily).first()
    reflection = DailyReflectionResponse.objects.filter(user=user, daily_engagement=current_daily).first()
    mission = DailyMissionCompletion.objects.filter(user=user, daily_engagement=current_daily).first()
    ledger = {
        entry["source"]: entry["points"]
        for entry in PointLedger.objects.filter(
            user=user,
            challenge=current_daily.challenge,
            note=f"day:{current_daily.day_number}",
        ).values("source", "points")
    }
    return {
        "quiz_attempt": quiz_attempt,
        "reflection": reflection,
        "mission": mission,
        "quiz_points": ledger.get(PointSource.QUIZ, 0),
        "reflection_points": ledger.get(PointSource.REFLECTION, 0),
        "mission_points": ledger.get(PointSource.MISSION, 0),
        "daily_bonus_points": ledger.get(PointSource.DAILY_BONUS, 0),
    }


def _build_home_context(request):
    active_church = _resolve_active_church(request)
    highlight_messages = []
    for message in list(messages.get_messages(request)):
        text = str(message)
        if "공감" in text or "마음에 남은 말씀" in text or "말씀 선택" in text:
            highlight_messages.append({"text": text, "tags": message.tags})
        else:
            if not hasattr(request, "_remaining_messages"):
                request._remaining_messages = []
            request._remaining_messages.append({"text": text, "tags": message.tags})

    prayer_tab_enabled = _can_access_prayer_tab(request.user)
    allowed_tabs = {"sermon", "overview", "routine", "today"}
    if prayer_tab_enabled:
        allowed_tabs.add("prayer")

    active_home_tab = request.GET.get("tab", "sermon")
    if active_home_tab not in allowed_tabs:
        active_home_tab = "sermon"
    active_feedback = request.GET.get("feedback", "")
    if active_feedback not in {"quiz", "reflection", "mission"}:
        active_feedback = ""
    open_prayer_id = request.GET.get("open_prayer")
    try:
        open_prayer_id = int(open_prayer_id) if open_prayer_id else None
    except (TypeError, ValueError):
        open_prayer_id = None

    challenge = _get_active_challenge(active_church)
    sermon = challenge.sermon if challenge else None
    summary = _get_summary(sermon)
    profile = _get_or_create_profile(request.user, active_church)

    weekly_points = 0
    weekly_bonus_awarded = False
    if request.user.is_authenticated and challenge:
        weekly_points = (
            PointLedger.objects.filter(user=request.user, challenge=challenge)
            .aggregate(total=Sum("points"))
            .get("total")
            or 0
        )
        weekly_bonus_awarded = PointLedger.objects.filter(
            user=request.user,
            challenge=challenge,
            source=PointSource.WEEKLY_BONUS,
            note="week_complete",
        ).exists()

    daily_engagements = []
    current_daily = None
    current_day_number = None
    completed_days_count = 0
    daily_focus_map = {}
    if challenge:
        current_day_number = challenge.current_day_number(timezone.localdate())
        daily_engagements = list(
            challenge.daily_engagements.filter(approved=True).order_by("day_number")
        )
        outline_chunks = _chunk_outline_points(getattr(summary, "outline_points", []) if summary else [])
        key_points = [
            point
            for point in [
                getattr(summary, "key_point1", "") if summary else "",
                getattr(summary, "key_point2", "") if summary else "",
                getattr(summary, "key_point3", "") if summary else "",
            ]
            if point
        ]
        for index, item in enumerate(daily_engagements, start=1):
            outline_for_day = outline_chunks[index - 1] if index - 1 < len(outline_chunks) else []
            key_for_day = key_points[(index - 1) % len(key_points)] if key_points else ""
            item.focus_outline_points = outline_for_day
            item.focus_key_point = key_for_day
            daily_focus_map[item.day_number] = {
                "outline_points": outline_for_day,
                "key_point": key_for_day,
            }
        current_daily = next(
            (item for item in daily_engagements if item.day_number == current_day_number),
            None,
        )
        if request.user.is_authenticated:
            completed_days_count = sum(
                1
                for item in daily_engagements
                if DailyQuizAttempt.objects.filter(user=request.user, daily_engagement=item).exists()
                and DailyReflectionResponse.objects.filter(user=request.user, daily_engagement=item).exists()
                and DailyMissionCompletion.objects.filter(
                    user=request.user,
                    daily_engagement=item,
                    completed=True,
                ).exists()
            )

    current_daily_state = _get_daily_state(request.user, current_daily)
    highlight_choices = []
    highlight_vote = None
    top_highlight_choice = None
    total_highlight_voters = 0
    weekly_audio_clip = None
    today_audio_clip = None
    my_prayer_requests = []
    public_prayer_requests = []
    testimony_prayer_requests = []
    if sermon:
        highlight_choices = list(sermon.highlight_choices.all())
        if request.user.is_authenticated:
            highlight_vote = (
                SermonHighlightVote.objects.filter(user=request.user, sermon=sermon)
                .select_related("choice")
                .first()
            )
        if highlight_choices:
            highlight_totals = {
                row["choice_id"]: row["total"]
                for row in (
                    SermonHighlightVote.objects.filter(sermon=sermon)
                    .values("choice_id")
                    .annotate(total=Count("id"))
                )
            }
            total_highlight_voters = sum(highlight_totals.values())
            for choice in highlight_choices:
                choice.vote_count = highlight_totals.get(choice.id, 0)
            top_highlight_choice = max(
                highlight_choices,
                key=lambda choice: (getattr(choice, "vote_count", 0), -choice.order),
            )
        weekly_audio_clip = (
            SermonAudioClip.objects.filter(
                sermon=sermon,
                kind=SermonAudioClipKind.WEEKLY_SUMMARY,
                day_number=0,
            )
            .exclude(file="")
            .first()
        )
        if current_day_number:
            today_audio_clip = (
                SermonAudioClip.objects.filter(
                    sermon=sermon,
                    kind=SermonAudioClipKind.DAILY_CONTENT,
                    day_number=current_day_number,
                )
                .exclude(file="")
                .first()
            )
    if request.user.is_authenticated:
        my_prayer_support_counts = {
            row["prayer_request_id"]: row["total"]
            for row in (
                PrayerCompanion.objects.filter(prayer_request__user=request.user)
                .exclude(user=request.user)
                .values("prayer_request_id")
                .annotate(total=Count("id"))
            )
        }
        my_prayer_support_me = {
            row["prayer_request_id"]
            for row in (
                PrayerCompanion.objects.filter(prayer_request__user=request.user, user=request.user)
                .values("prayer_request_id")
            )
        }
        my_prayer_requests = list(
            PrayerRequest.objects.filter(user=request.user).order_by("-updated_at", "-created_at", "-id")
        )
        for prayer in my_prayer_requests:
            prayer.support_count = my_prayer_support_counts.get(prayer.id, 0)
            prayer.supported_by_me = prayer.id in my_prayer_support_me
        _hydrate_prayer_scripture_recommendations(my_prayer_requests)

        supported_public_ids = {
            row["prayer_request_id"]
            for row in (
                PrayerCompanion.objects.filter(user=request.user)
                .values("prayer_request_id")
            )
        }
        public_prayer_requests = list(
            PrayerRequest.objects.filter(is_public=True, user__userprofile__church=active_church)
            .exclude(user=request.user)
            .annotate(support_count=Count("companions", distinct=True))
            .select_related("user")
            .order_by(
                Case(
                    When(status=PrayerRequestStatus.PRAYING, then=Value(0)),
                    When(status=PrayerRequestStatus.ON_HOLD, then=Value(1)),
                    default=Value(2),
                    output_field=IntegerField(),
                ),
                "-updated_at",
                "-created_at",
            )[:8]
        )
        for prayer in public_prayer_requests:
            prayer.supported_by_me = prayer.id in supported_public_ids
        _hydrate_prayer_scripture_recommendations(public_prayer_requests)
        testimony_prayer_requests = list(
            PrayerRequest.objects.filter(
                is_public=True,
                status=PrayerRequestStatus.ANSWERED,
                user__userprofile__church=active_church,
            )
            .exclude(testimony_note="")
            .select_related("user")
            .order_by("-answered_at", "-updated_at", "-created_at")[:6]
        )
        _hydrate_prayer_scripture_recommendations(testimony_prayer_requests)
    weekly_base_max_points = (QUIZ_POINTS + REFLECTION_POINTS + MISSION_POINTS + DAILY_COMPLETION_POINTS) * 5
    weekly_total_max_points = weekly_base_max_points + WEEKLY_COMPLETION_POINTS

    context = {
        "profile": profile,
        "active_church": active_church,
        "challenge": challenge,
        "sermon": sermon,
        "weekly_points": weekly_points,
        "total_points": profile.points if profile else 0,
        "streak_days": profile.streak_days if profile else 0,
        "weekly_bonus_awarded": weekly_bonus_awarded,
        "weekly_base_max_points": weekly_base_max_points,
        "weekly_total_max_points": weekly_total_max_points,
        "quiz_points_value": QUIZ_POINTS,
        "reflection_points_value": REFLECTION_POINTS,
        "mission_points_value": MISSION_POINTS,
        "daily_bonus_points_value": DAILY_COMPLETION_POINTS,
        "weekly_bonus_points_value": WEEKLY_COMPLETION_POINTS,
        "completed_days_count": completed_days_count,
        "overview": getattr(summary, "overview", "") if summary else "",
        "daily_engagements": daily_engagements,
        "current_daily": current_daily,
        "current_day_number": current_day_number,
        "current_daily_state": current_daily_state,
        "daily_focus_map": daily_focus_map,
        "highlight_choices": highlight_choices,
        "highlight_vote": highlight_vote,
        "top_highlight_choice": top_highlight_choice,
        "total_highlight_voters": total_highlight_voters,
        "weekly_audio_clip": weekly_audio_clip,
        "today_audio_clip": today_audio_clip,
        "highlight_messages": highlight_messages,
        "remaining_messages": getattr(request, "_remaining_messages", []),
        "active_home_tab": active_home_tab,
        "active_feedback": active_feedback,
        "open_prayer_id": open_prayer_id,
        "prayer_tab_enabled": prayer_tab_enabled,
        "my_prayer_requests": my_prayer_requests,
        "public_prayer_requests": public_prayer_requests,
        "testimony_prayer_requests": testimony_prayer_requests,
        "prayer_status_choices": PrayerRequestStatus.choices,
        "prayer_visibility_choices": _build_prayer_visibility_options(),
    }
    context.update(_build_church_nav_context(active_church))
    return context


def _get_released_daily_or_404(request, pk):
    challenge = _get_active_challenge(_resolve_active_church(request))
    if not challenge:
        raise Http404
    daily = get_object_or_404(
        DailyEngagement,
        pk=pk,
        challenge=challenge,
        approved=True,
    )
    if daily.day_number > challenge.current_day_number(timezone.localdate()):
        raise Http404
    return daily


def _redirect_home(request, tab="sermon", anchor="", extra_params=None):
    params = {}
    if tab:
        params["tab"] = tab
    if extra_params:
        params.update({key: value for key, value in extra_params.items() if value})
    query = urlencode(params) if params else ""
    url = _church_home_url(_resolve_active_church(request))
    if query:
        url = f"{url}?{query}"
    if anchor:
        url = f"{url}#{anchor}"
    return redirect(url)

def _redirect_to_today_set(request):
    return _redirect_home(request, tab="today", anchor="today-set")


def _hydrate_prayer_scripture_recommendations(prayer_requests):
    for prayer in prayer_requests or []:
        recommendations = getattr(prayer, "scripture_recommendations", None) or []
        if not recommendations:
            continue
        try:
            enriched, changed = enrich_prayer_scripture_recommendations(recommendations)
        except Exception:
            logger.exception("Failed to enrich prayer scripture recommendations for prayer_request=%s", prayer.pk)
            continue
        prayer.scripture_recommendations = enriched
        if changed:
            PrayerRequest.objects.filter(pk=prayer.pk).update(scripture_recommendations=enriched)


def _redirect_to_today_anchor(request, default_anchor="today-set"):
    anchor = request.POST.get("return_anchor", "").strip() or default_anchor
    return _redirect_home(request, tab="today", anchor=anchor)


def _build_prayer_visibility_options():
    descriptions = {
        PrayerRequestVisibility.PRIVATE: "이 기도제목은 나만 보고 조용히 기도합니다.",
        PrayerRequestVisibility.PUBLIC: "교인들에게 공개해서 함께 기도해 주시길 부탁합니다.",
        PrayerRequestVisibility.ANONYMOUS: "교인들에게 공개해서 함께 기도를 부탁하지만, 내 이름은 보이지 않습니다.",
    }
    return [
        {
            "value": value,
            "label": label,
            "description": descriptions.get(value, ""),
        }
        for value, label in PrayerRequestVisibility.choices
    ]


def _is_pastor_user(user):
    if not user.is_authenticated:
        return False
    if user.is_superuser or user.is_staff:
        return True
    profile = UserProfile.objects.filter(user=user).first()
    return bool(profile and profile.member_role == "pastor")


def _can_use_audio_transcriber(user):
    if not user.is_authenticated:
        return False
    if user.is_superuser or user.is_staff:
        return True
    profile = UserProfile.objects.filter(user=user).first()
    return bool(profile and profile.member_role == "pastor" and profile.can_use_audio_transcriber)


def _create_pastor_audio_transcript_job(*, user, scope_church, uploaded_file):
    transcript_job = PastorAudioTranscript(
        church=scope_church,
        user=user,
        source_file=uploaded_file,
        original_filename=getattr(uploaded_file, "name", ""),
        source_content_type=getattr(uploaded_file, "content_type", "") or "",
        source_size=getattr(uploaded_file, "size", 0) or 0,
    )
    transcript_job.save()
    try:
        transcript_text = transcribe_audio_file(transcript_job.source_file.path)
    except TranscriptFetchError as exc:
        transcript_job.status = PastorAudioTranscriptStatus.FAILED
        transcript_job.error_text = str(exc)
        transcript_job.transcript_text = ""
        transcript_job.save(update_fields=["status", "error_text", "transcript_text", "updated_at"])
        raise
    else:
        transcript_job.status = PastorAudioTranscriptStatus.COMPLETED
        transcript_job.transcript_text = transcript_text
        transcript_job.error_text = ""
        transcript_job.save(update_fields=["status", "transcript_text", "error_text", "updated_at"])
        return transcript_job


def _can_access_prayer_tab(user):
    if getattr(settings, "SERMONLIFE_PRAYER_TAB_PUBLIC", False):
        return user.is_authenticated
    return _is_pastor_user(user)


def _get_access_scope_church(user):
    if user.is_superuser:
        return None
    return _get_user_church(user)


pastor_required = user_passes_test(_is_pastor_user, login_url="core:login")


def _build_pastor_publish_checklist(sermon):
    challenge = sermon.weekly_challenges.order_by("-week_start", "-id").first()
    try:
        summary = sermon.summary
    except SermonSummary.DoesNotExist:
        summary = None

    approved_days = list(sermon.daily_engagements.filter(approved=True).order_by("day_number"))
    checklist = [
        {"label": "설교 제목이 입력되어 있습니다", "ok": bool(sermon.title.strip())},
        {"label": "자막 및 원문이 준비되어 있습니다", "ok": bool(sermon.transcript.strip())},
        {"label": "설교 개요가 입력되어 있습니다", "ok": bool(summary and summary.overview.strip())},
        {"label": "핵심 메시지 3개가 준비되어 있습니다", "ok": bool(summary and summary.key_point1.strip() and summary.key_point2.strip() and summary.key_point3.strip())},
        {"label": "Day 1~5가 모두 준비되어 있습니다", "ok": len(approved_days) == 5},
        {"label": "공감 문장 후보가 3개 준비되어 있습니다", "ok": sermon.highlight_choices.count() >= 3},
        {"label": "주간 챌린지가 연결되어 있습니다", "ok": challenge is not None},
    ]
    if approved_days:
        checklist.extend(
            {
                "label": f"Day {daily.day_number}의 퀴즈, 묵상, 미션이 모두 입력되어 있습니다",
                "ok": bool(
                    daily.title.strip()
                    and daily.quiz_question.strip()
                    and daily.quiz_answer.strip()
                    and daily.reflection_question.strip()
                    and daily.mission_title.strip()
                ),
            }
            for daily in approved_days
        )
    return checklist


@login_required
def watch_sermon_view(request, church_slug=None):
    wrong_route_redirect = _redirect_if_wrong_church_route(request, church_slug)
    if wrong_route_redirect:
        return wrong_route_redirect
    active_church = _resolve_active_church(request, church_slug)
    challenge = _get_active_challenge(active_church)
    sermon = challenge.sermon if challenge else None
    if not sermon:
        raise Http404
    context = {
        "challenge": challenge,
        "sermon": sermon,
        "active_church": active_church,
    }
    context.update(_build_church_nav_context(active_church))
    return render(request, "core/watch_sermon.html", context)


@login_required
def read_sermon_view(request, church_slug=None):
    wrong_route_redirect = _redirect_if_wrong_church_route(request, church_slug)
    if wrong_route_redirect:
        return wrong_route_redirect
    active_church = _resolve_active_church(request, church_slug)
    challenge = _get_active_challenge(active_church)
    sermon = challenge.sermon if challenge else None
    if not sermon:
        raise Http404
    transcript_paragraphs = _format_transcript_paragraphs(sermon.transcript)
    current_day_number = challenge.current_day_number(timezone.localdate()) if challenge else None
    current_daily = None
    if challenge and current_day_number:
        current_daily = (
            challenge.daily_engagements.filter(
                approved=True,
                day_number=current_day_number,
            ).first()
        )
    context = {
        "challenge": challenge,
        "sermon": sermon,
        "transcript_paragraphs": transcript_paragraphs,
        "current_daily": current_daily,
        "current_day_number": current_day_number,
        "active_church": active_church,
    }
    context.update(_build_church_nav_context(active_church))
    return render(request, "core/read_sermon.html", context)


@login_required
def my_history_view(request, church_slug=None):
    wrong_route_redirect = _redirect_if_wrong_church_route(request, church_slug)
    if wrong_route_redirect:
        return wrong_route_redirect
    active_church = _resolve_active_church(request, church_slug)
    profile = _get_or_create_profile(request.user, active_church)
    challenge = _get_active_challenge(active_church)
    weekly_points = 0
    if challenge:
        weekly_points = (
            PointLedger.objects.filter(user=request.user, challenge=challenge)
            .aggregate(total=Sum("points"))
            .get("total")
            or 0
        )

    challenge_ids = list(
        PointLedger.objects.filter(user=request.user, challenge__sermon__church=active_church)
        .order_by("-created_at")
        .values_list("challenge_id", flat=True)
        .distinct()[:8]
    )
    challenges = (
        WeeklyChallenge.objects.filter(id__in=challenge_ids)
        .select_related("sermon")
        .prefetch_related("daily_engagements")
    )
    challenge_map = {challenge.id: challenge for challenge in challenges}
    weekly_history = []
    for challenge_id in challenge_ids:
        item = challenge_map.get(challenge_id)
        if not item:
            continue
        earned_points = (
            PointLedger.objects.filter(user=request.user, challenge=item)
            .aggregate(total=Sum("points"))
            .get("total")
            or 0
        )
        completed_days = sum(
            1
            for daily in item.daily_engagements.filter(approved=True).order_by("day_number")
            if DailyQuizAttempt.objects.filter(user=request.user, daily_engagement=daily).exists()
            and DailyReflectionResponse.objects.filter(user=request.user, daily_engagement=daily).exists()
            and DailyMissionCompletion.objects.filter(
                user=request.user,
                daily_engagement=daily,
                completed=True,
            ).exists()
        )
        weekly_history.append(
            {
                "challenge": item,
                "earned_points": earned_points,
                "completed_days": completed_days,
                "weekly_bonus_awarded": PointLedger.objects.filter(
                    user=request.user,
                    challenge=item,
                    source=PointSource.WEEKLY_BONUS,
                    note="week_complete",
                ).exists(),
            }
        )

    recent_entries = PointLedger.objects.filter(
        user=request.user,
        challenge__sermon__church=active_church,
    ).select_related("challenge", "sermon")[:20]

    context = {
        "profile": profile,
        "current_challenge": challenge,
        "weekly_points": weekly_points,
        "weekly_history": weekly_history,
        "recent_entries": recent_entries,
        "active_church": active_church,
    }
    context.update(_build_church_nav_context(active_church))
    return render(request, "core/my_history.html", context)


def home_view(request, church_slug=None):
    wrong_route_redirect = _redirect_if_wrong_church_route(request, church_slug)
    if wrong_route_redirect:
        return wrong_route_redirect
    active_church = _resolve_active_church(request, church_slug)
    if not request.user.is_authenticated:
        return redirect(_church_login_url(active_church))
    return render(request, "core/home.html", _build_home_context(request))


@never_cache
@ensure_csrf_cookie
def signup_view(request, church_slug=None):
    active_church = _resolve_active_church(request, church_slug)
    if request.user.is_authenticated:
        return redirect(_church_home_url(_get_user_church(request.user) or active_church))

    if request.method == "POST":
        form = SermonLifeSignUpForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.first_name = form.cleaned_data["first_name"]
            user.save()
            UserProfile.objects.update_or_create(
                user=user,
                defaults={"member_role": form.cleaned_data["member_role"], "church": active_church},
            )
            _set_active_church_session(request, active_church)
            login(request, user)
            messages.success(request, "회원가입이 완료되었습니다. 이번 주 설교 루틴을 시작해 보세요.")
            return redirect(_church_home_url(active_church))
    else:
        form = SermonLifeSignUpForm()

    context = {
        "form": form,
        "active_church": active_church,
    }
    context.update(_build_church_nav_context(active_church))
    return render(request, "core/signup.html", context)


@never_cache
@ensure_csrf_cookie
def login_view(request, church_slug=None):
    active_church = _resolve_active_church(request, church_slug)
    if request.user.is_authenticated:
        return redirect(_church_home_url(_get_user_church(request.user) or active_church))

    form = AuthenticationForm(request, data=request.POST or None)
    form.fields["username"].label = "아이디"
    form.fields["password"].label = "비밀번호"
    form.fields["username"].widget.attrs.update({"placeholder": "아이디"})
    form.fields["password"].widget.attrs.update({"placeholder": "비밀번호"})
    if request.method == "POST" and form.is_valid():
        user = form.get_user()
        user_church = _get_user_church(user) or active_church
        _set_active_church_session(request, user_church)
        login(request, user)
        messages.success(request, "로그인되었습니다.")
        return redirect(_church_home_url(user_church))

    context = {
        "form": form,
        "active_church": active_church,
    }
    context.update(_build_church_nav_context(active_church))
    return render(request, "core/login.html", context)


def logout_view(request, church_slug=None):
    active_church = _resolve_active_church(request, church_slug)
    logout(request)
    messages.success(request, "로그아웃되었습니다.")
    return redirect(_church_login_url(active_church))


@login_required
@require_POST
def submit_daily_quiz_view(request, pk):
    daily = _get_released_daily_or_404(request, pk)
    selected_answer = request.POST.get("selected_answer", "").strip()
    if selected_answer not in daily.choices:
        messages.error(request, "퀴즈 보기를 선택한 뒤 제출해 주세요.")
        return _redirect_home(request, tab="today", anchor="today-quiz-card", extra_params={"feedback": "quiz"})

    result = submit_daily_quiz(
        user=request.user,
        daily_engagement=daily,
        selected_answer=selected_answer,
    )
    attempt = result["attempt"]
    bonus_suffix = ""
    if result["daily_bonus_awarded"]:
        bonus_suffix += " 하루 완료 보너스 3달란트가 추가되었습니다."
    if result["weekly_bonus_awarded"]:
        bonus_suffix += " 5일 완주 보너스 20달란트가 추가되었습니다."

    if result["is_update"]:
        if attempt.is_correct:
            messages.warning(request, f"이미 제출한 퀴즈입니다. 달란트는 1회만 적립됩니다.{bonus_suffix}")
        else:
            messages.warning(request, "이미 제출한 퀴즈입니다. 다시 설교 내용을 확인해 보세요.")
    elif attempt.is_correct:
        messages.success(request, f"정답입니다. 퀴즈 5달란트를 받았습니다.{bonus_suffix}")
    else:
        messages.warning(request, "정답이 아닙니다. 다시 설교 내용을 확인해 보세요.")
    return _redirect_home(request, tab="today", anchor="today-quiz-card", extra_params={"feedback": "quiz"})


@login_required
@require_POST
def submit_reflection_view(request, pk):
    daily = _get_released_daily_or_404(request, pk)
    response_text = request.POST.get("response_text", "")
    try:
        result = submit_reflection(
            user=request.user,
            daily_engagement=daily,
            response_text=response_text,
        )
    except ValueError as exc:
        messages.error(request, str(exc))
        return _redirect_home(request, tab="today", anchor="today-reflection-card", extra_params={"feedback": "reflection"})

    bonus_suffix = ""
    if result["daily_bonus_awarded"]:
        bonus_suffix += " 하루 완료 보너스 3달란트가 추가되었습니다."
    if result["weekly_bonus_awarded"]:
        bonus_suffix += " 5일 완주 보너스 20달란트가 추가되었습니다."

    if result["is_update"]:
        messages.warning(request, f"이미 저장한 묵상입니다. 달란트는 1회만 적립됩니다.{bonus_suffix}")
    else:
        messages.success(request, f"묵상 답변이 저장되었습니다. 5달란트를 받았습니다.{bonus_suffix}")
    return _redirect_home(request, tab="today", anchor="today-reflection-card", extra_params={"feedback": "reflection"})


@login_required
@require_POST
def complete_mission_view(request, pk):
    daily = _get_released_daily_or_404(request, pk)
    note = request.POST.get("mission_note", "")
    result = complete_mission(
        user=request.user,
        daily_engagement=daily,
        note=note,
    )

    bonus_suffix = ""
    if result["daily_bonus_awarded"]:
        bonus_suffix += " 하루 완료 보너스 3달란트가 추가되었습니다."
    if result["weekly_bonus_awarded"]:
        bonus_suffix += " 5일 완주 보너스 20달란트가 추가되었습니다."

    if result["is_update"]:
        messages.warning(request, f"이미 완료한 미션입니다. 달란트는 1회만 적립됩니다.{bonus_suffix}")
    else:
        messages.success(request, f"오늘의 미션을 완료했습니다. 7달란트를 받았습니다.{bonus_suffix}")
    return _redirect_home(request, tab="today", anchor="today-mission-card", extra_params={"feedback": "mission"})


@login_required
@require_POST
def create_prayer_request_view(request):
    if not _can_access_prayer_tab(request.user):
        messages.error(request, "기도 기능은 아직 전체 공개 전입니다.")
        return _redirect_home(request)
    content = (request.POST.get("content") or "").strip()
    visibility = (request.POST.get("visibility") or PrayerRequestVisibility.PRIVATE).strip()
    valid_visibilities = {choice[0] for choice in PrayerRequestVisibility.choices}

    if len(content) < 5:
        messages.error(request, "기도제목 내용을 조금 더 적어 주세요.")
        return _redirect_home(request, tab="prayer", anchor="prayer-create-card")
    if visibility not in valid_visibilities:
        messages.error(request, "공개 방식을 다시 선택해 주세요.")
        return _redirect_home(request, tab="prayer", anchor="prayer-create-card")

    prayer_request = PrayerRequest.objects.create(
        user=request.user,
        title="",
        content=content,
        visibility=visibility,
    )
    recommendation_ready = False
    try:
        prayer_request.scripture_recommendations = request_prayer_scripture_recommendations(prayer_request)
        prayer_request.save(update_fields=["scripture_recommendations", "updated_at"])
        recommendation_ready = True
    except PrayerScriptureRecommendationError:
        logger.exception("Failed to generate prayer scripture recommendations for prayer_request=%s", prayer_request.pk)
    if visibility == PrayerRequestVisibility.PUBLIC:
        message = "기도제목을 등록했고 교인들과 함께 기도할 수 있도록 공개했습니다."
    elif visibility == PrayerRequestVisibility.ANONYMOUS:
        message = "기도제목을 등록했고 익명으로 함께 기도할 수 있도록 공개했습니다."
    else:
        message = "기도제목을 등록했습니다."
    if recommendation_ready:
        message = f"{message} 함께 읽어보면 좋은 말씀도 추천해 드렸습니다."
    messages.success(request, message)
    return _redirect_home(
        request,
        tab="prayer",
        anchor=f"prayer-request-{prayer_request.pk}",
        extra_params={"open_prayer": prayer_request.pk},
    )


@login_required
@require_POST
def update_prayer_request_view(request, pk):
    if not _can_access_prayer_tab(request.user):
        messages.error(request, "기도 기능은 아직 전체 공개 전입니다.")
        return _redirect_home(request)
    prayer_request = get_object_or_404(PrayerRequest, pk=pk, user=request.user)
    previous_content = prayer_request.content
    content = (request.POST.get("content") or "").strip()
    status = (request.POST.get("status") or PrayerRequestStatus.PRAYING).strip()
    testimony_note = (request.POST.get("testimony_note") or "").strip()
    visibility = (request.POST.get("visibility") or PrayerRequestVisibility.PRIVATE).strip()

    valid_statuses = {choice[0] for choice in PrayerRequestStatus.choices}
    valid_visibilities = {choice[0] for choice in PrayerRequestVisibility.choices}
    if status not in valid_statuses:
        messages.error(request, "기도 상태를 다시 선택해 주세요.")
        return _redirect_home(request, tab="prayer", anchor=f"prayer-request-{prayer_request.pk}")
    if len(content) < 5:
        messages.error(request, "기도제목 내용을 조금 더 적어 주세요.")
        return _redirect_home(request, tab="prayer", anchor=f"prayer-request-{prayer_request.pk}")
    if visibility not in valid_visibilities:
        messages.error(request, "공개 방식을 다시 선택해 주세요.")
        return _redirect_home(request, tab="prayer", anchor=f"prayer-request-{prayer_request.pk}")
    if status == PrayerRequestStatus.ANSWERED and not testimony_note:
        messages.error(request, "응답받음으로 표시할 때는 간증이나 결과 메모를 함께 적어 주세요.")
        return _redirect_home(request, tab="prayer", anchor=f"prayer-request-{prayer_request.pk}")

    prayer_request.content = content
    prayer_request.status = status
    prayer_request.visibility = visibility
    prayer_request.testimony_note = testimony_note
    prayer_request.save()
    recommendation_ready = False
    if content != previous_content or not prayer_request.scripture_recommendations:
        try:
            prayer_request.scripture_recommendations = request_prayer_scripture_recommendations(prayer_request)
            prayer_request.save(update_fields=["scripture_recommendations", "updated_at"])
            recommendation_ready = True
        except PrayerScriptureRecommendationError:
            logger.exception("Failed to generate prayer scripture recommendations for prayer_request=%s", prayer_request.pk)

    if status == PrayerRequestStatus.ANSWERED:
        message = "기도제목을 응답받음으로 저장했고 간증 메모도 반영했습니다."
    else:
        message = "기도제목을 저장했습니다."
    if recommendation_ready:
        message = f"{message} 함께 읽어보면 좋은 말씀도 새로 추천해 드렸습니다."
    messages.success(request, message)
    return _redirect_home(
        request,
        tab="prayer",
        anchor="my-prayer-list",
    )


@login_required
@require_POST
def delete_prayer_request_view(request, pk):
    if not _can_access_prayer_tab(request.user):
        messages.error(request, "기도 기능은 아직 전체 공개 전입니다.")
        return _redirect_home(request)

    prayer_request = get_object_or_404(PrayerRequest, pk=pk, user=request.user)
    prayer_title = prayer_request.title
    prayer_request.delete()
    messages.success(request, f"'{prayer_title}' 기도제목을 삭제했습니다.")
    return _redirect_home(request, tab="prayer", anchor="my-prayer-list")


@login_required
@require_POST
def join_prayer_request_view(request, pk):
    if not _can_access_prayer_tab(request.user):
        messages.error(request, "기도 기능은 아직 전체 공개 전입니다.")
        return _redirect_home(request)
    prayer_request = get_object_or_404(
        PrayerRequest.objects.exclude(user=request.user),
        pk=pk,
        is_public=True,
    )
    companion, created = PrayerCompanion.objects.get_or_create(
        prayer_request=prayer_request,
        user=request.user,
    )
    if created:
        messages.success(request, "함께 기도하기로 표시했습니다.")
    else:
        messages.success(request, "이미 함께 기도 중으로 표시되어 있습니다.")
    return _redirect_home(request, tab="prayer", anchor="public-prayer-list")


@login_required
@require_POST
def transcribe_voice_note_view(request):
    audio_file = request.FILES.get("audio")
    if not audio_file:
        return JsonResponse({"ok": False, "error": "녹음 파일이 전달되지 않았습니다."}, status=400)

    file_size = getattr(audio_file, "size", 0) or 0
    content_type = getattr(audio_file, "content_type", "") or "unknown"

    try:
        transcript = transcribe_uploaded_audio(audio_file)
    except TranscriptFetchError as exc:
        message = str(exc)
        if "empty transcript" in message.lower():
            readable_size = f"{round(file_size / 1024, 1)}KB"
            message = (
                f"전사 결과가 비어 있습니다. 녹음 파일은 {readable_size}, 형식은 {content_type}입니다. "
                "조금 더 크게 또렷하게 말씀해 보시거나, 마이크를 입 가까이 두고 다시 시도해 주세요."
            )
        return JsonResponse({"ok": False, "error": message, "size": file_size, "content_type": content_type}, status=400)
    except Exception:
        return JsonResponse(
            {
                "ok": False,
                "error": "음성 전사 중 오류가 발생했습니다. 다시 시도해 주세요.",
                "size": file_size,
                "content_type": content_type,
            },
            status=500,
        )

    return JsonResponse({"ok": True, "text": transcript, "size": file_size, "content_type": content_type})
@login_required
@require_POST
def submit_highlight_vote_view(request):
    challenge = _get_active_challenge(_resolve_active_church(request))
    sermon = challenge.sermon if challenge else None
    if not sermon:
        messages.error(request, "투표할 설교를 찾을 수 없습니다.")
        return redirect("core:home")

    choice = get_object_or_404(
        SermonHighlightChoice,
        pk=request.POST.get("choice_id"),
        sermon=sermon,
    )

    existing_vote = SermonHighlightVote.objects.filter(user=request.user, sermon=sermon).first()
    SermonHighlightVote.objects.update_or_create(
        user=request.user,
        sermon=sermon,
        defaults={"choice": choice},
    )

    if existing_vote:
        messages.success(request, "가장 마음에 남은 말씀 선택이 변경되었습니다.")
    else:
        messages.success(request, "가장 마음에 남은 말씀에 투표했습니다.")

    return _redirect_home(request, tab="routine", anchor="highlight-panel")


@pastor_required
def pastor_transcript_corrections_view(request):
    scope_church = _get_access_scope_church(request.user)
    rules = list(TranscriptCorrectionRule.objects.all())
    create_form = PastorTranscriptCorrectionRuleForm(prefix="create")
    editing_rule_id = None

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()
        if action == "create":
            create_post = request.POST.copy()
            create_post["create-is_active"] = "on"
            create_form = PastorTranscriptCorrectionRuleForm(create_post, prefix="create")
            if create_form.is_valid():
                create_form.save()
                messages.success(request, "새 단어 규칙을 저장했습니다.")
                return redirect("core:pastor_transcript_corrections")
            messages.error(request, "새 규칙 내용을 다시 확인해 주세요.")
        elif action in {"update", "delete", "toggle"}:
            rule = get_object_or_404(TranscriptCorrectionRule, pk=request.POST.get("rule_id"))
            if action == "delete":
                deleted_name = rule.source_text
                rule.delete()
                messages.success(request, f"'{deleted_name}' 규칙을 삭제했습니다.")
                return redirect("core:pastor_transcript_corrections")
            if action == "toggle":
                rule.is_active = not rule.is_active
                rule.save(update_fields=["is_active", "updated_at"])
                if rule.is_active:
                    messages.success(request, f"'{rule.source_text}' 규칙을 다시 사용합니다.")
                else:
                    messages.success(request, f"'{rule.source_text}' 규칙 적용을 잠시 중지했습니다.")
                return redirect("core:pastor_transcript_corrections")

            editing_rule_id = rule.pk
            edit_post = request.POST.copy()
            edit_post[f"rule-{rule.pk}-is_active"] = "on"
            edit_form = PastorTranscriptCorrectionRuleForm(edit_post, instance=rule, prefix=f"rule-{rule.pk}")
            if edit_form.is_valid():
                edit_form.save()
                messages.success(request, f"'{rule.source_text}' 규칙을 저장했습니다.")
                return redirect("core:pastor_transcript_corrections")
            messages.error(request, "수정한 규칙 내용을 다시 확인해 주세요.")
        rules = list(TranscriptCorrectionRule.objects.all())

    rule_rows = []
    for rule in rules:
        if request.method == "POST" and editing_rule_id == rule.pk:
            edit_post = request.POST.copy()
            edit_post[f"rule-{rule.pk}-is_active"] = "on"
            form = PastorTranscriptCorrectionRuleForm(edit_post, instance=rule, prefix=f"rule-{rule.pk}")
        else:
            form = PastorTranscriptCorrectionRuleForm(instance=rule, prefix=f"rule-{rule.pk}")
        rule_rows.append({"rule": rule, "form": form})

    return render(
        request,
        "core/pastor_transcript_corrections.html",
        {
            "active_church": scope_church,
            "create_form": create_form,
            "rule_rows": rule_rows,
            "pastor_menu": "transcript_rules",
            "can_use_audio_transcriber": _can_use_audio_transcriber(request.user),
            **_build_church_nav_context(scope_church),
        },
    )


@never_cache
@ensure_csrf_cookie
@pastor_required
def pastor_audio_transcriber_view(request):
    scope_church = _get_access_scope_church(request.user)
    if not _can_use_audio_transcriber(request.user):
        messages.warning(request, "이 기능은 허용된 목회자 계정에서만 사용할 수 있습니다.")
        return redirect("core:pastor_dashboard")

    create_form = PastorAudioTranscriptUploadForm(prefix="create")

    if request.method == "POST":
        create_form = PastorAudioTranscriptUploadForm(request.POST, request.FILES, prefix="create")
        if create_form.is_valid():
            uploaded_file = create_form.cleaned_data["source_file"]
            try:
                _create_pastor_audio_transcript_job(
                    user=request.user,
                    scope_church=scope_church,
                    uploaded_file=uploaded_file,
                )
            except TranscriptFetchError as exc:
                messages.error(request, "음성 전사에 실패했습니다. 파일 형식이나 음질을 다시 확인해 주세요.")
            else:
                messages.success(request, "음성 파일 transcript를 생성했습니다.")
            return redirect("core:pastor_audio_transcriber")
        messages.error(request, "업로드할 음성 파일을 다시 확인해 주세요.")

    transcript_jobs = list(
        PastorAudioTranscript.objects.filter(user=request.user)
        .select_related("church")
        .order_by("-created_at", "-id")[:20]
    )
    for transcript_job in transcript_jobs:
        transcript_job.formatted_paragraphs = _format_transcript_paragraphs(
            transcript_job.transcript_text,
            sentences_per_paragraph=3,
        ) if transcript_job.transcript_text else []

    return render(
        request,
        "core/pastor_audio_transcriber.html",
        {
            "active_church": scope_church,
            "create_form": create_form,
            "transcript_jobs": transcript_jobs,
            "pastor_menu": "audio_transcriber",
            "can_use_audio_transcriber": True,
            **_build_church_nav_context(scope_church),
        },
    )


@never_cache
@ensure_csrf_cookie
@pastor_required
@require_POST
def pastor_audio_transcriber_record_view(request):
    scope_church = _get_access_scope_church(request.user)
    if not _can_use_audio_transcriber(request.user):
        return JsonResponse({"ok": False, "error": "이 기능은 허용된 목회자 계정에서만 사용할 수 있습니다."}, status=403)

    recorded_audio = request.FILES.get("audio")
    if not recorded_audio:
        return JsonResponse({"ok": False, "error": "녹음 파일이 전달되지 않았습니다."}, status=400)

    content_type = getattr(recorded_audio, "content_type", "") or "audio/webm"
    extension_map = {
        "audio/mp4": ".m4a",
        "audio/x-m4a": ".m4a",
        "audio/aac": ".aac",
        "audio/mpeg": ".mp3",
        "audio/mp3": ".mp3",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/webm": ".webm",
        "audio/ogg": ".ogg",
    }
    suffix = extension_map.get(content_type, ".webm")
    uploaded_file = SimpleUploadedFile(
        name=f"pastor-recording-{timezone.now().strftime('%Y%m%d-%H%M%S')}{suffix}",
        content=recorded_audio.read(),
        content_type=content_type,
    )

    try:
        _create_pastor_audio_transcript_job(
            user=request.user,
            scope_church=scope_church,
            uploaded_file=uploaded_file,
        )
    except TranscriptFetchError as exc:
        messages.error(request, "음성 전사에 실패했습니다. 파일 형식이나 음질을 다시 확인해 주세요.")
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)

    messages.success(request, "바로 녹음한 음성을 transcript로 저장했습니다.")
    return JsonResponse({"ok": True, "redirect_url": reverse("core:pastor_audio_transcriber")})


@pastor_required
@require_POST
def delete_pastor_audio_transcript_view(request, pk):
    if not _can_use_audio_transcriber(request.user):
        messages.warning(request, "이 기능은 허용된 목회자 계정에서만 사용할 수 있습니다.")
        return redirect("core:pastor_dashboard")

    transcript_job = get_object_or_404(PastorAudioTranscript, pk=pk, user=request.user)
    if transcript_job.source_file:
        transcript_job.source_file.delete(save=False)
    transcript_job.delete()
    messages.success(request, "Transcript 기록을 삭제했습니다.")
    return redirect("core:pastor_audio_transcriber")


@pastor_required
@require_POST
def email_pastor_audio_transcript_view(request, pk):
    if not _can_use_audio_transcriber(request.user):
        messages.warning(request, "이 기능은 허용된 목회자 계정에서만 사용할 수 있습니다.")
        return redirect("core:pastor_dashboard")

    transcript_job = get_object_or_404(PastorAudioTranscript, pk=pk, user=request.user)
    recipient = (request.user.email or "").strip()
    if not recipient:
        messages.error(request, "이 계정에는 이메일 주소가 등록되어 있지 않습니다.")
        return redirect("core:pastor_audio_transcriber")

    if not transcript_job.transcript_text.strip():
        messages.error(request, "보낼 Transcript 내용이 아직 없습니다.")
        return redirect("core:pastor_audio_transcriber")

    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "") or getattr(settings, "EMAIL_HOST_USER", "")
    if not from_email:
        messages.error(request, "발신 이메일 설정이 없습니다. DEFAULT_FROM_EMAIL 또는 EMAIL_HOST_USER를 설정해 주세요.")
        return redirect("core:pastor_audio_transcriber")

    subject = f"[WORD & LIFE] Transcript: {transcript_job.original_filename or '녹음 파일'}"
    body = (
        "요청하신 Transcript를 보내드립니다.\n\n"
        f"파일명: {transcript_job.original_filename or transcript_job.source_file.name}\n"
        f"생성 시각: {timezone.localtime(transcript_job.created_at).strftime('%Y-%m-%d %H:%M')}\n\n"
        f"{transcript_job.transcript_text}"
    )
    try:
        send_mail(
            subject=subject,
            message=body,
            from_email=from_email,
            recipient_list=[recipient],
            fail_silently=False,
        )
    except Exception:
        messages.error(request, "이메일 전송에 실패했습니다. 메일 설정과 네트워크 상태를 확인해 주세요.")
    else:
        messages.success(request, f"{recipient} 로 Transcript를 보냈습니다.")
    return redirect("core:pastor_audio_transcriber")


@pastor_required
def pastor_dashboard_view(request):
    scope_church = _get_access_scope_church(request.user)
    active_challenge = _get_active_challenge(scope_church)
    sermon_queryset = Sermon.objects.filter(Q(pastor_review_requested=True) | Q(is_published=True))
    if scope_church is not None:
        sermon_queryset = sermon_queryset.filter(church=scope_church)
    available_sermons = list(
        sermon_queryset
        .select_related("summary")
        .prefetch_related("daily_engagements", "weekly_challenges")
        .order_by("-created_at", "-id")
    )
    sermon = _get_default_pastor_sermon(available_sermons, active_challenge)
    selected_sermon_id = request.GET.get("sermon")
    if selected_sermon_id:
        try:
            sermon = next(item for item in available_sermons if item.pk == int(selected_sermon_id))
        except (StopIteration, ValueError):
            pass
    if sermon:
        return redirect("core:pastor_sermon_edit", pk=sermon.pk)

    return render(
        request,
        "core/pastor_dashboard.html",
        {
            "active_church": scope_church,
            "sermon": None,
            "summary": None,
            "active_challenge": None,
            "available_sermons": available_sermons,
            "weekly_report": None,
            "sermon_report": None,
            "daily_report": None,
            "quality_report": None,
            "member_reports": [],
            "pastor_menu": "sermon",
            "can_use_audio_transcriber": _can_use_audio_transcriber(request.user),
            **_build_church_nav_context(scope_church),
        },
    )


@pastor_required
def pastor_sermon_edit_view(request, pk):
    scope_church = _get_access_scope_church(request.user)
    sermon_queryset = Sermon.objects.prefetch_related("daily_engagements", "highlight_choices", "weekly_challenges")
    if scope_church is not None:
        sermon_queryset = sermon_queryset.filter(church=scope_church)
    sermon = get_object_or_404(sermon_queryset, pk=pk)
    if not sermon.pastor_review_requested and not sermon.is_published:
        messages.warning(request, "?꾩쭅 ?대뱶誘쇱뿉??紐⑺쉶??寃???붿껌??蹂대궡吏 ?딆? ?ㅺ탳?낅땲??")
        return redirect("core:pastor_dashboard")
    available_queryset = Sermon.objects.filter(Q(pastor_review_requested=True) | Q(is_published=True))
    if scope_church is not None:
        available_queryset = available_queryset.filter(church=scope_church)
    available_sermons = list(
        available_queryset
        .order_by("-created_at", "-id")
        .only("id", "title", "sermon_date")
    )
    current_public_sermon_id = get_current_public_sermon_id(church=sermon.church)
    publication_state = _get_publication_state(sermon, current_public_sermon_id)
    summary, _ = SermonSummary.objects.get_or_create(sermon=sermon)
    daily_qs = sermon.daily_engagements.order_by("day_number", "id")
    DailyFormSet = modelformset_factory(DailyEngagement, form=PastorDailyEngagementForm, extra=0)

    checklist = _build_pastor_publish_checklist(sermon)
    checklist_has_issues = any(not item["ok"] for item in checklist)

    if request.method == "POST":
        action = request.POST.get("action")
        sermon_form = PastorSermonEditForm(request.POST, instance=sermon, prefix="sermon")
        summary_form = PastorSermonSummaryForm(request.POST, instance=summary, prefix="summary")
        daily_formset = DailyFormSet(request.POST, queryset=daily_qs, prefix="days")

        if action == "unpublish":
            if not publication_state["is_current"]:
                messages.warning(request, "?꾩옱 怨듦컻 以묒씤 ?ㅺ탳留?怨듦컻 ?댁젣?????덉뒿?덈떎.")
                return redirect("core:pastor_sermon_edit", pk=sermon.pk)
            sermon.unpublish()
            messages.success(request, "?ㅺ탳 怨듦컻瑜??댁젣?덉뒿?덈떎.")
            return redirect("core:pastor_sermon_edit", pk=sermon.pk)

        if action == "regenerate":
            if sermon.is_published:
                messages.warning(request, "?대? 怨듦컻???ㅺ탳??援먯뿭???섏씠吏?먯꽌 ?ㅼ떆 ?뺣━?섏? ?딅룄濡?留됱븘?먯뿀?듬땲?? 吏곸젒 ?섏젙 ????ν빐 二쇱꽭??")
                return redirect("core:pastor_sermon_edit", pk=sermon.pk)
            try:
                generate_sermon_content(sermon)
            except AIContentGenerationError as exc:
                messages.error(request, f"AI ?댁슜 ?ㅼ떆 ?뺣━ ?ㅽ뙣: {exc}")
            else:
                messages.success(request, "AI媛 ??λ맂 ?먮쭑??湲곗??쇰줈 ?ㅺ탳 ?댁슜???ㅼ떆 ?뺣━?덉뒿?덈떎.")
            return redirect("core:pastor_sermon_edit", pk=sermon.pk)

        if sermon_form.is_valid() and summary_form.is_valid() and daily_formset.is_valid():
            publish_result = None
            publish_at = None
            with transaction.atomic():
                sermon_form.save()
                sermon.sync_weekly_challenge_schedule()
                summary_obj = summary_form.save(commit=False)
                summary_obj.sermon = sermon
                summary_obj.approved = True
                summary_obj.save()
                daily_formset.save()
                for choice in sermon.highlight_choices.all().order_by("order", "id"):
                    updated_text = request.POST.get(f"highlight_choice_{choice.id}", "").strip()
                    if updated_text:
                        choice.text = updated_text
                        choice.save(update_fields=["text"])
                sermon.approve_generated_content()
                if action == "publish":
                    checklist = _build_pastor_publish_checklist(sermon)
                    checklist_has_issues = any(not item["ok"] for item in checklist)
                    incomplete_items = [item["label"] for item in checklist if not item["ok"]]
                    if incomplete_items:
                        transaction.set_rollback(True)
                        messages.warning(request, "공개 전에 확인이 필요한 항목이 있습니다: " + ", ".join(incomplete_items[:4]))
                        return redirect("core:pastor_sermon_edit", pk=sermon.pk)
                    sermon.mark_pastor_publication_requested(request.user)
                    publish_result, publish_at = sermon.schedule_or_publish()

            if action == "publish":
                if publish_result == "scheduled":
                    publish_at_text = timezone.localtime(publish_at).strftime("%Y-%m-%d %H:%M")
                    messages.success(
                        request,
                        f"검토를 마쳤습니다. 실제 공개는 {publish_at_text}에 자동으로 진행되어 교인들에게 보여집니다.",
                    )
                else:
                    messages.success(request, "설교를 공개했습니다. 교인 화면에 바로 반영됩니다.")
            else:
                messages.success(request, "수정 내용을 저장했습니다.")
            return redirect("core:pastor_sermon_edit", pk=sermon.pk)

        messages.error(request, "?낅젰???댁슜???ㅼ떆 ?뺤씤??二쇱꽭??")
    else:
        sermon_form = PastorSermonEditForm(instance=sermon, prefix="sermon")
        summary_form = PastorSermonSummaryForm(instance=summary, prefix="summary")
        daily_formset = DailyFormSet(queryset=daily_qs, prefix="days")

    challenge = sermon.weekly_challenges.order_by("-week_start", "-id").first()
    highlight_choices = list(sermon.highlight_choices.all())
    highlight_vote_totals = {
        row["choice_id"]: row["total"]
        for row in (
            SermonHighlightVote.objects.filter(sermon=sermon)
            .values("choice_id")
            .annotate(total=Count("id"))
        )
    }
    for choice in highlight_choices:
        choice.vote_count = highlight_vote_totals.get(choice.id, 0)

    return render(
        request,
        "core/pastor_sermon_edit.html",
        {
            "active_church": scope_church,
            "sermon": sermon,
            "challenge": challenge,
            "available_sermons": available_sermons,
            "sermon_form": sermon_form,
            "summary_form": summary_form,
            "daily_formset": daily_formset,
            "highlight_choices": highlight_choices,
            "publish_checklist": checklist,
            "checklist_has_issues": checklist_has_issues,
            "publication_state_label": publication_state["label"],
            "is_current_public_sermon": publication_state["is_current"],
            "pastor_menu": "sermon",
            "can_use_audio_transcriber": _can_use_audio_transcriber(request.user),
            **_build_church_nav_context(scope_church),
        },
    )


@pastor_required
def pastor_reports_view(request):
    scope_church = _get_access_scope_church(request.user)
    challenge_queryset = WeeklyChallenge.objects.select_related("sermon")
    if scope_church is not None:
        challenge_queryset = challenge_queryset.filter(sermon__church=scope_church)
    available_challenges = list(challenge_queryset.order_by("-week_start", "-id"))

    selected_challenge = None
    selected_challenge_id = request.GET.get("challenge")
    if selected_challenge_id:
        try:
            selected_challenge = next(
                challenge for challenge in available_challenges if challenge.pk == int(selected_challenge_id)
            )
        except (StopIteration, ValueError):
            selected_challenge = None

    if selected_challenge is None and available_challenges:
        selected_challenge = available_challenges[1] if len(available_challenges) > 1 else available_challenges[0]

    weekly_report = sync_weekly_participation_report(selected_challenge) if selected_challenge else None
    daily_report = sync_daily_action_report(selected_challenge) if selected_challenge else None
    quality_report = sync_content_quality_report(selected_challenge) if selected_challenge else None
    sermon = selected_challenge.sermon if selected_challenge else None
    sermon_report = sync_sermon_participation_report(sermon) if sermon else None
    highlight_summary = _build_highlight_summary(sermon)
    member_queryset = UserProfile.objects.select_related("user")
    if scope_church is not None:
        member_queryset = member_queryset.filter(church=scope_church)
    member_reports = [
        sync_user_participation_report(profile.user)
        for profile in member_queryset.order_by("-points", "user__username")[:20]
    ]

    return render(
        request,
        "core/pastor_reports.html",
        {
            "active_church": scope_church,
            "active_challenge": selected_challenge,
            "available_challenges": available_challenges,
            "weekly_report": weekly_report,
            "daily_report": daily_report,
            "quality_report": quality_report,
            "sermon_report": sermon_report,
            "highlight_summary": highlight_summary,
            "member_reports": member_reports,
            "pastor_menu": "reports",
            "can_use_audio_transcriber": _can_use_audio_transcriber(request.user),
            **_build_church_nav_context(scope_church),
        },
    )


@pastor_required
def pastor_members_view(request):
    scope_church = _get_access_scope_church(request.user)
    profile_queryset = UserProfile.objects.select_related("user")
    if scope_church is not None:
        profile_queryset = profile_queryset.filter(church=scope_church)
    profiles = list(profile_queryset.order_by("-points", "user__username"))
    all_reports = [sync_user_participation_report(profile.user) for profile in profiles]

    search_query = (request.GET.get("q") or "").strip().lower()
    role_filter = (request.GET.get("role") or "").strip()
    status_filter = (request.GET.get("status") or "").strip()

    member_reports = []
    for report in all_reports:
        if search_query:
            haystacks = [
                (report.display_name or "").lower(),
                (report.username or "").lower(),
                (report.member_role or "").lower(),
            ]
            if not any(search_query in value for value in haystacks):
                continue

        if role_filter and report.member_role != role_filter:
            continue

        if status_filter == "active" and not report.active_this_week:
            continue
        if status_filter == "inactive" and report.active_this_week:
            continue
        if status_filter == "streak" and not report.recent_two_week_streak:
            continue
        if status_filter == "away" and not report.inactive_for_two_weeks:
            continue

        member_reports.append(report)

    role_options = sorted({report.member_role for report in all_reports if report.member_role})
    summary = {
        "member_count": len(all_reports),
        "active_count": sum(1 for report in all_reports if report.active_this_week),
        "streak_count": sum(1 for report in all_reports if report.recent_two_week_streak),
        "away_count": sum(1 for report in all_reports if report.inactive_for_two_weeks),
    }

    return render(
        request,
        "core/pastor_members.html",
        {
            "active_church": scope_church,
            "member_reports": member_reports,
            "role_options": role_options,
            "search_query": request.GET.get("q", ""),
            "selected_role": role_filter,
            "selected_status": status_filter,
            "member_summary": summary,
            "pastor_menu": "members",
            "can_use_audio_transcriber": _can_use_audio_transcriber(request.user),
            **_build_church_nav_context(scope_church),
        },
    )

