import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

from django.contrib.auth import get_user_model
from django.test import Client
from core.models import Church


def main():
    user = get_user_model().objects.filter(is_superuser=True).first()
    if not user:
        print("no superuser")
        return

    client = Client()
    client.force_login(user)

    bcpc = Church.objects.filter(slug="bcpc").first()
    paths = [
        "/admin/core/sourcemediaasset/",
        "/admin/core/sermon/add/",
    ]
    if bcpc:
        paths.append(f"/admin/core/sermon/add/?church={bcpc.pk}")

    for path in paths:
        print("PATH", path)
        response = client.get(path)
        print("status", response.status_code)
        if response.status_code >= 500:
            print(response.content.decode("utf-8", errors="replace")[:4000])
        else:
            html = response.content.decode("utf-8", errors="replace")
            if path == "/admin/core/sourcemediaasset/":
                row_count = html.count('<tr><td class="action-checkbox">')
                print("source_media_rows", row_count)
            if 'id="id_source_media_asset"' in html:
                start = html.find('id="id_source_media_asset"')
                print(html[start:start + 2500])
            else:
                print(html[:1200])
        print("=" * 80)


if __name__ == "__main__":
    main()
