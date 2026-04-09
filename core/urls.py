from django.urls import path
from django.contrib.auth.views import PasswordChangeDoneView, PasswordChangeView

from .views import (
    complete_mission_view,
    home_view,
    login_view,
    logout_view,
    my_history_view,
    pastor_dashboard_view,
    pastor_members_view,
    pastor_reports_view,
    pastor_sermon_edit_view,
    read_sermon_view,
    signup_view,
    submit_highlight_vote_view,
    submit_daily_quiz_view,
    submit_reflection_view,
    watch_sermon_view,
)

app_name = "core"

urlpatterns = [
    path("", home_view, name="home"),
    path("history/", my_history_view, name="my_history"),
    path("pastor/", pastor_dashboard_view, name="pastor_dashboard"),
    path("pastor/reports/", pastor_reports_view, name="pastor_reports"),
    path("pastor/members/", pastor_members_view, name="pastor_members"),
    path("pastor/sermons/<int:pk>/", pastor_sermon_edit_view, name="pastor_sermon_edit"),
    path("watch/", watch_sermon_view, name="watch_sermon"),
    path("read/", read_sermon_view, name="read_sermon"),
    path("login/", login_view, name="login"),
    path("signup/", signup_view, name="signup"),
    path("logout/", logout_view, name="logout"),
    path(
        "password-change/",
        PasswordChangeView.as_view(
            template_name="core/password_change.html",
            success_url="/password-change/done/",
        ),
        name="password_change",
    ),
    path(
        "password-change/done/",
        PasswordChangeDoneView.as_view(template_name="core/password_change_done.html"),
        name="password_change_done",
    ),
    path("daily/<int:pk>/quiz/", submit_daily_quiz_view, name="submit_daily_quiz"),
    path("daily/<int:pk>/reflection/", submit_reflection_view, name="submit_reflection"),
    path("daily/<int:pk>/mission/", complete_mission_view, name="complete_mission"),
    path("highlight/vote/", submit_highlight_vote_view, name="submit_highlight_vote"),
]
