"""
Tests for Node Management functionality.

These tests verify node administrator capabilities including:
- Author creation, modification, and deletion
- Signup requiring admin approval
- Soft deletion behavior for posts
- Visibility rules for deleted posts

Portions of this test structure were developed with assistance from
ChatGPT (OpenAI) to ensure correct Django testing practices.

"""
from django.test import TestCase

# Create your tests here.
from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from posts.models import Post

Author = get_user_model()


class NodeAdminTests(TestCase):

    def setUp(self):
        self.client = Client()

        # create admin
        self.admin = Author.objects.create_user(
            username="admin",
            password="adminpass",
            is_superuser=True,
            is_staff=True
        )

        # create normal user
        self.user = Author.objects.create_user(
            username="user1",
            password="testpass",
            is_approved=True
        )

    # -----------------------------------------
    # Signup requires admin approval
    # -----------------------------------------
    def test_signup_creates_unapproved_user(self):

        response = self.client.post(
            reverse("signup-author"),
            {"username": "newuser", "password": "pass123"}
        )

        user = Author.objects.get(username="newuser")

        self.assertFalse(user.is_approved)
        self.assertEqual(response.status_code, 200)

    # -----------------------------------------
    # Admin can create author
    # -----------------------------------------
    def test_admin_can_create_author(self):

        self.client.login(username="admin", password="adminpass")

        response = self.client.post(
            reverse("add-author"),
            {"username": "createduser", "password": "12345"}
        )

        self.assertTrue(
            Author.objects.filter(username="createduser").exists()
        )

    # -----------------------------------------
    # Admin can modify author
    # -----------------------------------------
    def test_admin_can_edit_author(self):

        self.client.login(username="admin", password="adminpass")

        response = self.client.post(
            reverse("edit-profile", args=[self.user.id]),
            {"displayName": "UpdatedUser"}
        )

        self.user.refresh_from_db()
        self.assertEqual(self.user.displayName, "UpdatedUser")

    # -----------------------------------------
    # Admin can delete author
    # -----------------------------------------
    def test_admin_can_delete_author(self):

        self.client.login(username="admin", password="adminpass")

        response = self.client.get(
            reverse("delete-author", args=[self.user.id])
        )

        self.assertFalse(
            Author.objects.filter(id=self.user.id).exists()
        )

    # -----------------------------------------
    # Deleted posts remain in database
    # -----------------------------------------
    def test_soft_delete_post(self):

        post = Post.objects.create(
            title="Test Post",
            content="Hello",
            author=self.user,
            deleted=True
        )

        self.assertTrue(Post.objects.filter(id=post.id).exists())
        self.assertTrue(post.deleted)

    # -----------------------------------------
    # Superuser can view deleted posts
    # -----------------------------------------
    def test_admin_can_view_deleted_post(self):

        post = Post.objects.create(
            title="Deleted Post",
            content="test",
            author=self.user,
            deleted=True
        )

        self.client.login(username="admin", password="adminpass")

        response = self.client.get(
            reverse("posts:detail", args=[post.id])
        )

        self.assertEqual(response.status_code, 200)

    # -----------------------------------------
    # Normal users cannot view deleted posts
    # -----------------------------------------
    def test_user_cannot_view_deleted_post(self):

        post = Post.objects.create(
            title="Deleted Post",
            content="test",
            author=self.user,
            deleted=True
        )

        self.client.login(username="user1", password="testpass")

        response = self.client.get(
            reverse("posts:detail", args=[post.id])
        )

        self.assertEqual(response.status_code, 404)