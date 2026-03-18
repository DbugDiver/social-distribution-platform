from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import Http404, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from collections import namedtuple
import markdown as md
from django.conf import settings
import requests
from datetime import datetime

from authors.models import Author, Follower
from .forms import PostForm
from .models import Comment, Like, Post
from django.db.models import Q

"""
Change citation (local project work):
- Added comments/likes web interaction flow (stream + detail + POST handlers).
- Added visibility/permission helpers for safe like/comment actions.
"""

try:
    import markdown
except ImportError:
    markdown = None

"""
Helper function to render markdown content to HTML. If the markdown library is not installed, it will return the original text.
"""
def _render_markdown(text: str) -> str:
    if not markdown:
        return text  # fallback: show as plain text if markdown lib not installed
    return markdown.markdown(text, extensions=["extra", "sane_lists"])


def _is_friend(user, other):
    # Changed section: mutual accepted follow check for FRIENDS visibility.
    return (
        Follower.objects.filter(follower=user, following=other, status="accepted").exists()
        and Follower.objects.filter(follower=other, following=user, status="accepted").exists()
    )


def _can_interact_with_post(user, post):
    # Changed section: centralized permission guard for like/comment actions.
    if post.deleted:
        return False
    if post.visibility in [Post.Visibility.PUBLIC, Post.Visibility.UNLISTED]:
        return user.is_authenticated
    if not user.is_authenticated:
        return False
    if user == post.author:
        return True
    return _is_friend(user, post.author)


def _visible_comments_for_viewer(user, post):
    # User Story 3: on FRIENDS entries, comments are visible to friends and each comment's author.
    comments = post.comments.select_related("author").prefetch_related("likes")
    if post.visibility != Post.Visibility.FRIENDS:
        return comments
    if not user.is_authenticated:
        return comments.none()
    if user == post.author or _is_friend(user, post.author):
        return comments
    return comments.filter(author=user)

"""
This function handle the logic for displaying the stream of posts.
It will GET the posts that are not deleted, ordered by created time (newest first).
Then it will loop through the posts. If the content type is markdown then convert it to HTML.
Finally, it will send the posts to the stream.html template for rendering.
"""
@login_required
def stream(request):
    user = request.user

    # --- LOCAL POSTS ---
    following_ids = Follower.objects.filter(
        follower=user,
        status="accepted",
    ).values_list("following_id", flat=True)

    local_posts = (
        Post.objects.filter(deleted=False)
        .filter(
            Q(author=user)
            | Q(visibility=Post.Visibility.PUBLIC)
            | Q(
                author_id__in=following_ids,
                visibility__in=[Post.Visibility.FRIENDS, Post.Visibility.UNLISTED],
            )
        )
        .prefetch_related("comments__author", "comments__likes", "likes")
        .order_by("-created")
    )

    # Prepare likes for the current user
    post_liked_ids = set(
        Like.objects.filter(author=user, post__in=local_posts).values_list("post_id", flat=True)
    )
    comment_liked_ids = set(
        Like.objects.filter(author=user, comment__post__in=local_posts).values_list("comment_id", flat=True)
    )

    for p in local_posts:
        if p.content_type == Post.ContentType.MARKDOWN:
            p.rendered = md.markdown(p.content or "", extensions=["extra"])
        else:
            p.rendered = None
        p.like_count = p.likes.count()
        p.comment_count = p.comments.count()
        p.liked_by_me = p.id in post_liked_ids
        p.comment_list = list(_visible_comments_for_viewer(user, p)[:3])
        for c in p.comment_list:
            c.like_count = c.likes.count()
            c.liked_by_me = c.id in comment_liked_ids

    # --- REMOTE POSTS ---
    class RemotePost:
    """Wrap remote post JSON to behave like a Post object for the template"""
    def __init__(self, data, node_url):
        # Copy data
        self.id = data.get("id")
        self.title = data.get("title")
        self.content = data.get("content")
        self.content_type = data.get("content_type")
        self.visibility = data.get("visibility")
        self.image = data.get("image")  # string URL from remote
        self.rendered = md.markdown(self.content or "", extensions=["extra"]) if self.content_type == "text/markdown" else self.content
        self.like_count = data.get("like_count", 0)
        self.comment_count = len(data.get("comments", []))
        self.liked_by_me = False
        self.remote = True
        self.node_url = node_url

        # Parse created string into datetime
        created_str = data.get("created")
        try:
            self.created = datetime.fromisoformat(created_str) if created_str else datetime.min
        except ValueError:
            self.created = datetime.min

        # Wrap author dict into an object that template can use
        author_data = data.get("author", {})
        self.author = type("AuthorObj", (), {
            "username": author_data.get("username", "Unknown"),
            "profileImage": author_data.get("profileImage", None)  # string URL or None
        })

        # Wrap comments into objects that template can use
        self.comment_list = []
        for c in data.get("comments", [])[:3]:
            comment_author = type("AuthorObj", (), {
                "username": c.get("author", {}).get("username", "Unknown")
            })
            comment_obj = type("CommentObj", (), {
                "id": c.get("id"),
                "comment": c.get("comment"),
                "author": comment_author,
                "like_count": c.get("like_count", 0),
                "liked_by_me": False
            })
            self.comment_list.append(comment_obj)

    current_node = request.build_absolute_uri("/").rstrip("/")
    remote_posts = []
    for node_url in getattr(settings, "REMOTE_NODES", []):
        node_url = node_url.strip("/")
        
        if node_url == current_node:
            continue
    
        try:
            r = requests.get(f"{node_url}/remote-posts/", timeout=3)
            if r.status_code == 200:
                for rp in r.json():
                    remote_posts.append(RemotePost(rp, node_url))
        except requests.RequestException:
            continue

    # --- MERGE AND SORT ---
    all_posts = list(local_posts) + remote_posts
    all_posts.sort(key=lambda x: getattr(x, "created", datetime.min), reverse=True)

    return render(
        request,
        "posts/stream.html",
        {
            "posts": all_posts,
            "feed_title": "Public Stream",
        },
    )
    
"""
This function handle the logic for displaying the details of a single post.
It will GET a single post by its ID, but only if it is not deleted. If it does not exist, return a 404 error page.
If markdown then it will convert it to HTML.
Finally, it will send the post content to the detail.html template for rendering.
"""
@login_required
def detail(request, post_id):
    """
    Display the details of a single post, local or remote.
    Handles visibility, Markdown rendering, comments, likes, and images.
    """
    post = None

    # --- Try local first ---
    try:
        if request.user.is_superuser:
            post = Post.objects.get(id=post_id)
        else:
            post = Post.objects.get(id=post_id, deleted=False)
    except Post.DoesNotExist:
        post = None

    if post:
        # --- Visibility checks ---
        if not request.user.is_superuser:
            if post.visibility == Post.Visibility.PUBLIC:
                pass
            elif post.visibility == Post.Visibility.UNLISTED:
                pass
            elif post.visibility == Post.Visibility.FRIENDS:
                if not request.user.is_authenticated:
                    return HttpResponseForbidden("Login required.")
                # FRIENDS: allow if friend, author, or has commented
                if (
                    request.user != post.author
                    and not _is_friend(request.user, post.author)
                    and not post.comments.filter(author=request.user).exists()
                ):
                    return HttpResponseForbidden("Not allowed.")
            else:
                return HttpResponseForbidden("Invalid visibility.")

        # --- Render Markdown ---
        rendered = (
            md.markdown(post.content or "", extensions=["extra"])
            if post.content_type == Post.ContentType.MARKDOWN
            else post.content
        )

        # --- Comments and likes ---
        comments = _visible_comments_for_viewer(request.user, post)
        comment_liked_ids = set()
        post_liked_by_me = False
        if request.user.is_authenticated:
            comment_liked_ids = set(
                Like.objects.filter(author=request.user, comment__post=post)
                .values_list("comment_id", flat=True)
            )
            post_liked_by_me = Like.objects.filter(author=request.user, post=post).exists()

        for c in comments:
            c.like_count = c.likes.count()
            c.liked_by_me = c.id in comment_liked_ids

        return render(
            request,
            "posts/detail.html",
            {
                "post": post,
                "rendered": rendered,
                "comments": comments,
                "post_liked_by_me": post_liked_by_me,
            },
        )

    # --- Try remote nodes ---
    class RemotePostDetail:
        """Wrap a remote post JSON for detail view"""
        def __init__(self, data, node_url):
            self.id = data.get("id")
            self.title = data.get("title")
            self.content = data.get("content")
            self.content_type = data.get("content_type")
            self.visibility = data.get("visibility")
            self.image = data.get("image")  # string URL
            self.rendered = (
                md.markdown(self.content or "", extensions=["extra"])
                if self.content_type == "text/markdown"
                else self.content
            )
            self.like_count = data.get("like_count", 0)
            self.comment_count = len(data.get("comments", []))
            self.liked_by_me = False
            self.remote = True
            self.node_url = node_url

            # Author
            author_data = data.get("author", {})
            self.author = type(
                "AuthorObj",
                (),
                {
                    "username": author_data.get("username", "Unknown"),
                    "profileImage": author_data.get("profileImage", None),
                },
            )

            # Comments
            self.comment_list = []
            for c in data.get("comments", []):
                comment_author = type(
                    "AuthorObj",
                    (),
                    {"username": c.get("author", {}).get("username", "Unknown")},
                )
                comment_obj = type(
                    "CommentObj",
                    (),
                    {
                        "id": c.get("id"),
                        "comment": c.get("comment"),
                        "author": comment_author,
                        "like_count": c.get("like_count", 0),
                        "liked_by_me": False,
                    },
                )
                self.comment_list.append(comment_obj)

    current_node = request.build_absolute_uri("/").rstrip("/")
    for node_url in getattr(settings, "REMOTE_NODES", []):
        node_url = node_url.rstrip("/")
        if node_url == current_node:
            continue

        try:
            r = requests.get(f"{node_url}/remote-posts/", timeout=3)
            if r.status_code == 200:
                remote_posts = r.json()
                for rp in remote_posts:
                    if str(rp.get("id")) == str(post_id):
                        remote_post = RemotePostDetail(rp, node_url)

                        # For remote posts, visibility rules can only be applied loosely
                        # (e.g., PUBLIC / UNLISTED)
                        if remote_post.visibility not in [
                            "PUBLIC",
                            "UNLISTED",
                        ]:
                            # Only allow friends posts if they are PUBLIC for cross-node
                            return HttpResponseForbidden("Not allowed.")

                        return render(
                            request,
                            "posts/detail.html",
                            {
                                "post": remote_post,
                                "rendered": remote_post.rendered,
                                "comments": remote_post.comment_list,
                                "post_liked_by_me": remote_post.liked_by_me,
                            },
                        )

        except requests.RequestException:
            continue
    raise Http404("Post not found")

@login_required
def add_comment(request, post_id):
    # User Story 2/3: HTML form endpoint for comment creation with visibility guard.
    post = get_object_or_404(Post, id=post_id, deleted=False)
    if request.method != "POST":
        raise Http404()
    if not _can_interact_with_post(request.user, post):
        return HttpResponseForbidden("Not allowed.")

    text = (request.POST.get("comment") or "").strip()
    if text:
        Comment.objects.create(
            post=post,
            author=request.user,
            comment=text,
            content_type=Comment.ContentType.PLAIN,
        )

    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or redirect("posts:detail", post_id=post.id).url
    return redirect(next_url)

def superuser_required(user):
    return user.is_superuser

@user_passes_test(superuser_required)
def author_posts(request, author_id):
    author = get_object_or_404(Author, id=author_id)
    posts = Post.objects.filter(author=author)

    return render(request, "posts/author_posts.html", {
        "author": author,
        "posts": posts
    })

@login_required
def like_post(request, post_id):
    # User Story 4: HTML form endpoint that records likes on shared/public entries.
    post = get_object_or_404(Post, id=post_id, deleted=False)
    if request.method != "POST":
        raise Http404()
    if not _can_interact_with_post(request.user, post):
        return HttpResponseForbidden("Not allowed.")

    Like.objects.get_or_create(author=request.user, post=post)
    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or redirect("posts:detail", post_id=post.id).url
    return redirect(next_url)


@login_required
def like_comment(request, post_id, comment_id):
    # User Story 3: HTML form endpoint to like comments if viewer is allowed to see that thread.
    post = get_object_or_404(Post, id=post_id, deleted=False)
    comment = get_object_or_404(Comment, id=comment_id, post=post)
    if request.method != "POST":
        raise Http404()
    if not _can_interact_with_post(request.user, post):
        return HttpResponseForbidden("Not allowed.")

    Like.objects.get_or_create(author=request.user, comment=comment)
    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or redirect("posts:detail", post_id=post.id).url
    return redirect(next_url)

"""
  Rosy: I have a form that I want to use for posting. I want this form to craete, and edit posts. 
  How do I implement these in views.py depening on is user is autheticated/logged In or not.
  ChatGPT: create and edit below are based on the answer provided
  Citation: ChatGPT, OpenAI, 2026-02-22, https://chatgpt.com/share/699ab22d-6bac-800a-ab03-5609dd01c80e
"""

"""
This function handle the logic for creating a new post.
It requires the user to be logged in.
If the request method is POST, it will validate the form data and create a new post with the current user as the author.
And then redirect the user to the stream page. 
If the request method is GET, it will display an empty form for creating a new post.
"""
@login_required
def create(request):
    if request.method == "POST":
        form = PostForm(request.POST, request.FILES)
        if form.is_valid():
            post = form.save(commit=False)
            post.author = request.user
            post.save()
            return redirect("posts:stream")
    else:
        form = PostForm()
    return render(request, "posts/create.html", {"form": form, "mode": "Create"})

"""
This function handle the logic for editing an existing post.
It requires the user to be logged in.  
It will GET the post by its ID, but only if it is not deleted. If it does not exist, return a 404 error page.
If the current user is not the author of the post, return a 404 error page.
If the request method is POST, it will validate the form data and update the post. And then redirect the user to the stream page. 
If the request method is GET, it will display a form pre-filled with the post data for editing. 
"""
@login_required
def edit(request, post_id):
    post = get_object_or_404(Post, id=post_id, deleted=False)
    if post.author_id != request.user.id:
        raise Http404()
    if request.method == "POST":
        form = PostForm(request.POST, request.FILES, instance=post)
        if form.is_valid():
            form.save()
            return redirect("posts:stream")
    else:
        form = PostForm(instance=post)

    return render(request, "posts/create.html", {"form": form, "mode": "Edit", "post": post})

"""
This function handle the logic for deleting an existing post.
It requires the user to be logged in.
It will GET the post by its ID, but only if it is not deleted. If it does not exist, return a 404 error page.
If the current user is not the author of the post, return a 404 error page.
If the request method is POST, it will mark the post as deleted and save it. And then redirect the user to the stream page. 
If the request method is GET, it will display a confirmation page for deleting the post. 
"""
@login_required
def delete(request, post_id):
    post = get_object_or_404(Post, id=post_id, deleted=False)
    if post.author_id != request.user.id:
        raise Http404()
    if request.method == "POST":
        post.deleted = True
        post.save(update_fields=["deleted", "updated"])
        return redirect("posts:stream")
    return render(request, "posts/delete_confirm.html", {"post": post})


@login_required
def followers_feed(request):
    """Show posts from friends only (mutual followers)"""
    author=request.user
    # Get friends: authors who you follow AND who follow you back
    following=Follower.objects.filter(follower=author, status="accepted").values_list("following", flat=True)
    followers=Follower.objects.filter(following=author, status="accepted").values_list("follower", flat=True)
    friends=Author.objects.filter(id__in=following).filter(id__in=followers)

    # Fetch posts by friends
    posts = Post.objects.filter(
            Q(author_id__in=following, visibility="UNLISTED") | #all unlisted posts from following
            Q(author__in=friends) |     #all mutual posts
            Q(author_id=request.user)   #my own created posts of all visibiliyt type
            ).filter(deleted=False).order_by("-created")
    context={"posts": posts,"feed_title": "Friends Feed",}
    return render(request, "posts/stream.html", context)

from django.http import JsonResponse

def remote_posts(request):
    posts = Post.objects.filter(
        deleted=False,
        visibility=Post.Visibility.PUBLIC
    ).order_by("-created")

    data = []

    for p in posts:
        data.append({
            "id": p.id,
            "title": p.title,
            "content": p.content,
            "content_type": p.content_type,
            "created": p.created.isoformat(),
            "author": {
                "username": p.author.username,
                "profileImage": p.author.profileImage.url if p.author.profileImage else "",
            },
            "like_count": p.likes.count(),
            "comments": [
                {
                    "author": c.author.username,
                    "comment": c.comment,
                }
                for c in p.comments.all()[:3]
            ]
        })

    return JsonResponse(data, safe=False)