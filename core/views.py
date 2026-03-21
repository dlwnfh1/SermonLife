from django.shortcuts import render

from django.db.models import Sum
from django.utils import timezone

from .models import PointLedger, SermonStatus, SermonSummary, WeeklyChallenge


def home_view(request):
    challenge = (
        WeeklyChallenge.objects.filter(
            is_active=True,
            sermon__status=SermonStatus.PUBLISHED,
            sermon__is_published=True,
        )
        .select_related("sermon", "sermon__summary")
        .prefetch_related("sermon__quizzes", "sermon__missions", "daily_engagements")
        .first()
    )
    sermon = challenge.sermon if challenge else None
    summary = None
    if sermon:
        try:
            candidate = sermon.summary
        except SermonSummary.DoesNotExist:
            candidate = None
        if candidate and candidate.approved:
            summary = candidate

    weekly_points = 0
    if request.user.is_authenticated and challenge:
        weekly_points = (
            PointLedger.objects.filter(user=request.user, challenge=challenge)
            .aggregate(total=Sum("points"))
            .get("total")
            or 0
        )

    daily_engagements = []
    current_daily = None
    current_day_number = None
    if challenge:
        current_day_number = challenge.current_day_number(timezone.localdate())
        daily_engagements = list(
            challenge.daily_engagements.filter(approved=True).order_by("day_number")
        )
        current_daily = next(
            (item for item in daily_engagements if item.day_number == current_day_number),
            None,
        )

    context = {
        "challenge": challenge,
        "sermon": sermon,
        "weekly_points": weekly_points,
        "overview": getattr(summary, "overview", "") if summary else "",
        "outline_points": getattr(summary, "outline_points", []) if summary else [],
        "summary_lines": [
            line
            for line in [
                getattr(summary, "summary_line1", ""),
                getattr(summary, "summary_line2", ""),
                getattr(summary, "summary_line3", ""),
            ]
            if line
        ],
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
    }
    return render(request, "core/home.html", context)
