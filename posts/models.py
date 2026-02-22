import uuid
from django.conf import settings
from django.db import models

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