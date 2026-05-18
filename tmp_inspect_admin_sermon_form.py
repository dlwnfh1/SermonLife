import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

from django.contrib.auth import get_user_model
from django.test import Client


def main():
    user = get_user_model().objects.filter(is_superuser=True).first()
    client = Client()
    client.force_login(user)
    response = client.get("/admin/core/sermon/add/")
    print("status", response.status_code)
    html = response.content.decode("utf-8", errors="replace")
    start = html.find('id="id_source_media_asset"')
    if start == -1:
        print("source_media_asset select not found")
        return
    snippet = html[start:start + 4000]
    print(snippet)


if __name__ == "__main__":
    main()
