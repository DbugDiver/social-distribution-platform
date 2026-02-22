import uuid
from django.conf import settings
from django.db import models

class Post(models.Model):
    class ContentType(models.TextChoices):
        PLAIN = "text/plain", "Plain text"
        MARKDOWN = "text/markdown", "Markdown"

    class Visibility(models.TextChoices):
        PUBLIC = "PUBLIC", "Public"
        FRIENDS = "FRIENDS", "Friends"
        UNLISTED = "UNLISTED", "Unlisted"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="posts")

    title = models.CharField(max_length=200, blank=True)
    content_type = models.CharField(max_length=50, choices=ContentType.choices, default=ContentType.PLAIN)
    content = models.TextField(blank=True)

    # Optional image attachment for ANY post (plain or markdown)
    image = models.ImageField(upload_to="posts/", blank=True, null=True)

    visibility = models.CharField(max_length=10, choices=Visibility.choices, default=Visibility.PUBLIC)
    deleted = models.BooleanField(default=False)

    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    @property
    def has_image(self) -> bool:
        return bool(self.image)

    def __str__(self):
        return f"{self.title or '(no title)'} [{self.id}]"