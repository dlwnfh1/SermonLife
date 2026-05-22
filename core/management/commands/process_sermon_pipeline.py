from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from core.models import Sermon


class Command(BaseCommand):
    help = "Run sermon transcript creation and AI content generation in the background."

    def add_arguments(self, parser):
        parser.add_argument("sermon_id", type=int)

    def handle(self, *args, **options):
        try:
            sermon = Sermon.objects.get(pk=options["sermon_id"])
        except Sermon.DoesNotExist as exc:
            raise CommandError("Sermon not found.") from exc

        media_path = sermon.resolved_source_media_path
        if not media_path:
            raise CommandError("No source media file is connected to this sermon.")

        self.stdout.write(f"Starting sermon pipeline for '{sermon.title}'.")
        call_command("transcribe_sermon_audio", sermon.pk, media_path)
        self.stdout.write(self.style.SUCCESS(f"Finished sermon pipeline for '{sermon.title}'."))
