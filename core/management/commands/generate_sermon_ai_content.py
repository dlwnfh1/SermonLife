from django.core.management.base import BaseCommand, CommandError

from core.models import Sermon
from core.services.ai_generation import AIContentGenerationError, generate_sermon_content


class Command(BaseCommand):
    help = "Generate AI summary, quizzes, and missions for a sermon."

    def add_arguments(self, parser):
        parser.add_argument("sermon_id", type=int)

    def handle(self, *args, **options):
        try:
            sermon = Sermon.objects.get(pk=options["sermon_id"])
        except Sermon.DoesNotExist as exc:
            raise CommandError("Sermon not found.") from exc

        try:
            generate_sermon_content(sermon)
        except AIContentGenerationError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"Generated AI content for sermon '{sermon.title}'."
            )
        )
