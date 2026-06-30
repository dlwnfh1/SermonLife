import json
import os
from dataclasses import dataclass

from django.conf import settings
from django.utils import timezone

from core.models import (
    Church,
    DailyEngagement,
    DailyMissionCompletion,
    DailyQuizAttempt,
    DailyReflectionResponse,
    UserProfile,
    WebPushSubscription,
    WeeklyChallenge,
)

try:
    from pywebpush import WebPushException, webpush
except ImportError:  # pragma: no cover
    WebPushException = Exception
    webpush = None


REMINDER_TITLE = "Word & Life"
REMINDER_BODY = "오늘 말씀 묵상을 잠깐 시작해 보세요"


class ReminderConfigurationError(Exception):
    pass


@dataclass
class ReminderCandidate:
    profile: UserProfile
    challenge: WeeklyChallenge
    daily: DailyEngagement


def get_vapid_public_key():
    return getattr(settings, "WEB_PUSH_VAPID_PUBLIC_KEY", "") or os.environ.get("WEB_PUSH_VAPID_PUBLIC_KEY", "")


def _get_vapid_private_key():
    return getattr(settings, "WEB_PUSH_VAPID_PRIVATE_KEY", "") or os.environ.get("WEB_PUSH_VAPID_PRIVATE_KEY", "")


def _get_vapid_subject():
    return getattr(settings, "WEB_PUSH_VAPID_SUBJECT", "") or os.environ.get("WEB_PUSH_VAPID_SUBJECT", "")


def web_push_is_configured():
    return bool(get_vapid_public_key() and _get_vapid_private_key() and _get_vapid_subject() and webpush)


def _get_church_for_profile(profile: UserProfile):
    return profile.church or Church.get_default()


def _get_reminder_daily_for_church(church, today=None):
    today = today or timezone.localdate()
    challenge = WeeklyChallenge.get_current_public_challenge(church=church)
    if not challenge:
        return None, None

    day_one = challenge.release_date_for_day(1)
    day_five = challenge.release_date_for_day(5)
    if today < day_one or today > day_five:
        return challenge, None

    day_number = challenge.current_day_number(today)
    daily = challenge.daily_engagements.filter(approved=True, day_number=day_number).first()
    return challenge, daily


def user_has_any_daily_activity(user, daily):
    if not daily:
        return False
    return (
        DailyQuizAttempt.objects.filter(user=user, daily_engagement=daily).exists()
        or DailyReflectionResponse.objects.filter(user=user, daily_engagement=daily).exists()
        or DailyMissionCompletion.objects.filter(user=user, daily_engagement=daily, completed=True).exists()
    )


def get_reminder_candidates(target_hour=None, today=None):
    today = today or timezone.localdate()
    target_hour = timezone.localtime().hour if target_hour is None else int(target_hour)
    profiles = (
        UserProfile.objects.select_related("user", "church")
        .filter(reminder_enabled=True, reminder_hour=target_hour)
        .exclude(reminder_last_sent_on=today)
    )

    candidates = []
    for profile in profiles:
        challenge, daily = _get_reminder_daily_for_church(_get_church_for_profile(profile), today=today)
        if not challenge or not daily:
            continue
        if user_has_any_daily_activity(profile.user, daily):
            continue
        candidates.append(ReminderCandidate(profile=profile, challenge=challenge, daily=daily))
    return candidates


def save_web_push_subscription(*, user, payload, user_agent=""):
    subscription = payload.get("subscription") or payload
    endpoint = (subscription.get("endpoint") or "").strip()
    keys = subscription.get("keys") or {}
    auth_key = (keys.get("auth") or "").strip()
    p256dh_key = (keys.get("p256dh") or "").strip()
    expiration_time = subscription.get("expirationTime")

    if not endpoint or not auth_key or not p256dh_key:
        raise ValueError("Invalid push subscription payload.")

    profile = UserProfile.objects.select_related("church").filter(user=user).first()
    church = profile.church if profile and profile.church_id else Church.get_default()
    subscription_obj, _ = WebPushSubscription.objects.update_or_create(
        endpoint=endpoint,
        defaults={
            "user": user,
            "church": church,
            "auth_key": auth_key,
            "p256dh_key": p256dh_key,
            "expiration_time": expiration_time,
            "user_agent": user_agent[:1000],
        },
    )
    return subscription_obj


def delete_web_push_subscription(*, user, endpoint):
    if not endpoint:
        return 0
    deleted_count, _ = WebPushSubscription.objects.filter(user=user, endpoint=endpoint).delete()
    return deleted_count


def send_web_push_reminder(candidate: ReminderCandidate, click_url: str):
    if not web_push_is_configured():
        raise ReminderConfigurationError("Web push is not configured.")

    subscriptions = list(WebPushSubscription.objects.filter(user=candidate.profile.user))
    if not subscriptions:
        return {"sent": 0, "deleted": 0}

    vapid_claims = {"sub": _get_vapid_subject()}
    payload = json.dumps(
        {
            "title": REMINDER_TITLE,
            "body": REMINDER_BODY,
            "url": click_url,
        }
    )
    sent = 0
    deleted = 0

    for subscription in subscriptions:
        try:
            webpush(
                subscription_info={
                    "endpoint": subscription.endpoint,
                    "keys": {"auth": subscription.auth_key, "p256dh": subscription.p256dh_key},
                },
                data=payload,
                vapid_private_key=_get_vapid_private_key(),
                vapid_claims=vapid_claims,
                ttl=3600,
            )
            sent += 1
        except WebPushException as exc:  # pragma: no cover
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            if status_code in {404, 410}:
                subscription.delete()
                deleted += 1
                continue
            raise

    if sent:
        UserProfile.objects.filter(pk=candidate.profile.pk).update(reminder_last_sent_on=timezone.localdate())

    return {"sent": sent, "deleted": deleted}
