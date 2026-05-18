import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

from django.contrib.auth import get_user_model
from django.test import Client


def dump_response(path: str, response):
    print("PATH", path)
    print("STATUS", response.status_code)
    if response.status_code >= 500:
        print(response.content.decode("utf-8", errors="replace")[:8000])
    else:
        html = response.content.decode("utf-8", errors="replace")
        print("HAS_SOURCE_SELECT", 'id="id_source_media_asset"' in html)
        if 'id="id_source_media_asset"' in html:
            start = html.find('id="id_source_media_asset"')
            print(html[start:start + 2000])
        else:
            print(html[:1500])
    print("=" * 100)


def main():
    user = get_user_model().objects.filter(is_superuser=True).first()
    if not user:
        print("No superuser found")
        return

    client = Client()
    client.force_login(user)

    paths = [
        "/admin/core/sourcemediaasset/",
        "/admin/core/sourcemediaasset/?_to_field=id&_popup=1",
        "/admin/core/sermon/add/",
        "/admin/core/sermon/add/?church=2",
    ]

    for path in paths:
        response = client.get(path)
        dump_response(path, response)


if __name__ == "__main__":
    main()
