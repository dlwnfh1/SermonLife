from django.core.management.base import BaseCommand, CommandError

from core.models import Sermon, SermonAudioClipKind, SermonStatus
from core.services.sermon_audio import SermonAudioGenerationError, generate_sermon_audio_package


class Command(BaseCommand):
    help = "Generate SermonLife listening audio files for one sermon or all published sermons missing audio."

    def add_arguments(self, parser):
        parser.add_argument(
            "--sermon-id",
            type=int,
            help="Generate audio for a specific sermon id.",
        )
        parser.add_argument(
            "--all-missing",
            action="store_true",
            help="Generate audio for published sermons that do not have a weekly summary audio file.",
        )

    def handle(self, *args, **options):
        sermon_id = options.get("sermon_id")
        all_missing = options.get("all_missing")

        if sermon_id and all_missing:
            raise CommandError("Use either --sermon-id or --all-missing, not both.")

        if sermon_id:
            sermons = [Sermon.objects.get(pk=sermon_id)]
        elif all_missing:
            published_sermons = (
                Sermon.objects.filter(is_published=True, status=SermonStatus.PUBLISHED)
                .order_by("-published_at", "-sermon_date", "-id")
            )
            sermons = [
                sermon
                for sermon in published_sermons
                if not sermon.audio_clips.filter(
                    kind=SermonAudioClipKind.WEEKLY_SUMMARY,
                    day_number=0,
                    file__gt="",
                ).exists()
            ]
        else:
            raise CommandError("Provide --sermon-id ID or --all-missing.")

        success_count = 0
        for sermon in sermons:
            self.stdout.write(f"Generating listening audio for sermon #{sermon.pk}: {sermon.title}")
            try:
                generate_sermon_audio_package(sermon)
            except SermonAudioGenerationError as exc:
                sermon.audio_error = str(exc)
                sermon.save(update_fields=["audio_error", "updated_at"])
                self.stderr.write(self.style.ERROR(f"Failed: {exc}"))
                continue
            success_count += 1
            self.stdout.write(self.style.SUCCESS("Generated."))

        self.stdout.write(self.style.SUCCESS(f"Done. {success_count} sermon(s) generated."))
