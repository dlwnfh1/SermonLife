from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.models import Sermon
from core.services.ai_generation import AIContentGenerationError, generate_sermon_content
from core.services.transcript_service import TranscriptFetchError, transcribe_audio_file


class Command(BaseCommand):
    help = "Transcribe a local sermon media file, save the transcript to a sermon, and generate AI content."

    def add_arguments(self, parser):
        parser.add_argument("sermon_id", type=int)
        parser.add_argument("audio_path")

    def handle(self, *args, **options):
        now = timezone.now()
        try:
            sermon = Sermon.objects.get(pk=options["sermon_id"])
        except Sermon.DoesNotExist as exc:
            raise CommandError("Sermon not found.") from exc

        Sermon.objects.filter(pk=sermon.pk).update(
            import_error="",
            ai_error="",
            updated_at=now,
        )

        try:
            transcript = transcribe_audio_file(options["audio_path"])
        except TranscriptFetchError as exc:
            Sermon.objects.filter(pk=sermon.pk).update(
                import_error=str(exc),
                updated_at=timezone.now(),
            )
            raise CommandError(str(exc)) from exc

        updated = Sermon.objects.filter(pk=sermon.pk).update(
            transcript=transcript,
            import_error="",
            last_imported_at=timezone.now(),
            updated_at=timezone.now(),
        )
        if not updated:
            raise CommandError("Sermon no longer exists while saving transcript.")

        sermon.refresh_from_db()

        try:
            generate_sermon_content(sermon)
        except AIContentGenerationError as exc:
            Sermon.objects.filter(pk=sermon.pk).update(
                ai_error=str(exc),
                updated_at=timezone.now(),
            )
            raise CommandError(f"Transcript saved, but AI generation failed: {exc}") from exc

        Sermon.objects.filter(pk=sermon.pk).update(
            ai_error="",
            pastor_review_requested=False,
            pastor_review_requested_at=None,
            updated_at=timezone.now(),
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Transcript saved and AI content generated for sermon '{sermon.title}'."
            )
        )
