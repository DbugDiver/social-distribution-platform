from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from authors.models import Author, Follower
from .models import Comment, Like, Post

from django.core.files.uploadedfile import SimpleUploadedFile

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

"""
    Rosy: Can you help me write a rest api test for this copied and pasted @login_required def create(request), and models.py
    ChatGPT: Some of the PostsViewTests(idea taken for initial testing appraoch) and all of setUpTestData function is based on suggestion provided
    Citation: ChatGPT, OpenAI, 2026-02-28, https://chatgpt.com/share/69a2a966-5abc-800a-90e6-51cfc3476b25
"""
class PostsViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.author = Author.objects.create_user(
            username="author",
            password="pass12345",
            displayName="Author"
        )

        cls.other = Author.objects.create_user(
            username="other",
            password="pass12345",
            displayName="Other"
        )

        cls.post_plain = Post.objects.create(
            author=cls.author,
            content="plain text",
            content_type=Post.ContentType.PLAIN,
            visibility=Post.Visibility.PUBLIC,
            deleted=False,
        )

        cls.post_md = Post.objects.create(
            author=cls.author,
            content="**bold**",
            content_type=Post.ContentType.MARKDOWN,
            visibility=Post.Visibility.PUBLIC,
            deleted=False,
        )

    def login_as(self, user):
        self.client.force_login(user)

    def _print_header(self, name: str):
        print("\n" + "=" * 60)
        print(f"RUNNING: {name}")
        print("=" * 60)

    # -------------------------------------------
    # Stream tests
    # -------------------------------------------
    
    # User Story 60: As a node admin, I don't want separate frontend and backend web servers,
    # so both UI pages and API endpoints should be served from the same Django server.
    def test_frontend_and_api_served_by_same_server(self):
        self._print_header("test_frontend_and_api_served_by_same_server")

        self.login_as(self.author)

        # Frontend request (HTML page)
        ui_response = self.client.get(reverse("posts:stream"))
        self.assertEqual(ui_response.status_code, 200)
        self.assertIn("text/html", ui_response["Content-Type"])
        print("Test Passed: Frontend HTML page served")

        # Backend request (API JSON)
        api_response = self.client.get(reverse("posts:api-stream"))
        self.assertEqual(api_response.status_code, 200)
        self.assertIn("application/json", api_response["Content-Type"])
        print("Test Passed: API JSON served")

        # Ensure both come from same test server
        self.assertTrue(ui_response.wsgi_request.get_host().startswith("testserver"))
        self.assertTrue(api_response.wsgi_request.get_host().startswith("testserver"))
        print("Test Passed: Both frontend and backend served from same server")

    def test_login_required_to_see_stream(self):
        self._print_header("test_login_required_to_see_stream")
        res = self.client.get(reverse("posts:stream"))
        self.assertEqual(res.status_code, 302)
        print("Test Passed: Anonymous user is redirected from stream")
        self.assertIn("/accounts/login", res["Location"])
        print("Test Passed: Redirect goes to login page")

    def test_stream_shows_for_logged_in_user(self):
        self._print_header("test_stream_shows_for_logged_in_user")
        self.login_as(self.author)
        res = self.client.get(reverse("posts:stream"))
        self.assertEqual(res.status_code, 200)
        print("Test Passed: Logged-in user can access stream")
        self.assertContains(res, "Public Stream")
        print("Test Passed: Stream page contains 'Public Stream'")

    # User Story 59: As a node admin, I don't want arrays stored in database fields,
    # so relationships should be stored as separate relational rows
    def test_no_array_fields_in_post_model(self):
        print("\nChecking Post model fields for array-like storage...")

        from django.db.models import JSONField
        for field in Post._meta.get_fields():
            print(f"Field checked: {field.name}")
            # Ensure no JSON/array style fields exist
            self.assertFalse(
                isinstance(field, JSONField),
                f"Array-like JSONField found: {field.name}"
            )
        print("Test Passed: No array-like fields exist in Post model")
        
    # -------------------------------------------
    # Create tests
    # -------------------------------------------
    
    # User Story 37: As an author, I want to be able to use my web-browser to manage/author my entries, so I don't have to use a clunky API.
    # User Story 1: As an author, I want to make entries, so I can share my thoughts and pictures with other local authors.
    # User Story 14: As an author, entries I make can be in simple plain text, 
    # because I don't always want all the formatting features of CommonMark.
    def test_create_plain_post_verify_success(self):
        self._print_header("test_create_plain_post_verify_success")
        self.login_as(self.author)
        payload = {
            "content": "plain posting test",
            "content_type": Post.ContentType.PLAIN,
            "visibility": Post.Visibility.PUBLIC,
        }
        res = self.client.post(reverse("posts:create"), payload)
        self.assertEqual(res.status_code, 302)
        print("Test Passed: Plain post can be successfully craeted and redirected")
        post = Post.objects.filter(author=self.author, content="plain posting test").first()
        self.assertTrue(post is not None)
        print("Test Passed: Plain post saved in database")
        self.assertEqual(post.content_type, Post.ContentType.PLAIN)
        print("Test Passed: Content type is listed as PLAIN")

    # User Story 12: As an author, entries I make can be in CommonMark, so I can give my entries some basic formatting.
    # User Story 21: As an author, entries I create that are in CommonMark can link to images, so that I can illustrate my entries.
    def test_create_markdown_post_verify_success(self):
        self._print_header("test_create_markdown_post_verify_success")
        self.login_as(self.author)
        md_text = "**bold** and *italics* and `code`"
        payload = {
            "content": md_text,
            "content_type": Post.ContentType.MARKDOWN,
            "visibility": Post.Visibility.PUBLIC,
        }
        res = self.client.post(reverse("posts:create"), payload)
        self.assertEqual(res.status_code, 302)
        print("Test Passed: Markdown post can be successfully created and riderected")
        post = Post.objects.filter(author=self.author, content=md_text).first()
        self.assertTrue(post is not None)
        print("Test Passed: Markdown post saved in database")
        self.assertEqual(post.content_type, Post.ContentType.MARKDOWN)
        print("Test Passed: Content type is listed MARKDOWN")

    #User Story 17: As an author, entries I create can be images, so that I can share pictures and drawings.
    """
    Rosy: Can you help me write a rest api test for this copied and pasted @login_required def create(request), and models.py
    ChatGPT: Below function is directly based on the response provided
    Citation: ChatGPT, OpenAI, 2026-02-28, https://chatgpt.com/share/69a2a966-5abc-800a-90e6-51cfc3476b25
    """
    def test_markdown_post_allows_image_upload(self):
        self._print_header("test_markdown_post_allows_image_upload")
        self.login_as(self.author)
        fake_png = SimpleUploadedFile(
            "test.png",
            b"\x89PNG\r\n\x1a\n" + b"0" * 64,
            content_type="image/png",
        )
        payload = {
            "content": "![alt](test.png)",
            "content_type": Post.ContentType.MARKDOWN,
            "visibility": Post.Visibility.PUBLIC,
            "image": fake_png,
        }
        res = self.client.post(reverse("posts:create"), payload)
        self.assertIn(res.status_code, (200, 302))
        print("Test Passed: Markdown post can have image linked to it")
        
    
    """
    Rosy: Can you help me write a test for markdown post can render an image link. Base it on this
    Copied and pasted the test_markdown_post_allows_image_upload function
    ChatGPT: Below function is directly based on the response provided
    Citation: ChatGPT, OpenAI, 2026-02-28, https://chatgpt.com/share/69a2b2c0-2e74-800a-ab30-43b7aba966bb
    """
    #User Story 21: As an author, entries I create that are in CommonMark can link to images, so that I can illustrate my entries.
    def test_markdown_post_renders_image_link(self):
        self._print_header("test_markdown_post_renders_image_link")
        self.login_as(self.author)

        payload = {
            "title": "Markdown Image Link Test",
            "content": "Here is an image: ![Cat](https://example.com/cat.png)",
            "content_type": Post.ContentType.MARKDOWN,
            "visibility": Post.Visibility.PUBLIC,
        }
        res = self.client.post(reverse("posts:create"), payload, follow=True)
        self.assertIn(res.status_code, (200, 302))
        post = Post.objects.get(title="Markdown Image Link Test")
        self.assertEqual(post.content_type, Post.ContentType.MARKDOWN)
        detail_res = self.client.get(reverse("posts:detail", args=[post.id]))
        self.assertEqual(detail_res.status_code, 200)
        self.assertContains(detail_res, '<img')
        self.assertContains(detail_res, 'src="https://example.com/cat.png"')
        self.assertContains(detail_res, 'alt="Cat"')
        print("Test Passed: Markdown image link renders correctly")
   
    # -------------------------------------------
    # Edit tests
    # -------------------------------------------
    
    #User Story 37: As an author, I want to be able to use my web-browser to manage/author my entries, so I don't have to use a clunky API.
    #User Story 3: As an author, I want to edit my entries locally, so that I'm not stuck with a typo on a popular entry.
    def test_author_can_edit_own_post(self):
        self._print_header("test_author_can_edit_own_post")
        self.login_as(self.author)
        payload = {
            "content": "edited",
            "content_type": Post.ContentType.PLAIN,
            "visibility": Post.Visibility.PUBLIC,
        }
        res = self.client.post(reverse("posts:edit", args=[self.post_plain.id]), payload)
        self.assertEqual(res.status_code, 302)
        print("Test Passed: Author can edit own post")
        self.post_plain.refresh_from_db()
        self.assertEqual(self.post_plain.content, "edited")
        print("Test Passed: Edited post content updated in database")

    #User Stopry 40: As an author, other authors cannot modify my entries, so that I don't get impersonated
    def test_other_author_cannot_edit_post(self):
        self._print_header("test_other_author_cannot_edit_post")
        self.login_as(self.other)
        payload = {
            "content": "hacked",
            "content_type": Post.ContentType.PLAIN,
            "visibility": Post.Visibility.PUBLIC,
        }
        res = self.client.post(reverse("posts:edit", args=[self.post_plain.id]), payload)
        self.assertEqual(res.status_code, 404)
        print("Test Passed: Non-owner cannot edit post")
        self.post_plain.refresh_from_db()
        self.assertEqual(self.post_plain.content, "plain text")
        print("Test Passed: Post content remains unchanged")

    # -------------------------------------------
    # Delete tests
    # -------------------------------------------
    
    #User Story 25: As an author, I want to delete my own entries locally, so I can remove entries that are out of date or made by mistake.
    def test_author_can_delete_own_post(self):
        self._print_header("test_author_can_delete_own_post")
        self.login_as(self.author)
        res = self.client.post(reverse("posts:delete", args=[self.post_plain.id]))
        self.assertEqual(res.status_code, 302)
        print("Test Passed: Author can delete own post")
        self.post_plain.refresh_from_db()
        self.assertTrue(self.post_plain.deleted)
        print("Test Passed: Deleted post is marked as deleted")

    #User Stopry 40: As an author, other authors cannot modify my entries, so that I don't get impersonated
    def test_other_author_cannot_delete_post(self):
        self._print_header("test_other_author_cannot_delete_post")
        self.login_as(self.other)
        res = self.client.post(reverse("posts:delete", args=[self.post_plain.id]))
        self.assertEqual(res.status_code, 404)
        print("Test Passed: Non-owner cannot delete post")
        self.post_plain.refresh_from_db()
        self.assertTrue(not self.post_plain.deleted)
        print("Test Passed: The post that non-author tried deleting remains undeleted")
        
    # User Story 61: As a node admin, I want deleted entries to stay in the database
    # and only be removed from the UI and API, so I can see what was deleted.
    def test_deleted_post_remains_in_database_but_hidden(self):
        self._print_header("test_deleted_post_remains_in_database_but_hidden")

        self.login_as(self.author)

        # Delete the post
        res = self.client.post(reverse("posts:delete", args=[self.post_plain.id]))
        self.assertEqual(res.status_code, 302)

        # Ensure the row still exists in DB
        exists_in_db = Post.objects.filter(id=self.post_plain.id).exists()
        self.assertTrue(exists_in_db)
        print("Test Passed: Deleted post still exists in the database")

        # Ensure it is marked deleted
        post = Post.objects.get(id=self.post_plain.id)
        self.assertTrue(post.deleted)
        print("Test Passed: Post is marked as deleted")

        # Ensure it does not appear in the stream UI
        res = self.client.get(reverse("posts:stream"))
        self.assertNotContains(res, "plain text")
        print("Test Passed: Deleted post does not appear in the stream UI")

'''
These tests below refers to user stories : Visibiltiy
Citation:
    This test class was developed with guidance from ChatGPT (OpenAI).
    Final implementation and testing were completed by the developer.

'''
class PostVisibilityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.author = Author.objects.create_user(
            username="author",
            password="pass123"
        )

        cls.friend = Author.objects.create_user(
            username="friend",
            password="pass123"
        )

        cls.follower = Author.objects.create_user(
            username="follower",
            password="pass123"
        )

        cls.stranger = Author.objects.create_user(
            username="stranger",
            password="pass123"
        )

        # Create relationships
        # Friend = mutual follow
        Follower.objects.create(follower=cls.friend, following=cls.author, status="accepted")
        Follower.objects.create(follower=cls.author, following=cls.friend, status="accepted")

        # Follower = only one-way
        Follower.objects.create(follower=cls.follower, following=cls.author, status="accepted")

        # Create posts
        cls.public_post = Post.objects.create(
            author=cls.author,
            content="public post",
            visibility=Post.Visibility.PUBLIC,
        )

        cls.unlisted_post = Post.objects.create(
            author=cls.author,
            content="unlisted post",
            visibility=Post.Visibility.UNLISTED,
        )

        cls.friends_post = Post.objects.create(
            author=cls.author,
            content="friends post",
            visibility=Post.Visibility.FRIENDS,
        )

    #Public Post Test  visible to everyone	
    def test_public_post_visible_to_everyone(self):
        for user in [self.friend, self.follower, self.stranger]:
            self.client.force_login(user)
            res = self.client.get(reverse("posts:detail", args=[self.public_post.id]))
            self.assertEqual(res.status_code, 200)
    
    #Unlisted Posts visible to followers + link
    def test_unlisted_visible_to_follower(self):
        self.client.force_login(self.follower)
        res = self.client.get(reverse("posts:detail", args=[self.unlisted_post.id]))
        self.assertEqual(res.status_code, 200)
    
    #Unlisted Post Not visible in Public stream
    def test_unlisted_not_in_public_stream(self):
        self.client.force_login(self.stranger)
        res = self.client.get(reverse("posts:stream"))
        self.assertNotContains(res, "unlisted post")
    
    #Friends-only visible only to friends
    def test_friends_post_visible_to_friend(self):
        self.client.force_login(self.friend)
        res = self.client.get(reverse("posts:detail", args=[self.friends_post.id]))
        self.assertEqual(res.status_code, 200)
    
    #Friends-only NOT visible to follower
    def test_friends_post_not_visible_to_follower(self):
        self.client.force_login(self.follower)
        res = self.client.get(reverse("posts:detail", args=[self.friends_post.id]))
        self.assertEqual(res.status_code, 403)  #Access denied

    #Author always sees own posts
    def test_author_always_sees_own_posts(self):
        self.client.force_login(self.author)
        for post in [self.public_post, self.unlisted_post, self.friends_post]:
            res = self.client.get(reverse("posts:detail", args=[post.id]))
            self.assertEqual(res.status_code, 200)


class ProjectPart2StoryTests(TestCase):
    """
    Change citation:
    - Tests added by Copilot to validate only the requested Project 2 user stories.
    - Includes SQL-level index checks, REST route checks, and visibility/likes behavior checks.
    """

    def setUp(self):
        self.author = Author.objects.create_user(
            username="story_author",
            password="pass12345",
            host="http://testserver/",
            displayName="Story Author",
        )
        self.friend = Author.objects.create_user(
            username="story_friend",
            password="pass12345",
            host="http://testserver/",
            displayName="Story Friend",
        )
        self.comment_author = Author.objects.create_user(
            username="story_comment_author",
            password="pass12345",
            host="http://testserver/",
            displayName="Story Comment Author",
        )
        self.receiver = Author.objects.create_user(
            username="story_receiver",
            password="pass12345",
            host="http://testserver/",
            displayName="Story Receiver",
        )
        self.liker = Author.objects.create_user(
            username="story_liker",
            password="pass12345",
            host="http://testserver/",
            displayName="Story Liker",
        )

    # User Story 1: relational DB should be well-indexed for common query paths.
    def test_story1_sqlite_has_expected_indexes_for_core_tables(self):
        from django.db import connection

        expected_indexes_by_table = {
            "posts_post": {
                "post_author_del_created_idx",
                "post_vis_del_created_idx",
            },
            "posts_comment": {
                "comment_post_pub_idx",
            },
            "posts_like": {
                "like_post_created_idx",
                "like_comment_created_idx",
            },
            "authors_follower": {
                "follower_status_idx",
                "following_status_idx",
            },
        }

        with connection.cursor() as cursor:
            for table_name, expected_indexes in expected_indexes_by_table.items():
                cursor.execute(f"PRAGMA index_list('{table_name}')")
                rows = cursor.fetchall()
                index_names = {row[1] for row in rows}

                for idx_name in expected_indexes:
                    self.assertIn(
                        idx_name,
                        index_names,
                        msg=f"Missing expected index {idx_name} on {table_name}",
                    )

                    # Extra SQL-level check to confirm each index is physically defined with columns.
                    cursor.execute(f"PRAGMA index_info('{idx_name}')")
                    index_cols = cursor.fetchall()
                    self.assertGreater(
                        len(index_cols),
                        0,
                        msg=f"Index {idx_name} exists but has no indexed columns",
                    )

    # User Story 2: RESTful interface for core author operations.
    def test_story2_rest_can_fetch_single_author(self):
        self.client.force_login(self.receiver)
        response = self.client.get(f"/authors/api/authors/{self.author.id}/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["id"], f"http://testserver/authors/{self.author.id}")
        self.assertEqual(payload["displayName"], "Story Author")

    # User Story 3: friends-only post comments visible to friends and comment author.
    def test_story3_friends_post_comment_visibility_scoped(self):
        post = Post.objects.create(
            author=self.author,
            title="friends only",
            content="private",
            visibility=Post.Visibility.FRIENDS,
        )
        # Make exactly one mutual friend.
        Follower.objects.create(follower=self.friend, following=self.author, status="accepted")
        Follower.objects.create(follower=self.author, following=self.friend, status="accepted")

        friend_comment = Comment.objects.create(post=post, author=self.friend, comment="friend comment")
        own_comment = Comment.objects.create(post=post, author=self.comment_author, comment="my own comment")

        comments_url = reverse(
            "posts:api-post-comments",
            kwargs={"author_id": post.author_id, "post_id": post.id},
        )

        self.client.force_login(self.comment_author)
        response = self.client.get(comments_url)
        self.assertEqual(response.status_code, 200)
        comment_texts = [item["comment"] for item in response.json()["src"]]
        self.assertIn("my own comment", comment_texts)
        self.assertNotIn("friend comment", comment_texts)

        # Quick sanity check so this test doesn't accidentally pass with no comments.
        self.assertEqual(Comment.objects.filter(post=post).count(), 2)
        self.assertIsNotNone(friend_comment.id)
        self.assertIsNotNone(own_comment.id)

    # User Story 4: receiver of a public entry can see like count.
    def test_story4_public_entry_shows_likes_to_receiver(self):
        public_post = Post.objects.create(
            author=self.author,
            title="shared public",
            content="take a look",
            visibility=Post.Visibility.PUBLIC,
        )
        Like.objects.create(author=self.liker, post=public_post)

        self.client.force_login(self.receiver)
        detail_url = reverse(
            "posts:api-post-detail",
            kwargs={"author_id": public_post.author_id, "post_id": public_post.id},
        )
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["type"], "entry")
        self.assertEqual(payload["likes"]["count"], 1)