from rest_framework.test import APITestCase, APIClient
from rest_framework import status
from django.urls import reverse

from authors.models import Author, Follower


class FollowAPITestCase(APITestCase):

    def setUp(self):
        self.client=APIClient()
        self.author1=Author.objects.create_user(username="author1",password="testpass",displayName="Author One")
        self.author2=Author.objects.create_user(username="author2",password="testpass",displayName="Author Two")
        self.author3=Author.objects.create_user(username="author3",password="testpass",displayName="Author Three")
    # USER STORY 1
    def test_follow_author(self):
        self.client.force_authenticate(user=self.author1)
        url=f"/authors/api/authors/{self.author2.id}/follow/"
        response=self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(Follower.objects.filter(follower=self.author1,following=self.author2).exists())
    # USER STORY 3
    def test_accept_follow_request(self):
        follow=Follower.objects.create(follower=self.author1,following=self.author2,status="pending")
        self.client.force_authenticate(user=self.author2)
        url=f"/authors/api/authors/{self.author1.id}/accept/"
        response=self.client.post(url)
        follow.refresh_from_db()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(follow.status, "accepted")
    # USER STORY 3
    def test_reject_follow_request(self):
        follow=Follower.objects.create(follower=self.author1,following=self.author2,status="pending")
        self.client.force_authenticate(user=self.author2)
        url=f"/authors/api/authors/{self.author1.id}/reject/"
        response=self.client.post(url)
        follow.refresh_from_db()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(follow.status, "rejected")
    # USER STORY 4
    def test_view_follow_requests(self):
        Follower.objects.create(follower=self.author1,following=self.author2,status="pending")
        self.client.force_authenticate(user=self.author2)
        url=f"/authors/api/authors/{self.author2.id}/following/"
        response=self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
    # USER STORY 5
    def test_unfollow_author(self):
        Follower.objects.create(follower=self.author1,following=self.author2,status="accepted")
        self.client.force_authenticate(user=self.author1)
        url=f"/authors/api/authors/{self.author2.id}/unfollow/"
        response=self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Follower.objects.filter(follower=self.author1,following=self.author2).exists())
    # USER STORY 6
    def test_become_friends(self):
        Follower.objects.create(follower=self.author1,following=self.author2,status="accepted")
        Follower.objects.create(follower=self.author2,following=self.author1,status="accepted")
        self.client.force_authenticate(user=self.author1)
        url=f"/authors/api/authors/{self.author1.id}/friends/"
        response=self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(len(response.data) > 0)
    # USER STORY 7
    def test_unfriend(self):
        Follower.objects.create(follower=self.author1,following=self.author2,status="accepted")
        Follower.objects.create(follower=self.author2,following=self.author1,status="accepted")
        self.client.force_authenticate(user=self.author1)
        url=f"/authors/api/authors/{self.author2.id}/unfollow/"
        response=self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        still_friend=Follower.objects.filter(follower=self.author1,following=self.author2).exists()
        self.assertFalse(still_friend)