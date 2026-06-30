from django.core.management.base import BaseCommand, CommandError
from django.urls import reverse

from core.services.reminders import (
    ReminderConfigurationError,
    get_reminder_candidates,
    send_web_push_reminder,
)


class Command(BaseCommand):
    help = "Send daily Word & Life reminders to users who have not started today's routine."

    def add_arguments(self, parser):
        parser.add_argument("--hour", type=int, help="Target reminder hour in 24h format. Defaults to current local hour.")
        parser.add_argument("--dry-run", action="store_true", help="Show recipients without sending notifications.")
        parser.add_argument("--base-url", default="https://sermonlife.pythonanywhere.com", help="Base URL used for reminder click links.")

    def handle(self, *args, **options):
        candidates = get_reminder_candidates(target_hour=options.get("hour"))
        if options["dry_run"]:
            self.stdout.write(f"Reminder candidates: {len(candidates)}")
            for candidate in candidates:
                self.stdout.write(
                    f"- {candidate.profile.user.get_username()} | {candidate.profile.user.first_name or '-'} | "
                    f"{candidate.challenge.title} | Day {candidate.daily.day_number}"
                )
            return

        sent_total = 0
        deleted_total = 0
        base_url = options["base_url"].rstrip("/")
        click_url = f"{base_url}{reverse('core:home')}?tab=today#today-set"
        for candidate in candidates:
            try:
                summary = send_web_push_reminder(candidate, click_url=click_url)
            except ReminderConfigurationError as exc:
                raise CommandError(str(exc)) from exc
            sent_total += summary["sent"]
            deleted_total += summary["deleted"]

        self.stdout.write(
            self.style.SUCCESS(
                f"Processed {len(candidates)} candidates. Sent {sent_total} notifications, removed {deleted_total} stale subscriptions."
            )
        )
