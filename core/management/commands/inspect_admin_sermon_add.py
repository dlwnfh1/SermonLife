from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.test import Client

from core.models import Church, SourceMediaAsset
from core.admin import sync_source_media_assets


class Command(BaseCommand):
    help = "Inspect rendered source media options on the sermon add admin page."

    def handle(self, *args, **options):
        user = get_user_model().objects.filter(is_superuser=True).first()
        if not user:
            self.stdout.write("No superuser found.")
            return

        self.stdout.write("Source media assets:")
        assets = list(SourceMediaAsset.objects.select_related("church").order_by("id"))
        self.stdout.write(f"count={len(assets)}")
        for asset in assets:
            church_slug = asset.church.slug if asset.church_id else "None"
            self.stdout.write(f"  - id={asset.id} church={church_slug} file={asset.file.name}")

        self.stdout.write("")
        self.stdout.write("Running sync_source_media_assets()...")
        sync_source_media_assets()
        assets = list(SourceMediaAsset.objects.select_related("church").order_by("id"))
        self.stdout.write(f"count_after_sync={len(assets)}")
        for asset in assets:
            church_slug = asset.church.slug if asset.church_id else "None"
            self.stdout.write(f"  - id={asset.id} church={church_slug} file={asset.file.name}")

        client = Client()
        client.force_login(user)

        for church in Church.objects.order_by("id"):
            path = f"/admin/core/sermon/add/?church={church.pk}"
            response = client.get(path)
            html = response.content.decode("utf-8", errors="replace")
            marker = 'id="id_source_media_asset"'
            start = html.find(marker)
            self.stdout.write("")
            self.stdout.write(f"PATH {path} status={response.status_code}")
            if start == -1:
                self.stdout.write("  source_media_asset select not found")
                continue
            snippet = html[start:start + 1800]
            self.stdout.write(snippet)
