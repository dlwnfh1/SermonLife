from django.db import transaction
from django.utils import timezone

from core.models import (
    DailyMissionCompletion,
    DailyQuizAttempt,
    DailyReflectionResponse,
    PointLedger,
    PointSource,
    UserProfile,
)


QUIZ_POINTS = 5
REFLECTION_POINTS = 5
MISSION_POINTS = 7
DAILY_COMPLETION_POINTS = 3
WEEKLY_COMPLETION_POINTS = 20
MIN_REFLECTION_LENGTH = 10


def _ensure_profile(user):
    profile, _ = UserProfile.objects.get_or_create(user=user)
    return profile


def _award_points(*, user, challenge, sermon, source, points, note):
    ledger, created = PointLedger.objects.get_or_create(
        user=user,
        challenge=challenge,
        sermon=sermon,
        source=source,
        note=note,
        defaults={"points": points},
    )
    if created:
        profile = _ensure_profile(user)
        profile.points += points
        profile.save(update_fields=["points"])
    return created, ledger


def _has_daily_completion(user, daily_engagement):
    has_quiz = DailyQuizAttempt.objects.filter(user=user, daily_engagement=daily_engagement).exists()
    has_reflection = DailyReflectionResponse.objects.filter(user=user, daily_engagement=daily_engagement).exists()
    has_mission = DailyMissionCompletion.objects.filter(
        user=user,
        daily_engagement=daily_engagement,
        completed=True,
    ).exists()
    return has_quiz and has_reflection and has_mission


def _maybe_award_daily_bonus(user, daily_engagement):
    if not _has_daily_completion(user, daily_engagement):
        return False
    created, _ = _award_points(
        user=user,
        challenge=daily_engagement.challenge,
        sermon=daily_engagement.sermon,
        source=PointSource.DAILY_BONUS,
        points=DAILY_COMPLETION_POINTS,
        note=f"day:{daily_engagement.day_number}",
    )
    return created


def _maybe_award_weekly_bonus(user, challenge):
    daily_items = list(challenge.daily_engagements.filter(approved=True).order_by("day_number"))
    if len(daily_items) < 5:
        return False
    if not all(_has_daily_completion(user, item) for item in daily_items[:5]):
        return False
    created, _ = _award_points(
        user=user,
        challenge=challenge,
        sermon=challenge.sermon,
        source=PointSource.WEEKLY_BONUS,
        points=WEEKLY_COMPLETION_POINTS,
        note="week_complete",
    )
    return created


@transaction.atomic
def submit_daily_quiz(*, user, daily_engagement, selected_answer):
    existing_attempt = DailyQuizAttempt.objects.filter(
        user=user,
        daily_engagement=daily_engagement,
    ).first()

    if existing_attempt is not None:
        return {
            "attempt": existing_attempt,
            "is_update": True,
            "points_awarded": False,
            "daily_bonus_awarded": False,
            "weekly_bonus_awarded": False,
        }

    attempt = DailyQuizAttempt.objects.create(
        user=user,
        daily_engagement=daily_engagement,
        challenge=daily_engagement.challenge,
        selected_answer=selected_answer,
        is_correct=selected_answer == daily_engagement.quiz_answer,
    )
    points_awarded = False
    if attempt.is_correct:
        points_awarded, _ = _award_points(
            user=user,
            challenge=daily_engagement.challenge,
            sermon=daily_engagement.sermon,
            source=PointSource.QUIZ,
            points=QUIZ_POINTS,
            note=f"day:{daily_engagement.day_number}",
        )
    daily_bonus_awarded = _maybe_award_daily_bonus(user, daily_engagement)
    weekly_bonus_awarded = _maybe_award_weekly_bonus(user, daily_engagement.challenge)
    return {
        "attempt": attempt,
        "is_update": existing_attempt is not None,
        "points_awarded": points_awarded,
        "daily_bonus_awarded": daily_bonus_awarded,
        "weekly_bonus_awarded": weekly_bonus_awarded,
    }


@transaction.atomic
def submit_reflection(*, user, daily_engagement, response_text):
    cleaned = response_text.strip()
    if len(cleaned) < MIN_REFLECTION_LENGTH:
        raise ValueError(f"묵상 답변은 {MIN_REFLECTION_LENGTH}자 이상 입력해야 합니다.")

    existing_response = DailyReflectionResponse.objects.filter(
        user=user,
        daily_engagement=daily_engagement,
    ).first()
    response, _ = DailyReflectionResponse.objects.update_or_create(
        user=user,
        daily_engagement=daily_engagement,
        defaults={
            "challenge": daily_engagement.challenge,
            "response_text": cleaned,
        },
    )
    points_awarded, _ = _award_points(
        user=user,
        challenge=daily_engagement.challenge,
        sermon=daily_engagement.sermon,
        source=PointSource.REFLECTION,
        points=REFLECTION_POINTS,
        note=f"day:{daily_engagement.day_number}",
    )
    daily_bonus_awarded = _maybe_award_daily_bonus(user, daily_engagement)
    weekly_bonus_awarded = _maybe_award_weekly_bonus(user, daily_engagement.challenge)
    return {
        "response": response,
        "is_update": existing_response is not None,
        "points_awarded": points_awarded,
        "daily_bonus_awarded": daily_bonus_awarded,
        "weekly_bonus_awarded": weekly_bonus_awarded,
    }


@transaction.atomic
def complete_mission(*, user, daily_engagement, note=""):
    existing_completion = DailyMissionCompletion.objects.filter(
        user=user,
        daily_engagement=daily_engagement,
        completed=True,
    ).first()
    completion, _ = DailyMissionCompletion.objects.update_or_create(
        user=user,
        daily_engagement=daily_engagement,
        defaults={
            "challenge": daily_engagement.challenge,
            "completed": True,
            "note": note.strip(),
            "completed_at": timezone.now(),
        },
    )

    points_awarded, _ = _award_points(
        user=user,
        challenge=daily_engagement.challenge,
        sermon=daily_engagement.sermon,
        source=PointSource.MISSION,
        points=MISSION_POINTS,
        note=f"day:{daily_engagement.day_number}",
    )
    daily_bonus_awarded = _maybe_award_daily_bonus(user, daily_engagement)
    weekly_bonus_awarded = _maybe_award_weekly_bonus(user, daily_engagement.challenge)
    return {
        "completion": completion,
        "is_update": existing_completion is not None,
        "points_awarded": points_awarded,
        "daily_bonus_awarded": daily_bonus_awarded,
        "weekly_bonus_awarded": weekly_bonus_awarded,
    }
