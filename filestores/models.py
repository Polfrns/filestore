import secrets
import string

from django.contrib.auth.models import User
from django.db import models


FILESTORE_UID_ALPHABET = string.ascii_lowercase + string.digits
FILESTORE_UID_LENGTH = 8


def generate_filestore_uid() -> str:
    return "".join(secrets.choice(FILESTORE_UID_ALPHABET) for _ in range(FILESTORE_UID_LENGTH))


class FileStoreKind(models.TextChoices):
    LOCAL = "local", "Local"
    GRAPH = "graph", "Graph"


class FileStore(models.Model):
    uid = models.CharField(max_length=FILESTORE_UID_LENGTH, unique=True, editable=False, default=generate_filestore_uid)
    display_name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    kind = models.CharField(max_length=20, choices=FileStoreKind.choices, default=FileStoreKind.LOCAL)
    ref = models.TextField()
    read_only = models.BooleanField(default=False)
    shared = models.BooleanField(default=True)
    ephemeral = models.BooleanField(default=False)
    owner = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="owned_filestores",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.uid} ({self.kind})"