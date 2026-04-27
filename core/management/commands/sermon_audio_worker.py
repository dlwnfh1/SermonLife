import time

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import (
    Sermon,
    SermonAudioClipKind,
    SermonStatus,
    get_current_public_sermon_id,
)
from core.services.sermon_audio import SermonAudioGenerationError, generate_sermon_audio_package


class Command(BaseCommand):
    help = "Run a lightweight worker that generates listening audio for the current public sermon."

    def add_arguments(self, parser):
        parser.add_argument(
            "--interval",
            type=int,
            default=30,
            help="Seconds to wait between checks when running continuously.",
        )
        parser.add_argument(
            "--once",
            action="store_true",
            help="Check once and exit. Useful for testing.",
        )

    def handle(self, *args, **options):
        interval = max(options["interval"], 10)
        run_once = options["once"]

        self.stdout.write(self.style.SUCCESS("Sermon audio worker started."))

        while True:
            self._process_current_public_sermon()
            if run_once:
                break
            time.sleep(interval)

    def _process_current_public_sermon(self):
        released_ids = Sermon.release_due_publications()
        if released_ids:
            self.stdout.write(
                self.style.SUCCESS(
                    f"{timezone.now().isoformat()} Released scheduled sermons: {', '.join(str(pk) for pk in released_ids)}"
                )
            )

        sermon_id = get_current_public_sermon_id()
        if not sermon_id:
            self.stdout.write(f"{timezone.now().isoformat()} No current public sermon.")
            return

        sermon = Sermon.objects.filter(
            pk=sermon_id,
            is_published=True,
            status=SermonStatus.PUBLISHED,
        ).first()
        if not sermon:
            self.stdout.write(f"{timezone.now().isoformat()} Current public sermon was not found.")
            return

        has_weekly_audio = sermon.audio_clips.filter(
            kind=SermonAudioClipKind.WEEKLY_SUMMARY,
            day_number=0,
            file__gt="",
        ).exists()
        daily_audio_count = sermon.audio_clips.filter(
            kind=SermonAudioClipKind.DAILY_CONTENT,
            file__gt="",
        ).count()
        if has_weekly_audio and daily_audio_count >= 5:
            self.stdout.write(
                f"{timezone.now().isoformat()} Audio already exists for sermon #{sermon.pk}: {sermon.title}"
            )
            return

        self.stdout.write(
            f"{timezone.now().isoformat()} Generating audio for current sermon #{sermon.pk}: {sermon.title}"
        )
        try:
            generate_sermon_audio_package(sermon)
        except SermonAudioGenerationError as exc:
            sermon.audio_error = str(exc)
            sermon.save(update_fields=["audio_error", "updated_at"])
            self.stderr.write(self.style.ERROR(f"Audio generation failed: {exc}"))
            return

        self.stdout.write(self.style.SUCCESS(f"Audio generated for sermon #{sermon.pk}: {sermon.title}"))
