from django.shortcuts import render

from django.db.models import Sum

from .models import PointLedger, SermonStatus, SermonSummary, WeeklyChallenge


def home_view(request):
    challenge = (
        WeeklyChallenge.objects.filter(
            is_active=True,
            sermon__status=SermonStatus.PUBLISHED,
            sermon__is_published=True,
        )
        .select_related("sermon", "sermon__summary")
        .prefetch_related("sermon__quizzes", "sermon__missions")
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

    context = {
        "challenge": challenge,
        "sermon": sermon,
        "weekly_points": weekly_points,
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
        "quizzes": sermon.quizzes.filter(approved=True)[:3] if sermon else [],
        "missions": sermon.missions.filter(approved=True)[:2] if sermon else [],
    }
    return render(request, "core/home.html", context)
