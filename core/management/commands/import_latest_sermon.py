from django.core.management.base import BaseCommand, CommandError

from core.services.ai_generation import AIContentGenerationError, generate_sermon_content
from core.services.sermon_importer import SermonImportError, import_latest_sermon


class Command(BaseCommand):
    help = "Import the latest sermon, prepare a weekly challenge, and auto-generate AI draft content."

    def handle(self, *args, **options):
        try:
            sermon = import_latest_sermon()
        except SermonImportError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"Imported sermon '{sermon.title}' for {sermon.sermon_date}."
            )
        )

        if sermon.import_error:
            raise CommandError(f"Imported sermon, but transcript fetch failed: {sermon.import_error}")

        try:
            generate_sermon_content(sermon)
        except AIContentGenerationError as exc:
            sermon.ai_error = str(exc)
            sermon.save(update_fields=["ai_error", "updated_at"])
            raise CommandError(f"Imported sermon, but AI generation failed: {exc}") from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"Generated AI draft content for '{sermon.title}'. Ready for approval."
            )
        )
