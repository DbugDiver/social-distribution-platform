from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from authors.models import Author, Follower
from .models import Comment, Like, Post

"""
Change citation (local project work):
- Added API-level tests for comments/likes user stories and stream behavior.
- Includes pagination checks and deleted-post interaction denial checks.
"""


class CommentsLikesApiTests(TestCase):
	def setUp(self):
		self.author = Author.objects.create_user(
			username="author1",
			password="pass12345",
			host="http://testserver/",
			displayName="Author One",
		)
		self.reader = Author.objects.create_user(
			username="reader1",
			password="pass12345",
			host="http://testserver/",
			displayName="Reader One",
		)
		self.other = Author.objects.create_user(
			username="other1",
			password="pass12345",
			host="http://testserver/",
			displayName="Other One",
		)
		self.client.force_login(self.reader)

		self.public_post = Post.objects.create(
			author=self.author,
			title="Public",
			content="hello",
			visibility=Post.Visibility.PUBLIC,
		)
		self.unlisted_post = Post.objects.create(
			author=self.author,
			title="Unlisted",
			content="hidden-link",
			visibility=Post.Visibility.UNLISTED,
		)
		self.friends_post = Post.objects.create(
			author=self.author,
			title="Friends",
			content="friends-only",
			visibility=Post.Visibility.FRIENDS,
		)
		self.deleted_post = Post.objects.create(
			author=self.author,
			title="Deleted",
			content="gone",
			visibility=Post.Visibility.PUBLIC,
			deleted=True,
		)

	def _post_comments_url(self, post):
		return reverse(
			"posts:api-post-comments",
			kwargs={"author_id": post.author_id, "post_id": post.id},
		)

	def _post_likes_url(self, post):
		return reverse(
			"posts:api-post-likes",
			kwargs={"author_id": post.author_id, "post_id": post.id},
		)

	def _post_detail_url(self, post):
		return reverse(
			"posts:api-post-detail",
			kwargs={"author_id": post.author_id, "post_id": post.id},
		)

	def _comment_likes_url(self, post, comment):
		return reverse(
			"posts:api-comment-likes",
			kwargs={
				"author_id": post.author_id,
				"post_id": post.id,
				"comment_id": comment.id,
			},
		)

	def test_author_can_comment_on_accessible_entry(self):
		response = self.client.post(
			self._post_comments_url(self.public_post),
			data={"comment": "witty reply", "contentType": "text/plain"},
		)
		self.assertEqual(response.status_code, 201)
		data = response.json()
		self.assertEqual(data["type"], "comment")
		self.assertEqual(data["comment"], "witty reply")
		self.assertTrue(data["id"].startswith("http://testserver/"))

	def test_author_can_like_accessible_entry(self):
		response = self.client.post(self._post_likes_url(self.public_post))
		self.assertEqual(response.status_code, 201)
		body = response.json()
		self.assertEqual(body["type"], "like")
		self.assertEqual(body["object"], "http://testserver" + self._post_detail_url(self.public_post))

	def test_author_can_like_accessible_comment(self):
		comment = Comment.objects.create(
			post=self.public_post,
			author=self.author,
			comment="nice",
		)
		response = self.client.post(self._comment_likes_url(self.public_post, comment))
		self.assertEqual(response.status_code, 201)
		self.assertEqual(response.json()["type"], "like")

	def test_public_entry_returns_likes_summary(self):
		Like.objects.create(author=self.reader, post=self.public_post)
		response = self.client.get(self._post_detail_url(self.public_post))
		self.assertEqual(response.status_code, 200)
		payload = response.json()
		self.assertEqual(payload["type"], "entry")
		self.assertEqual(payload["likes"]["count"], 1)
		self.assertEqual(payload["comments"]["type"], "comments")

	def test_reader_can_use_public_or_unlisted_link(self):
		public_res = self.client.get(self._post_detail_url(self.public_post))
		unlisted_res = self.client.get(self._post_detail_url(self.unlisted_post))
		self.assertEqual(public_res.status_code, 200)
		self.assertEqual(unlisted_res.status_code, 200)
		self.assertTrue(public_res.json()["id"].startswith("http://testserver/"))
		self.assertTrue(unlisted_res.json()["id"].startswith("http://testserver/"))

	def test_stream_sorted_most_recent_first(self):
		old_post = Post.objects.create(
			author=self.author,
			title="Old Public",
			content="older",
			visibility=Post.Visibility.PUBLIC,
		)
		Post.objects.filter(id=old_post.id).update(created=timezone.now() - timedelta(days=1))

		response = self.client.get(reverse("posts:api-stream"))
		self.assertEqual(response.status_code, 200)
		entries = response.json()["src"]
		self.assertGreaterEqual(len(entries), 2)
		titles = [entry["title"] for entry in entries]
		self.assertEqual(titles[-1], "Old Public")

	def test_stream_visibility_and_deleted_filter(self):
		Follower.objects.create(follower=self.reader, following=self.author, status="accepted")
		response = self.client.get(reverse("posts:api-stream"))
		self.assertEqual(response.status_code, 200)
		titles = {item["title"] for item in response.json()["src"]}
		self.assertIn("Public", titles)
		self.assertIn("Unlisted", titles)
		self.assertIn("Friends", titles)
		self.assertNotIn("Deleted", titles)

	def test_deleted_entry_cannot_be_liked_or_commented(self):
		like_res = self.client.post(self._post_likes_url(self.deleted_post))
		comment_res = self.client.post(
			self._post_comments_url(self.deleted_post),
			data={"comment": "cannot", "contentType": "text/plain"},
		)
		self.assertEqual(like_res.status_code, 403)
		self.assertEqual(comment_res.status_code, 403)

	def test_comments_and_likes_pagination(self):
		for idx in range(7):
			Comment.objects.create(
				post=self.public_post,
				author=self.author,
				comment=f"comment-{idx}",
			)
		comments_res = self.client.get(self._post_comments_url(self.public_post), {"page": 2, "size": 3})
		self.assertEqual(comments_res.status_code, 200)
		comments_data = comments_res.json()
		self.assertEqual(comments_data["type"], "comments")
		self.assertEqual(comments_data["size"], 3)
		self.assertEqual(len(comments_data["src"]), 3)

		c = Comment.objects.create(post=self.public_post, author=self.author, comment="base")
		Like.objects.create(author=self.reader, comment=c)
		Like.objects.create(author=self.author, post=self.public_post)
		likes_res = self.client.get(self._post_likes_url(self.public_post), {"page": 1, "size": 1})
		self.assertEqual(likes_res.status_code, 200)
		likes_data = likes_res.json()
		self.assertEqual(likes_data["type"], "likes")
		self.assertEqual(likes_data["size"], 1)

	def test_get_things_liked_by_author(self):
		comment = Comment.objects.create(
			post=self.public_post,
			author=self.author,
			comment="x",
		)
		Like.objects.create(author=self.reader, post=self.public_post)
		Like.objects.create(author=self.reader, comment=comment)

		liked_url = reverse("posts:api-author-liked", kwargs={"author_id": self.reader.id})
		response = self.client.get(liked_url)
		self.assertEqual(response.status_code, 200)
		payload = response.json()
		self.assertEqual(payload["type"], "likes")
		self.assertEqual(len(payload["src"]), 2)
