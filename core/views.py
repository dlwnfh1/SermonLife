import re

from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.forms import AuthenticationForm
from django.db import transaction
from django.db.models import Count, Sum
from django.forms import modelformset_factory
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import (
    PastorDailyEngagementForm,
    PastorSermonEditForm,
    PastorSermonSummaryForm,
    SermonLifeSignUpForm,
)
from .models import (
    DailyEngagement,
    DailyMissionCompletion,
    DailyQuizAttempt,
    DailyReflectionResponse,
    PointLedger,
    PointSource,
    SermonAudioClip,
    SermonAudioClipKind,
    Sermon,
    SermonHighlightChoice,
    SermonHighlightVote,
    SermonStatus,
    SermonSummary,
    UserProfile,
    WeeklyChallenge,
    get_current_public_sermon_id,
)
from .services.ai_generation import AIContentGenerationError, generate_sermon_content
from .services.transcript_service import TranscriptFetchError, transcribe_uploaded_audio
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


def _get_or_create_profile(user):
    if not user.is_authenticated:
        return None
    profile, _ = UserProfile.objects.get_or_create(user=user)
    return profile


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
            for sentence in re.split(r"(?<=[.!?。！？])\s+", compact)
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


def _get_active_challenge():
    return (
        WeeklyChallenge.objects.filter(
            is_active=True,
            sermon__status=SermonStatus.PUBLISHED,
            sermon__is_published=True,
        )
        .select_related("sermon", "sermon__summary")
        .prefetch_related("daily_engagements")
        .first()
    )


def _get_default_pastor_sermon(available_sermons, active_challenge=None):
    review_sermon = next(
        (
            sermon
            for sermon in available_sermons
            if not sermon.is_published and (sermon.ai_generated or sermon.status != SermonStatus.DRAFT)
        ),
        None,
    )
    if review_sermon:
        return review_sermon

    unpublished_sermon = next((sermon for sermon in available_sermons if not sermon.is_published), None)
    if unpublished_sermon:
        return unpublished_sermon

    if active_challenge:
        return active_challenge.sermon

    return available_sermons[0] if available_sermons else None


def _get_publication_state(sermon, current_public_sermon_id=None):
    if not sermon:
        return {"label": "미공개", "is_current": False}
    current_public_sermon_id = current_public_sermon_id or get_current_public_sermon_id()
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
    highlight_messages = []
    for message in list(messages.get_messages(request)):
        text = str(message)
        if "마음에 남은 말씀" in text or "공감" in text:
            highlight_messages.append({"text": text, "tags": message.tags})
        else:
            if not hasattr(request, "_remaining_messages"):
                request._remaining_messages = []
            request._remaining_messages.append({"text": text, "tags": message.tags})

    challenge = _get_active_challenge()
    sermon = challenge.sermon if challenge else None
    summary = _get_summary(sermon)
    profile = _get_or_create_profile(request.user)

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
    weekly_base_max_points = (QUIZ_POINTS + REFLECTION_POINTS + MISSION_POINTS + DAILY_COMPLETION_POINTS) * 5
    weekly_total_max_points = weekly_base_max_points + WEEKLY_COMPLETION_POINTS

    return {
        "profile": profile,
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
    }


def _get_released_daily_or_404(pk):
    challenge = _get_active_challenge()
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


def _redirect_to_today_set():
    return redirect(f"{reverse('core:home')}#today-set")


def _is_pastor_user(user):
    if not user.is_authenticated:
        return False
    if user.is_superuser or user.is_staff:
        return True
    profile = UserProfile.objects.filter(user=user).first()
    return bool(profile and profile.member_role == "pastor")


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
                "label": f"Day {daily.day_number}의 퀴즈/묵상/미션이 모두 입력되어 있습니다",
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
def watch_sermon_view(request):
    challenge = _get_active_challenge()
    sermon = challenge.sermon if challenge else None
    if not sermon:
        raise Http404
    return render(
        request,
        "core/watch_sermon.html",
        {
            "challenge": challenge,
            "sermon": sermon,
        },
    )


@login_required
def read_sermon_view(request):
    challenge = _get_active_challenge()
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
    return render(
        request,
        "core/read_sermon.html",
        {
            "challenge": challenge,
            "sermon": sermon,
            "transcript_paragraphs": transcript_paragraphs,
            "current_daily": current_daily,
            "current_day_number": current_day_number,
        },
    )


@login_required
def my_history_view(request):
    profile = _get_or_create_profile(request.user)
    challenge = _get_active_challenge()
    weekly_points = 0
    if challenge:
        weekly_points = (
            PointLedger.objects.filter(user=request.user, challenge=challenge)
            .aggregate(total=Sum("points"))
            .get("total")
            or 0
        )

    challenge_ids = list(
        PointLedger.objects.filter(user=request.user)
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

    recent_entries = PointLedger.objects.filter(user=request.user).select_related("challenge", "sermon")[:20]

    return render(
        request,
        "core/my_history.html",
        {
            "profile": profile,
            "current_challenge": challenge,
            "weekly_points": weekly_points,
            "weekly_history": weekly_history,
            "recent_entries": recent_entries,
        },
    )


def home_view(request):
    if not request.user.is_authenticated:
        return redirect("core:login")
    return render(request, "core/home.html", _build_home_context(request))


def signup_view(request):
    if request.user.is_authenticated:
        return redirect("core:home")

    if request.method == "POST":
        form = SermonLifeSignUpForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.first_name = form.cleaned_data["first_name"]
            user.save()
            UserProfile.objects.update_or_create(
                user=user,
                defaults={"member_role": form.cleaned_data["member_role"]},
            )
            login(request, user)
            messages.success(request, "회원가입이 완료되었습니다. 이번 주 설교 루틴을 시작해 보세요.")
            return redirect("core:home")
        if "username" in form.errors:
            messages.error(request, "이미 사용 중인 아이디입니다.")
        else:
            messages.error(request, "입력한 내용을 다시 확인해 주세요.")
    else:
        form = SermonLifeSignUpForm()

    return render(request, "core/signup.html", {"form": form})


def login_view(request):
    if request.user.is_authenticated:
        return redirect("core:home")

    form = AuthenticationForm(request, data=request.POST or None)
    form.fields["username"].label = "아이디"
    form.fields["password"].label = "비밀번호"
    form.fields["username"].widget.attrs.update({"placeholder": "아이디"})
    form.fields["password"].widget.attrs.update({"placeholder": "비밀번호"})
    if request.method == "POST" and form.is_valid():
        login(request, form.get_user())
        messages.success(request, "로그인되었습니다.")
        return redirect("core:home")

    return render(request, "core/login.html", {"form": form})


def logout_view(request):
    logout(request)
    messages.success(request, "로그아웃되었습니다.")
    return redirect("core:login")


@login_required
@require_POST
def submit_daily_quiz_view(request, pk):
    daily = _get_released_daily_or_404(pk)
    selected_answer = request.POST.get("selected_answer", "").strip()
    if selected_answer not in daily.choices:
        messages.error(request, "퀴즈 보기를 선택한 뒤 제출해 주세요.")
        return _redirect_to_today_set()

    result = submit_daily_quiz(
        user=request.user,
        daily_engagement=daily,
        selected_answer=selected_answer,
    )
    attempt = result["attempt"]
    bonus_suffix = ""
    if result["daily_bonus_awarded"]:
        bonus_suffix += " 하루 완료 보너스 3점이 추가되었습니다."
    if result["weekly_bonus_awarded"]:
        bonus_suffix += " 5일 완주 보너스 20점이 추가되었습니다."

    if result["is_update"]:
        if attempt.is_correct:
            messages.warning(request, f"이미 제출한 퀴즈입니다. 점수는 1회만 적립됩니다.{bonus_suffix}")
        else:
            messages.warning(request, "이미 제출한 퀴즈입니다. 다시 설교 흐름을 확인해 보세요.")
    elif attempt.is_correct:
        messages.success(request, f"정답입니다. 퀴즈 5점을 받았습니다.{bonus_suffix}")
    else:
        messages.warning(request, "정답이 아닙니다. 다시 설교 흐름을 확인해 보세요.")
    return _redirect_to_today_set()


@login_required
@require_POST
def submit_reflection_view(request, pk):
    daily = _get_released_daily_or_404(pk)
    response_text = request.POST.get("response_text", "")
    try:
        result = submit_reflection(
            user=request.user,
            daily_engagement=daily,
            response_text=response_text,
        )
    except ValueError as exc:
        messages.error(request, str(exc))
        return _redirect_to_today_set()

    bonus_suffix = ""
    if result["daily_bonus_awarded"]:
        bonus_suffix += " 하루 완료 보너스 3점이 추가되었습니다."
    if result["weekly_bonus_awarded"]:
        bonus_suffix += " 5일 완주 보너스 20점이 추가되었습니다."

    if result["is_update"]:
        messages.warning(request, f"이미 저장한 묵상입니다. 점수는 1회만 적립됩니다.{bonus_suffix}")
    else:
        messages.success(request, f"묵상 답변이 저장되었습니다. 5점을 받았습니다.{bonus_suffix}")
    return _redirect_to_today_set()


@login_required
@require_POST
def complete_mission_view(request, pk):
    daily = _get_released_daily_or_404(pk)
    note = request.POST.get("mission_note", "")
    result = complete_mission(
        user=request.user,
        daily_engagement=daily,
        note=note,
    )

    bonus_suffix = ""
    if result["daily_bonus_awarded"]:
        bonus_suffix += " 하루 완료 보너스 3점이 추가되었습니다."
    if result["weekly_bonus_awarded"]:
        bonus_suffix += " 5일 완주 보너스 20점이 추가되었습니다."

    if result["is_update"]:
        messages.warning(request, f"이미 완료한 미션입니다. 점수는 1회만 적립됩니다.{bonus_suffix}")
    else:
        messages.success(request, f"오늘의 미션이 완료되었습니다. 7점을 받았습니다.{bonus_suffix}")
    return _redirect_to_today_set()


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
    challenge = _get_active_challenge()
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
        messages.success(request, "가장 마음에 남은 말씀 선택을 바꿨습니다.")
    else:
        messages.success(request, "가장 마음에 남은 말씀에 투표했습니다.")

    return redirect(f"{reverse('core:home')}#highlight-panel")


@pastor_required
def pastor_dashboard_view(request):
    active_challenge = _get_active_challenge()
    available_sermons = list(
        Sermon.objects.select_related("summary")
        .prefetch_related("daily_engagements", "weekly_challenges")
        .order_by("-sermon_date", "-id")
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
        },
    )


@pastor_required
def pastor_sermon_edit_view(request, pk):
    sermon = get_object_or_404(
        Sermon.objects.prefetch_related("daily_engagements", "highlight_choices", "weekly_challenges"),
        pk=pk,
    )
    available_sermons = list(
        Sermon.objects.order_by("-sermon_date", "-id").only("id", "title", "sermon_date")
    )
    current_public_sermon_id = get_current_public_sermon_id()
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
                messages.warning(request, "현재 공개 중인 설교만 공개 해제할 수 있습니다.")
                return redirect("core:pastor_sermon_edit", pk=sermon.pk)
            sermon.unpublish()
            messages.success(request, "설교 공개를 해제했습니다.")
            return redirect("core:pastor_sermon_edit", pk=sermon.pk)

        if action == "regenerate":
            if sermon.is_published:
                messages.warning(request, "이미 공개된 설교는 교역자 페이지에서 다시 정리하지 않도록 막아두었습니다. 직접 수정 후 저장해 주세요.")
                return redirect("core:pastor_sermon_edit", pk=sermon.pk)
            try:
                generate_sermon_content(sermon)
            except AIContentGenerationError as exc:
                messages.error(request, f"AI 내용 다시 정리 실패: {exc}")
            else:
                messages.success(request, "AI가 저장된 자막을 기준으로 설교 내용을 다시 정리했습니다.")
            return redirect("core:pastor_sermon_edit", pk=sermon.pk)

        if sermon_form.is_valid() and summary_form.is_valid() and daily_formset.is_valid():
            with transaction.atomic():
                sermon_form.save()
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
                    sermon.publish()

            if action == "publish":
                messages.success(request, "설교를 공개했습니다. 교인 화면에 바로 반영됩니다.")
            else:
                messages.success(request, "수정 내용을 저장했습니다.")
            return redirect("core:pastor_sermon_edit", pk=sermon.pk)

        messages.error(request, "입력한 내용을 다시 확인해 주세요.")
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
        },
    )


@pastor_required
def pastor_reports_view(request):
    available_challenges = list(
        WeeklyChallenge.objects.filter(
            sermon__status=SermonStatus.PUBLISHED,
            sermon__is_published=True,
        )
        .select_related("sermon")
        .order_by("-week_start", "-id")
    )

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
    member_reports = [
        sync_user_participation_report(profile.user)
        for profile in UserProfile.objects.select_related("user").order_by("-points", "user__username")[:20]
    ]

    return render(
        request,
        "core/pastor_reports.html",
        {
            "active_challenge": selected_challenge,
            "available_challenges": available_challenges,
            "weekly_report": weekly_report,
            "daily_report": daily_report,
            "quality_report": quality_report,
            "sermon_report": sermon_report,
            "highlight_summary": highlight_summary,
            "member_reports": member_reports,
            "pastor_menu": "reports",
        },
    )


@pastor_required
def pastor_members_view(request):
    profiles = list(UserProfile.objects.select_related("user").order_by("-points", "user__username"))
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
            "member_reports": member_reports,
            "role_options": role_options,
            "search_query": request.GET.get("q", ""),
            "selected_role": role_filter,
            "selected_status": status_filter,
            "member_summary": summary,
            "pastor_menu": "members",
        },
    )
