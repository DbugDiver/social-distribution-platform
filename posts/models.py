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

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="posts",
        null=True,
        blank=True,
    )

    title = models.CharField(max_length=200, blank=True)
    content_type = models.CharField(
        max_length=50,
        choices=ContentType.choices,
        default=ContentType.MARKDOWN,
    )
    content = models.TextField(blank=True)
    image = models.ImageField(upload_to="posts/", blank=True, null=True)
    visibility = models.CharField(
        max_length=10,
        choices=Visibility.choices,
        default=Visibility.PUBLIC,
    )
    deleted = models.BooleanField(default=False)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    remote_id = models.URLField(blank=True, null=True, unique=True, max_length=500)
    is_remote = models.BooleanField(default=False)
    node_url = models.URLField(blank=True, null=True)

    remote_author_url = models.URLField(blank=True, null=True)
    remote_author_name = models.CharField(max_length=200, blank=True, null=True)
    remote_author_host = models.URLField(blank=True, null=True)
    remote_image = models.URLField(blank=True, null=True, max_length=500)
    published = models.DateTimeField(blank=True, null=True)

    class Meta:
        indexes = [
            models.Index(fields=["author", "deleted", "-created"], name="post_author_del_created_idx"),
            models.Index(fields=["visibility", "deleted", "-created"], name="post_vis_del_created_idx"),
            models.Index(fields=["is_remote", "remote_id"], name="post_remote_lookup_idx"),
        ]

    @property
    def has_image(self) -> bool:
        return bool(self.image)

    @property
    def is_local(self):
        return not self.is_remote

    @property
    def effective_published(self):
        return self.published or self.created

    @property
    def display_author(self):
        if self.is_local and self.author:
            return {
                "id": str(self.author.id),
                "url": "",
                "username": getattr(self.author, "username", ""),
                "display_name": getattr(self.author, "get_full_name", lambda: self.author.username)() or self.author.username,
                "host": "",
            }
        return {
            "id": self.remote_author_url or "",
            "url": self.remote_author_url or "",
            "username": self.remote_author_name or "Remote Author",
            "display_name": self.remote_author_name or "Remote Author",
            "host": self.remote_author_host or self.node_url or "",
        }

    def __str__(self):
        return f"{self.title or '(no title)'} [{self.id}]"


class Comment(models.Model):
    class ContentType(models.TextChoices):
        PLAIN = "text/plain", "Plain text"
        MARKDOWN = "text/markdown", "Markdown"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="comments")

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="comments",
        null=True,
        blank=True,
    )

    comment = models.TextField()
    content_type = models.CharField(max_length=50, choices=ContentType.choices, default=ContentType.PLAIN)
    published = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    remote_id = models.URLField(blank=True, null=True, unique=True, max_length=500)
    is_remote = models.BooleanField(default=False)

    remote_author_url = models.URLField(blank=True, null=True)
    remote_author_name = models.CharField(max_length=200, blank=True, null=True)
    remote_author_host = models.URLField(blank=True, null=True)

    class Meta:
        ordering = ["-published"]
        indexes = [
            models.Index(fields=["post", "-published"], name="comment_post_pub_idx"),
            models.Index(fields=["is_remote", "remote_id"], name="comment_remote_lookup_idx"),
        ]

    @property
    def display_author(self):
        if not self.is_remote and self.author:
            return {
                "id": str(self.author.id),
                "username": getattr(self.author, "username", ""),
                "display_name": getattr(self.author, "get_full_name", lambda: self.author.username)() or self.author.username,
            }
        return {
            "id": self.remote_author_url or "",
            "username": self.remote_author_name or "Remote Author",
            "display_name": self.remote_author_name or "Remote Author",
        }

    def __str__(self):
        return f"Comment {self.id} on {self.post_id}"


class Like(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    remote_id = models.URLField(unique=True, null=True, blank=True, max_length=500)
    is_remote = models.BooleanField(default=False)

    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="likes",
        null=True,
        blank=True,
    )

    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="likes", null=True, blank=True)
    comment = models.ForeignKey(Comment, on_delete=models.CASCADE, related_name="likes", null=True, blank=True)
    created = models.DateTimeField(auto_now_add=True)

    remote_author_url = models.URLField(blank=True, null=True)
    remote_author_name = models.CharField(max_length=200, blank=True, null=True)
    remote_author_host = models.URLField(blank=True, null=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    (models.Q(post__isnull=False) & models.Q(comment__isnull=True))
                    | (models.Q(post__isnull=True) & models.Q(comment__isnull=False))
                ),
                name="like_exactly_one_target",
            ),
            models.UniqueConstraint(
                fields=["author", "post"],
                condition=models.Q(post__isnull=False, author__isnull=False),
                name="unique_author_post_like",
            ),
            models.UniqueConstraint(
                fields=["author", "comment"],
                condition=models.Q(comment__isnull=False, author__isnull=False),
                name="unique_author_comment_like",
            ),
        ]
        ordering = ["-created"]
        indexes = [
            models.Index(fields=["post", "-created"], name="like_post_created_idx"),
            models.Index(fields=["comment", "-created"], name="like_comment_created_idx"),
            models.Index(fields=["is_remote", "remote_id"], name="like_remote_lookup_idx"),
        ]

    def __str__(self):
        target = self.post_id or self.comment_id
        return f"Like {self.id} by {self.author_id or self.remote_author_name} on {target}"