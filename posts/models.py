import uuid
from django.conf import settings
from django.db import models

"""
Change citation (local project work):
- Added Comment and Like models for CMPUT 404 Part 1 comments/likes stories.
- Added Like constraints for one-target-only and duplicate-like prevention.
- Updated CheckConstraint to Django 6 style using `condition=`.
"""

"""
This is the Post model which represents a post created by a user.
It has fields for the author, title, content type, content, image attachment, visibility, deleted status, created time and updated time.
The content type can be either plain text or markdown.
The visibility can be public, friends or unlisted.
The deleted field is a boolean that indicates whether the post is deleted or not.
"""
class Post(models.Model):
    class ContentType(models.TextChoices):
        PLAIN = "text/plain", "Plain text"
        MARKDOWN = "text/markdown", "Markdown"

    class Visibility(models.TextChoices):
        PUBLIC = "PUBLIC", "Public"
        FRIENDS = "FRIENDS", "Friends"
        UNLISTED = "UNLISTED", "Unlisted"

    #The id field is a UUID field that serves as the primary key for the post. It is automatically generated and not editable.
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="posts")
    title = models.CharField(max_length=200, blank=True) # Optional title for the post, can be blank
    content_type = models.CharField(max_length=50, choices=ContentType.choices, default=ContentType.MARKDOWN) # Markdown by default
    content = models.TextField(blank=True)

    # Optional image attachment for ANY post (plain or markdown)
    image = models.ImageField(upload_to="posts/", blank=True, null=True)
    
    visibility = models.CharField(max_length=10, choices=Visibility.choices, default=Visibility.PUBLIC)
    deleted = models.BooleanField(default=False)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)
    remote_id = models.CharField(max_length=255, blank=True, null=True, unique=True)
    is_remote = models.BooleanField(default=False)
    node_url = models.URLField(blank=True, null=True)

    class Meta:
        # User Story 1: add practical indexes for common stream/detail queries.
        indexes = [
            models.Index(fields=["author", "deleted", "-created"], name="post_author_del_created_idx"),
            models.Index(fields=["visibility", "deleted", "-created"], name="post_vis_del_created_idx"),
        ]

    """
    The has_image property is a convenient way to check if a post has an
    image attached without having to directly access the image field. 
    It returns True if there is an image, and False otherwise.
    """
    @property
    def has_image(self) -> bool:
        return bool(self.image)

    def __str__(self):
        return f"{self.title or '(no title)'} [{self.id}]"


class Comment(models.Model):
    # Changed section: local comment model used by both web UI and API endpoints.
    class ContentType(models.TextChoices):
        PLAIN = "text/plain", "Plain text"
        MARKDOWN = "text/markdown", "Markdown"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="comments")
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="comments",
    )
    comment = models.TextField()
    content_type = models.CharField(
        max_length=50,
        choices=ContentType.choices,
        default=ContentType.PLAIN,
    )
    published = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)
    remote_id = models.CharField(max_length=255, blank=True, null=True, unique=True)
    is_remote = models.BooleanField(default=False)

    class Meta:
        ordering = ["-published"]
        # User Story 1: speed up comment-list lookups on a post.
        indexes = [
            models.Index(fields=["post", "-published"], name="comment_post_pub_idx"),
        ]

    def __str__(self):
        return f"Comment {self.id} on {self.post_id}"


class Like(models.Model):
    # Changed section: local like model (supports liking either a post or a comment).
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    remote_id = models.URLField(unique=True, null=True, blank=True)
    is_remote = models.BooleanField(default=False)
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="likes",
    )
    post = models.ForeignKey(
        Post,
        on_delete=models.CASCADE,
        related_name="likes",
        null=True,
        blank=True,
    )
    comment = models.ForeignKey(
        Comment,
        on_delete=models.CASCADE,
        related_name="likes",
        null=True,
        blank=True,
    )
    created = models.DateTimeField(auto_now_add=True)

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
                condition=models.Q(post__isnull=False),
                name="unique_author_post_like",
            ),
            models.UniqueConstraint(
                fields=["author", "comment"],
                condition=models.Q(comment__isnull=False),
                name="unique_author_comment_like",
            ),
        ]
        ordering = ["-created"]
        # User Story 1: improve listing likes by object/time.
        indexes = [
            models.Index(fields=["post", "-created"], name="like_post_created_idx"),
            models.Index(fields=["comment", "-created"], name="like_comment_created_idx"),
        ]

    def __str__(self):
        target = self.post_id or self.comment_id
        return f"Like {self.id} by {self.author_id} on {target}"