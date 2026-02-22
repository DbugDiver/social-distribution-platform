import uuid

from django.contrib.auth.models import AbstractUser
from django.db import models


class Author(AbstractUser):
    # Override the default ID with a UUID
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # Required by spec
    host = models.URLField(max_length=255, blank=True)
    displayName = models.CharField(max_length=255, blank=True)
    github = models.URLField(max_length=255, blank=True, null=True)
    profileImage = models.URLField(max_length=500, blank=True, null=True)

    def get_fqid(self):
        """Generates the exact URL-based ID required by the spec"""
        # Make sure self.host ends with a slash if needed
        return f"{self.host}authors/{self.id}"

    def __str__(self):
        return self.displayName or self.username
