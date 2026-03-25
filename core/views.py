from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.db.models import Sum
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import SermonLifeSignUpForm
from .models import (
    DailyEngagement,
    DailyMissionCompletion,
    DailyQuizAttempt,
    DailyReflectionResponse,
    PointLedger,
    PointSource,
    SermonStatus,
    SermonSummary,
    UserProfile,
    WeeklyChallenge,
)
from .services.engagement import complete_mission, submit_daily_quiz, submit_reflection


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
    challenge = _get_active_challenge()
    sermon = challenge.sermon if challenge else None
    summary = _get_summary(sermon)

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
    if challenge:
        current_day_number = challenge.current_day_number(timezone.localdate())
        daily_engagements = list(
            challenge.daily_engagements.filter(approved=True).order_by("day_number")
        )
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

    return {
        "challenge": challenge,
        "sermon": sermon,
        "weekly_points": weekly_points,
        "weekly_bonus_awarded": weekly_bonus_awarded,
        "completed_days_count": completed_days_count,
        "overview": getattr(summary, "overview", "") if summary else "",
        "outline_points": getattr(summary, "outline_points", []) if summary else [],
        "key_points": [
            point
            for point in [
                getattr(summary, "key_point1", ""),
                getattr(summary, "key_point2", ""),
                getattr(summary, "key_point3", ""),
            ]
            if point
        ],
        "daily_engagements": daily_engagements,
        "current_daily": current_daily,
        "current_day_number": current_day_number,
        "current_daily_state": current_daily_state,
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
