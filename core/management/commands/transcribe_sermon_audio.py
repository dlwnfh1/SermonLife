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
        try:
            sermon = Sermon.objects.get(pk=options["sermon_id"])
        except Sermon.DoesNotExist as exc:
            raise CommandError("Sermon not found.") from exc

        sermon.import_error = ""
        sermon.ai_error = ""
        sermon.save(update_fields=["import_error", "ai_error", "updated_at"])

        try:
            transcript = transcribe_audio_file(options["audio_path"])
        except TranscriptFetchError as exc:
            sermon.import_error = str(exc)
            sermon.save(update_fields=["import_error", "updated_at"])
            raise CommandError(str(exc)) from exc

        sermon.transcript = transcript
        sermon.import_error = ""
        sermon.last_imported_at = timezone.now()
        sermon.save(update_fields=["transcript", "import_error", "last_imported_at", "updated_at"])

        try:
            generate_sermon_content(sermon)
        except AIContentGenerationError as exc:
            sermon.ai_error = str(exc)
            sermon.save(update_fields=["ai_error", "updated_at"])
            raise CommandError(f"Transcript saved, but AI generation failed: {exc}") from exc

        sermon.ai_error = ""
        sermon.pastor_review_requested = False
        sermon.pastor_review_requested_at = None
        sermon.save(
            update_fields=[
                "ai_error",
                "pastor_review_requested",
                "pastor_review_requested_at",
                "updated_at",
            ]
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Transcript saved and AI content generated for sermon '{sermon.title}'."
            )
        )
