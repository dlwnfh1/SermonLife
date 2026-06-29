from django.core.management.base import BaseCommand

from core.services.transcript_service import cleanup_stale_transcript_temp_files


class Command(BaseCommand):
    help = "Clean up stale transcript-related audio temp files under the system temp directory."

    def add_arguments(self, parser):
        parser.add_argument(
            "--hours",
            type=int,
            default=24,
            help="Delete only temp artifacts older than this many hours. Default: 24.",
        )

    def handle(self, *args, **options):
        summary = cleanup_stale_transcript_temp_files(older_than_hours=options["hours"])
        reclaimed_mb = summary["reclaimed_bytes"] / (1024 * 1024)

        self.stdout.write(
            self.style.SUCCESS(
                "Cleaned transcript temp artifacts in "
                f"{summary['temp_root']}: "
                f"{summary['deleted_dirs']} directories, "
                f"{summary['deleted_files']} files, "
                f"{reclaimed_mb:.1f} MB reclaimed."
            )
        )
