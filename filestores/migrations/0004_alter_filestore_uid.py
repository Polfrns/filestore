import secrets
import string

import filestores.models
from django.db import migrations, models


UID_ALPHABET = string.ascii_lowercase + string.digits
UID_LENGTH = 8


def replace_legacy_uids(apps, schema_editor):
    FileStore = apps.get_model("filestores", "FileStore")
    KBIndex = apps.get_model("kb", "KBIndex")
    used_uids = set(FileStore.objects.values_list("uid", flat=True))

    for filestore in FileStore.objects.all():
        uid = "".join(secrets.choice(UID_ALPHABET) for _ in range(UID_LENGTH))
        while uid in used_uids:
            uid = "".join(secrets.choice(UID_ALPHABET) for _ in range(UID_LENGTH))
        used_uids.add(uid)
        filestore.uid = uid
        if not filestore.display_name:
            filestore.display_name = uid
        filestore.save(update_fields=["uid", "display_name"])

    KBIndex.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("filestores", "0003_filestore_ephemeral"),
        ("kb", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(replace_legacy_uids, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="filestore",
            name="uid",
            field=models.CharField(
                default=filestores.models.generate_filestore_uid,
                editable=False,
                max_length=8,
                unique=True,
            ),
        ),
        migrations.AlterField(
            model_name="filestore",
            name="display_name",
            field=models.CharField(max_length=150),
        ),
    ]