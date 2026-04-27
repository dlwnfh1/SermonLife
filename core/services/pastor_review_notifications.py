from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse

from core.models import PastorNotificationRecipient


class PastorReviewNotificationError(Exception):
    pass


def _get_active_recipient_emails():
    return list(
        PastorNotificationRecipient.objects.filter(is_active=True)
        .order_by("name", "email")
        .values_list("email", flat=True)
    )


def send_pastor_review_notification(sermon):
    recipient_emails = _get_active_recipient_emails()
    if not recipient_emails:
        raise PastorReviewNotificationError("활성화된 목회자 공지 수신자 이메일이 없습니다.")

    site_url = getattr(settings, "SERMONLIFE_SITE_URL", "").rstrip("/")
    pastor_path = reverse("core:pastor_sermon_edit", args=[sermon.pk])
    pastor_url = f"{site_url}{pastor_path}" if site_url else pastor_path

    subject = f"[SERMON LIFE] 목회자 검토 요청: {sermon.title}"
    message = "\n".join(
        [
            "설교 AI 정리 및 1차 검토가 완료되었습니다.",
            "",
            f"설교 제목: {sermon.title}",
            f"설교일: {sermon.sermon_date}",
            f"설교자: {sermon.preacher or '-'}",
            "",
            "아래 화면에서 내용을 검토한 뒤 공개를 진행해 주세요.",
            pastor_url,
        ]
    )

    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "") or getattr(settings, "EMAIL_HOST_USER", "")
    if not from_email:
        raise PastorReviewNotificationError("발신 이메일 설정이 없습니다. DEFAULT_FROM_EMAIL 또는 EMAIL_HOST_USER를 설정해 주세요.")

    try:
        sent_count = send_mail(
            subject=subject,
            message=message,
            from_email=from_email,
            recipient_list=recipient_emails,
            fail_silently=False,
        )
    except Exception as exc:
        raise PastorReviewNotificationError(f"이메일 발송 실패: {exc}") from exc

    if sent_count <= 0:
        raise PastorReviewNotificationError("이메일이 발송되지 않았습니다.")

    return recipient_emails
