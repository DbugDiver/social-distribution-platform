from django.test import Client, TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from authors.models import Author, Follower


class AuthorIdentityTests(TestCase):
    def setUp(self):
        """
        Setup runs before EVERY test. We use this to create a clean,
        fake database and a test client (browser simulator).
        """
        self.client = Client()

        # Create our main test user
        self.author = Author.objects.create_user(
            username="greg_johnson",
            password="supersecretpassword123",
            displayName="Greg",
            github="https://github.com/gjohnson",
            bio="Just a test bio",
        )

    def test_author_creation(self):
        """Test that an author is correctly created in the database"""
        author_in_db = Author.objects.get(username="greg_johnson")
        self.assertEqual(author_in_db.displayName, "Greg")
        self.assertEqual(author_in_db.github, "https://github.com/gjohnson")
        self.assertTrue(author_in_db.id)  # Ensures UUID/ID was generated

    def test_author_login(self):
        """Test that an author can log in securely"""
        # Attempt to login with the credentials we set up
        login_success = self.client.login(
            username="greg_johnson", password="supersecretpassword123"
        )
        self.assertTrue(login_success)

    def test_profile_view_authenticated(self):
        """Test that a logged-in user can view their profile"""
        self.client.login(username="greg_johnson", password="supersecretpassword123")

        # NOTE: Change '/profile/' to whatever your profile URL actually is!
        response = self.client.get(
            reverse("author-profile", kwargs={"pk": self.author.id})
        )

        # 200 means OK (the page loaded successfully)
        self.assertEqual(response.status_code, 200)
        # Check if the author's name is actually rendered in the HTML
        self.assertContains(response, "Greg")
        self.assertContains(response, "Just a test bio")

    def test_edit_profile_loads(self):
        """Test that the Edit Profile page loads for logged-in users"""
        self.client.login(username="greg_johnson", password="supersecretpassword123")

        response = self.client.get(reverse("edit-profile"))
        self.assertEqual(response.status_code, 200)

    def test_edit_profile_saves_changes(self):
        """Test that submitting the Edit Profile form updates the database"""
        self.client.login(username="greg_johnson", password="supersecretpassword123")

        # Simulate typing new data into the form and hitting Submit (POST)
        response = self.client.post(
            reverse("edit-profile"),
            {
                "displayName": "Gregory J.",
                "github": "https://github.com/torvalds",
                "bio": "I updated my bio!",
                "profileImage": "https://placecats.com/300/300",
            },
        )

        # Refresh our author object from the test database to see the new changes
        self.author.refresh_from_db()

        # Verify the database actually updated
        self.assertEqual(self.author.displayName, "Gregory J.")
        self.assertEqual(self.author.github, "https://github.com/torvalds")
        self.assertEqual(self.author.bio, "I updated my bio!")

    def test_unauthenticated_access_blocked(self):
        """Test that strangers cannot edit profiles without logging in"""
        # Notice we are NOT logging in here
        response = self.client.get(reverse("edit-profile"))

        # 302 means Redirect (Django should redirect them to the login page)
        self.assertEqual(response.status_code, 302)


class FollowAPITestCase(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.author1 = Author.objects.create_user(
            username="author1", password="testpass", displayName="Author One"
        )
        self.author2 = Author.objects.create_user(
            username="author2", password="testpass", displayName="Author Two"
        )
        self.author3 = Author.objects.create_user(
            username="author3", password="testpass", displayName="Author Three"
        )

    # USER STORY 1
    def test_follow_author(self):
        self.client.force_authenticate(user=self.author1)
        url = f"/authors/api/authors/{self.author2.id}/follow/"
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            Follower.objects.filter(
                follower=self.author1, following=self.author2
            ).exists()
        )

    # USER STORY 3
    def test_accept_follow_request(self):
        follow = Follower.objects.create(
            follower=self.author1, following=self.author2, status="pending"
        )
        self.client.force_authenticate(user=self.author2)
        url = f"/authors/api/authors/{self.author1.id}/accept/"
        response = self.client.post(url)
        follow.refresh_from_db()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(follow.status, "accepted")

    # USER STORY 3
    def test_reject_follow_request(self):
        follow = Follower.objects.create(
            follower=self.author1, following=self.author2, status="pending"
        )
        self.client.force_authenticate(user=self.author2)
        url = f"/authors/api/authors/{self.author1.id}/reject/"
        response = self.client.post(url)
        follow.refresh_from_db()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(follow.status, "rejected")

    # USER STORY 4
    def test_view_follow_requests(self):
        Follower.objects.create(
            follower=self.author1, following=self.author2, status="pending"
        )
        self.client.force_authenticate(user=self.author2)
        url = f"/authors/api/authors/{self.author2.id}/following/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    # USER STORY 5
    def test_unfollow_author(self):
        Follower.objects.create(
            follower=self.author1, following=self.author2, status="accepted"
        )
        self.client.force_authenticate(user=self.author1)
        url = f"/authors/api/authors/{self.author2.id}/unfollow/"
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(
            Follower.objects.filter(
                follower=self.author1, following=self.author2
            ).exists()
        )

    # USER STORY 6
    def test_become_friends(self):
        Follower.objects.create(
            follower=self.author1, following=self.author2, status="accepted"
        )
        Follower.objects.create(
            follower=self.author2, following=self.author1, status="accepted"
        )
        self.client.force_authenticate(user=self.author1)
        url = f"/authors/api/authors/{self.author1.id}/friends/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(len(response.data) > 0)

    # USER STORY 7
    def test_unfriend(self):
        Follower.objects.create(
            follower=self.author1, following=self.author2, status="accepted"
        )
        Follower.objects.create(
            follower=self.author2, following=self.author1, status="accepted"
        )
        self.client.force_authenticate(user=self.author1)
        url = f"/authors/api/authors/{self.author2.id}/unfollow/"
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        still_friend = Follower.objects.filter(
            follower=self.author1, following=self.author2
        ).exists()
        self.assertFalse(still_friend)
