from django.test import TestCase
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile

from authors.models import Author
from posts.models import Post

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