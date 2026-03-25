from django.urls import path
from django.contrib.auth.views import PasswordChangeDoneView, PasswordChangeView

from .views import (
    complete_mission_view,
    home_view,
    login_view,
    logout_view,
    signup_view,
    submit_daily_quiz_view,
    submit_reflection_view,
)

app_name = "core"

urlpatterns = [
    path("", home_view, name="home"),
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
]
