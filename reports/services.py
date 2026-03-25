from decimal import Decimal, ROUND_HALF_UP

from django.contrib.auth import get_user_model
from django.db.models import Max, Sum
from django.utils import timezone

from core.models import (
    DailyMissionCompletion,
    DailyQuizAttempt,
    DailyReflectionResponse,
    PointLedger,
    PointSource,
    Sermon,
    SermonStatus,
    UserProfile,
    WeeklyChallenge,
)

from .models import (
    ContentQualityReport,
    DailyActionReport,
    SermonParticipationReport,
    UserParticipationReport,
    WeeklyParticipationReport,
)

User = get_user_model()


def _decimal_rate(numerator, denominator):
    if not denominator:
        return Decimal("0.0")
    value = (Decimal(str(numerator)) / Decimal(str(denominator))) * Decimal("100")
    return value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def _decimal_average(total, count):
    if not count:
        return Decimal("0.0")
    value = Decimal(str(total)) / Decimal(str(count))
    return value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def _participant_count_for_challenge(challenge):
    return len(set(PointLedger.objects.filter(challenge=challenge).values_list("user_id", flat=True)))


def _published_challenges():
    return (
        WeeklyChallenge.objects.filter(sermon__status=SermonStatus.PUBLISHED, sermon__is_published=True)
        .select_related("sermon")
        .prefetch_related("daily_engagements")
        .order_by("-week_start", "-id")
    )


def _build_completion_rows(challenge):
    participant_count = _participant_count_for_challenge(challenge)
    day_rows = []
    for item in challenge.daily_engagements.filter(approved=True).order_by("day_number"):
        quiz_users = set(DailyQuizAttempt.objects.filter(challenge=challenge, daily_engagement=item).values_list("user_id", flat=True))
        reflection_users = set(DailyReflectionResponse.objects.filter(challenge=challenge, daily_engagement=item).values_list("user_id", flat=True))
        mission_users = set(
            DailyMissionCompletion.objects.filter(challenge=challenge, daily_engagement=item, completed=True).values_list(
                "user_id", flat=True
            )
        )
        completed_count = len(quiz_users & reflection_users & mission_users)
        day_rows.append(
            {
                "day_number": item.day_number,
                "title": item.title,
                "completed_count": completed_count,
                "completion_rate": float(_decimal_rate(completed_count, participant_count)),
            }
        )
    return participant_count, day_rows


def sync_weekly_participation_report(challenge):
    participant_count, day_rows = _build_completion_rows(challenge)
    total_points = PointLedger.objects.filter(challenge=challenge).aggregate(total=Sum("points")).get("total") or 0
    most_completed_day = max(day_rows, key=lambda row: row["completed_count"], default=None)
    least_completed_day = min(day_rows, key=lambda row: row["completed_count"], default=None)
    defaults = {
        "sermon": challenge.sermon,
        "title": challenge.title,
        "week_start": challenge.week_start,
        "week_end": challenge.week_end,
        "participant_count": participant_count,
        "total_points": total_points,
        "most_completed_day_label": f"Day {most_completed_day['day_number']} | {most_completed_day['title']}" if most_completed_day else "",
        "most_completed_day_count": most_completed_day["completed_count"] if most_completed_day else 0,
        "most_completed_day_rate": Decimal(str(most_completed_day["completion_rate"])) if most_completed_day else Decimal("0"),
        "least_completed_day_label": f"Day {least_completed_day['day_number']} | {least_completed_day['title']}" if least_completed_day else "",
        "least_completed_day_count": least_completed_day["completed_count"] if least_completed_day else 0,
        "least_completed_day_rate": Decimal(str(least_completed_day["completion_rate"])) if least_completed_day else Decimal("0"),
        "day_rows": day_rows,
    }
    report, _ = WeeklyParticipationReport.objects.update_or_create(challenge=challenge, defaults=defaults)
    return report


def sync_sermon_participation_report(sermon):
    challenges = sermon.weekly_challenges.order_by("-week_start", "-id")
    primary_challenge = challenges.first()
    participant_count = len(set(PointLedger.objects.filter(sermon=sermon).values_list("user_id", flat=True)))
    total_points = PointLedger.objects.filter(sermon=sermon).aggregate(total=Sum("points")).get("total") or 0
    quiz_participant_count = DailyQuizAttempt.objects.filter(challenge__sermon=sermon).values("user_id").distinct().count()
    reflection_participant_count = DailyReflectionResponse.objects.filter(challenge__sermon=sermon).values("user_id").distinct().count()
    mission_participant_count = DailyMissionCompletion.objects.filter(challenge__sermon=sermon, completed=True).values("user_id").distinct().count()
    weekly_completer_count = PointLedger.objects.filter(sermon=sermon, source=PointSource.WEEKLY_BONUS).values("user_id").distinct().count()
    action_rows = [
        {"label": "퀴즈 참여", "count": quiz_participant_count, "rate": float(_decimal_rate(quiz_participant_count, participant_count))},
        {"label": "묵상 응답", "count": reflection_participant_count, "rate": float(_decimal_rate(reflection_participant_count, participant_count))},
        {"label": "미션 완료", "count": mission_participant_count, "rate": float(_decimal_rate(mission_participant_count, participant_count))},
        {"label": "5일 완주", "count": weekly_completer_count, "rate": float(_decimal_rate(weekly_completer_count, participant_count))},
    ]
    defaults = {
        "primary_challenge": primary_challenge,
        "title": sermon.title,
        "sermon_date": sermon.sermon_date,
        "participant_count": participant_count,
        "total_points": total_points,
        "average_points_per_participant": _decimal_average(total_points, participant_count),
        "quiz_participant_count": quiz_participant_count,
        "reflection_participant_count": reflection_participant_count,
        "mission_participant_count": mission_participant_count,
        "weekly_completer_count": weekly_completer_count,
        "weekly_completion_rate": _decimal_rate(weekly_completer_count, participant_count),
        "action_rows": action_rows,
    }
    report, _ = SermonParticipationReport.objects.update_or_create(sermon=sermon, defaults=defaults)
    return report


def sync_daily_action_report(challenge):
    participant_count = _participant_count_for_challenge(challenge)
    day_rows = []
    for item in challenge.daily_engagements.filter(approved=True).order_by("day_number"):
        quiz_count = DailyQuizAttempt.objects.filter(challenge=challenge, daily_engagement=item).count()
        reflection_count = DailyReflectionResponse.objects.filter(challenge=challenge, daily_engagement=item).count()
        mission_count = DailyMissionCompletion.objects.filter(challenge=challenge, daily_engagement=item, completed=True).count()
        daily_bonus_count = PointLedger.objects.filter(challenge=challenge, source=PointSource.DAILY_BONUS, note=f"day_{item.day_number}_complete").count()
        weekly_bonus_cumulative = PointLedger.objects.filter(challenge=challenge, source=PointSource.WEEKLY_BONUS).count()
        engagement_score = quiz_count + reflection_count + mission_count + daily_bonus_count
        day_rows.append(
            {
                "day_number": item.day_number,
                "title": item.title,
                "quiz_count": quiz_count,
                "reflection_count": reflection_count,
                "mission_count": mission_count,
                "daily_bonus_count": daily_bonus_count,
                "weekly_bonus_cumulative": weekly_bonus_cumulative,
                "engagement_score": engagement_score,
                "quiz_rate": float(_decimal_rate(quiz_count, participant_count)),
                "reflection_rate": float(_decimal_rate(reflection_count, participant_count)),
                "mission_rate": float(_decimal_rate(mission_count, participant_count)),
            }
        )
    strongest_day = max(day_rows, key=lambda row: row["engagement_score"], default=None)
    weakest_day = min(day_rows, key=lambda row: row["engagement_score"], default=None)
    defaults = {
        "sermon": challenge.sermon,
        "title": challenge.title,
        "week_start": challenge.week_start,
        "week_end": challenge.week_end,
        "participant_count": participant_count,
        "day_rows": day_rows,
        "strongest_day_label": f"Day {strongest_day['day_number']} | {strongest_day['title']}" if strongest_day else "",
        "weakest_day_label": f"Day {weakest_day['day_number']} | {weakest_day['title']}" if weakest_day else "",
    }
    report, _ = DailyActionReport.objects.update_or_create(challenge=challenge, defaults=defaults)
    return report


def sync_user_participation_report(user):
    profile = UserProfile.objects.filter(user=user).first()
    total_points = PointLedger.objects.filter(user=user).aggregate(total=Sum("points")).get("total") or 0
    weekly_completer_count = PointLedger.objects.filter(user=user, source=PointSource.WEEKLY_BONUS).values("challenge_id").distinct().count()
    last_activity_at = PointLedger.objects.filter(user=user).aggregate(last=Max("created_at")).get("last")
    active_challenge = _published_challenges().filter(is_active=True).first()
    active_this_week = bool(active_challenge and PointLedger.objects.filter(user=user, challenge=active_challenge).exists())
    recent_challenges = list(_published_challenges()[:4])
    recent_week_rows = []
    for challenge in recent_challenges:
        points = PointLedger.objects.filter(user=user, challenge=challenge).aggregate(total=Sum("points")).get("total") or 0
        completed = PointLedger.objects.filter(user=user, challenge=challenge, source=PointSource.WEEKLY_BONUS).exists()
        recent_week_rows.append({"title": challenge.title, "week_start": challenge.week_start.isoformat(), "points": points, "completed": completed})
    latest_two = recent_challenges[:2]
    recent_two_week_streak = bool(len(latest_two) == 2 and all(PointLedger.objects.filter(user=user, challenge=challenge).exists() for challenge in latest_two))
    inactive_for_two_weeks = bool(not last_activity_at or last_activity_at < timezone.now() - timezone.timedelta(days=14))
    defaults = {
        "username": user.get_username(),
        "display_name": user.first_name or "",
        "member_role": profile.get_member_role_display() if profile else "",
        "total_points": total_points,
        "streak_days": profile.streak_days if profile else 0,
        "weekly_completer_count": weekly_completer_count,
        "active_this_week": active_this_week,
        "recent_two_week_streak": recent_two_week_streak,
        "inactive_for_two_weeks": inactive_for_two_weeks,
        "last_activity_at": last_activity_at,
        "recent_week_rows": recent_week_rows,
    }
    report, _ = UserParticipationReport.objects.update_or_create(user=user, defaults=defaults)
    return report


def sync_content_quality_report(challenge):
    participant_count = _participant_count_for_challenge(challenge)
    quality_rows = []
    for item in challenge.daily_engagements.filter(approved=True).order_by("day_number"):
        attempts = DailyQuizAttempt.objects.filter(challenge=challenge, daily_engagement=item)
        quiz_attempt_count = attempts.count()
        quiz_correct_count = attempts.filter(is_correct=True).count()
        quiz_accuracy_rate = _decimal_rate(quiz_correct_count, quiz_attempt_count)
        reflection_count = DailyReflectionResponse.objects.filter(challenge=challenge, daily_engagement=item).count()
        mission_count = DailyMissionCompletion.objects.filter(challenge=challenge, daily_engagement=item, completed=True).count()
        reflection_rate = _decimal_rate(reflection_count, participant_count)
        mission_rate = _decimal_rate(mission_count, participant_count)
        flags = []
        if quiz_attempt_count and quiz_accuracy_rate < Decimal("50.0"):
            flags.append("퀴즈 정답률 낮음")
        if participant_count and reflection_rate < Decimal("40.0"):
            flags.append("묵상 반응 낮음")
        if participant_count and mission_rate < Decimal("40.0"):
            flags.append("미션 반응 낮음")
        quality_rows.append(
            {
                "day_number": item.day_number,
                "title": item.title,
                "quiz_attempt_count": quiz_attempt_count,
                "quiz_correct_count": quiz_correct_count,
                "quiz_accuracy_rate": float(quiz_accuracy_rate),
                "reflection_count": reflection_count,
                "reflection_rate": float(reflection_rate),
                "mission_count": mission_count,
                "mission_rate": float(mission_rate),
                "flags": flags,
            }
        )
    lowest_quiz = min(quality_rows, key=lambda row: row["quiz_accuracy_rate"], default=None)
    lowest_reflection = min(quality_rows, key=lambda row: row["reflection_rate"], default=None)
    lowest_mission = min(quality_rows, key=lambda row: row["mission_rate"], default=None)
    issue_count = sum(1 for row in quality_rows if row["flags"])
    defaults = {
        "sermon": challenge.sermon,
        "title": challenge.title,
        "week_start": challenge.week_start,
        "week_end": challenge.week_end,
        "participant_count": participant_count,
        "lowest_quiz_accuracy_label": f"Day {lowest_quiz['day_number']} | {lowest_quiz['title']}" if lowest_quiz else "",
        "lowest_quiz_accuracy_rate": Decimal(str(lowest_quiz["quiz_accuracy_rate"])) if lowest_quiz else Decimal("0"),
        "lowest_reflection_label": f"Day {lowest_reflection['day_number']} | {lowest_reflection['title']}" if lowest_reflection else "",
        "lowest_reflection_rate": Decimal(str(lowest_reflection["reflection_rate"])) if lowest_reflection else Decimal("0"),
        "lowest_mission_label": f"Day {lowest_mission['day_number']} | {lowest_mission['title']}" if lowest_mission else "",
        "lowest_mission_rate": Decimal(str(lowest_mission["mission_rate"])) if lowest_mission else Decimal("0"),
        "issue_count": issue_count,
        "quality_rows": quality_rows,
    }
    report, _ = ContentQualityReport.objects.update_or_create(challenge=challenge, defaults=defaults)
    return report


def sync_all_weekly_participation_reports():
    return [sync_weekly_participation_report(challenge) for challenge in _published_challenges()]


def sync_all_sermon_participation_reports():
    return [
        sync_sermon_participation_report(sermon)
        for sermon in Sermon.objects.filter(status=SermonStatus.PUBLISHED, is_published=True).prefetch_related("weekly_challenges")
    ]


def sync_all_daily_action_reports():
    return [sync_daily_action_report(challenge) for challenge in _published_challenges()]


def sync_all_user_participation_reports():
    return [sync_user_participation_report(user) for user in User.objects.filter(is_superuser=False).order_by("username")]


def sync_all_content_quality_reports():
    return [sync_content_quality_report(challenge) for challenge in _published_challenges()]
