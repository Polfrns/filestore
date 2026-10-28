import secrets

from django.contrib.auth.models import User
from django.db import models


class APIKey(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="api_keys")
    key = models.CharField(max_length=64, unique=True)
    label = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @classmethod
    def generate(cls, user: User, label: str = "") -> "APIKey":
        return cls.objects.create(user=user, key=secrets.token_urlsafe(32), label=label)

    def __str__(self) -> str:
        preview = self.key[:8]
        return f"{self.user.username} — {self.label or preview + '...'}"