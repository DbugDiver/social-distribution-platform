#!/usr/bin/env python
import os
import sys
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'socialdistribution.settings')
django.setup()

from django.conf import settings
settings.DEBUG = True
settings.ALLOWED_HOSTS.append('testserver')

from django.test import Client
from authors.models import Author, Follower
from posts.models import Post, Like
import uuid
from urllib.parse import urljoin

# Create test users
user1, _ = Author.objects.get_or_create(
    username="testuser1",
    defaults={"email": "test1@test.com"}
)
if not user1.password:
    user1.set_password("testpass123")
    user1.save()

user2, _ = Author.objects.get_or_create(
    username="testuser2",
    defaults={"email": "test2@test.com"}
)
if not user2.password:
    user2.set_password("testpass123")
    user2.save()

# Create test post
post_uuid = uuid.uuid4()
post, _ = Post.objects.get_or_create(
    id=post_uuid,
    defaults={
        "title": "Test Post",
        "content": "Test content",
        "author": user1,
        "visibility": "PUBLIC"
    }
)
print(f"Test users: {user1.username}, {user2.username}")
print(f"Test post: {post.id}")

# Test client
client = Client(enforce_csrf_checks=False)
logged_in = client.login(username="testuser1", password="testpass123")
print(f"Logged in as {user1.username}: {logged_in}\n")

# Test LIKE
print("=" * 50)
print("Testing LIKE endpoint:")
response = client.post(f"/{post.id}/like/", data={"next": "/"}, follow=True)
print(f"Status: {response.status_code}")
print(f"Like created: {Like.objects.filter(author=user1, post=post).exists()}")

# Test COMMENT
print("\n" + "=" * 50)
print("Testing COMMENT endpoint:")
response = client.post(f"/{post.id}/comment/", data={"comment": "Test comment", "next": "/"}, follow=True)
print(f"Status: {response.status_code}")
print(f"Comments on post: {post.comments.count()}")

# Test FOLLOW
print("\n" + "=" * 50)
print("Testing FOLLOW endpoint:")
response = client.post("/authors/follow/", data={"uuid": str(user2.id), "is_remote": "False", "next": "/"}, follow=True)
print(f"Status: {response.status_code}")
print(f"Follow created: {Follower.objects.filter(follower=user1, following=user2).exists()}")
