from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import Sermon
from core.services.pastor_review_notifications import (
    PastorReviewNotificationError,
    send_pastor_review_notification,
)


class Command(BaseCommand):
    help = "Send pastor review emails that have reached their scheduled time."

    def handle(self, *args, **options):
        now = timezone.now()
        pending_sermons = Sermon.objects.filter(
            pastor_review_requested=True,
            pastor_review_email_scheduled_at__isnull=False,
            pastor_review_email_scheduled_at__lte=now,
            pastor_review_email_sent_at__isnull=True,
        ).select_related("church")

        sent_count = 0
        failed_count = 0
        for sermon in pending_sermons:
            try:
                send_pastor_review_notification(sermon)
            except PastorReviewNotificationError as exc:
                failed_count += 1
                self.stderr.write(f"{sermon.pk}: {exc}")
                continue

            sermon.pastor_review_email_sent_at = timezone.now()
            sermon.save(update_fields=["pastor_review_email_sent_at", "updated_at"])
            sent_count += 1
            self.stdout.write(self.style.SUCCESS(f"Sent: {sermon.title}"))

        self.stdout.write(f"Sent {sent_count} scheduled pastor review email(s); {failed_count} failed.")