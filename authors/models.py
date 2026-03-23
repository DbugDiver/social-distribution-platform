import uuid

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models


class Author(AbstractUser):
    # Override the default ID with a UUID
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    is_approved = models.BooleanField(default=False)
    # Required by spec
    host = models.URLField(max_length=255, blank=True)
    displayName = models.CharField(max_length=255, blank=True)
    github = models.URLField(max_length=255, blank=True, null=True)
    bio = models.TextField(blank=True, default="")
    profileImage = models.ImageField(upload_to="profile_images/", blank=True, null=True)
    remote_id = models.CharField(max_length=255, blank=True, null=True, unique=True)
    is_remote = models.BooleanField(default=False)

    def get_fqid(self):
        """Generates the exact URL-based ID required by the spec"""
        # Make sure self.host ends with a slash if needed
        return f"{self.host}authors/{self.id}"

    def __str__(self):
        return self.displayName or self.username


class Follower(models.Model):
    choices = [
        ("pending", "pending"),
        ("accepted", "accepted"),
        ("rejected", "rejected"),
    ]
    follower = models.ForeignKey(
        Author, on_delete=models.CASCADE, related_name="following_relationships"
    )
    following = models.ForeignKey(
        Author, on_delete=models.CASCADE, related_name="follower_relationships"
    )
    status = models.CharField(max_length=20, choices=choices, default="pending")
    time_created = models.DateTimeField(auto_now_add=True)

    # i got the Meta from GPT and it does not allow the same follower to follow the same following more than once, it will raise an error if we try to create a duplicate entry in the database
    class Meta:
        unique_together = ("follower", "following")
        # User Story 1: indexes for accepted-follow and mutual-follow checks.
        indexes = [
            models.Index(fields=["follower", "status"], name="follower_status_idx"),
            models.Index(fields=["following", "status"], name="following_status_idx"),
        ]

    def __str__(self):
        return f"{self.follower} → {self.following} ({self.status})"


User = settings.AUTH_USER_MODEL


class Notification(models.Model):
    TYPE_CHOICES = (("follow", "Follow"), ("like", "Like"), ("comment", "Comment"))
    recipient = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="notifications"
    )
    sender = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="sent_notifications"
    )
    notification_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    message = models.CharField(max_length=255)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.sender} -> {self.recipient} ({self.notification_type})"
