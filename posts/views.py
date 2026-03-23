from datetime import datetime
import hashlib
from urllib.parse import urljoin

import markdown as md
import requests
from django.conf import settings
from django.contrib.auth.decorators import login_required, user_passes_test
from django.db.models import Q
from django.http import Http404, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from authors.models import Author, Follower
from .forms import PostForm
from .models import Comment, Like, Post
import uuid
import requests
from django.conf import settings

# ---------- Markdown ----------

def _render_markdown(text: str) -> str:
    try:
        import markdown
    except ImportError:
        return text
    return markdown.markdown(text, extensions=["extra", "sane_lists"])


# ---------- Friendship / visibility ----------

def _is_friend(user, other):
    return (
        Follower.objects.filter(follower=user, following=other, status="accepted").exists()
        and Follower.objects.filter(follower=other, following=user, status="accepted").exists()
    )


def _can_interact_with_post(user, post):
    if post.deleted:
        return False

    if post.visibility in [Post.Visibility.PUBLIC, Post.Visibility.UNLISTED]:
        return user.is_authenticated

    if not user.is_authenticated:
        return False

    if post.is_remote:
        # For remote FRIENDS-only, we cannot reliably compute friendship unless you
        # also federate follower syncing. Allow only public/unlisted here.
        return False

    if user == post.author:
        return True

    return _is_friend(user, post.author)


def _visible_comments_for_viewer(user, post):
    comments = post.comments.select_related("author").prefetch_related("likes")

    if post.visibility != Post.Visibility.FRIENDS:
        return comments

    if not user.is_authenticated:
        return comments.none()

    if post.is_remote:
        return comments.none()

    if user == post.author or _is_friend(user, post.author):
        return comments

    return comments.filter(author=user)


# ---------- Local author identity helpers ----------

def _site_url():
    return getattr(settings, "SITE_URL", "").rstrip("/")

# ---------- Federation HTTP helpers ----------

def _auth_for_node(node_url):
    creds = getattr(settings, "REMOTE_NODE_CREDENTIALS", {}) or {}
    info = creds.get(node_url.rstrip("/"))
    if info and info.get("username") and info.get("password"):
        return (info["username"], info["password"])
    return None


def _candidate_post_endpoints(node_url):
    base = node_url.rstrip("/")
    return [f"{base}/api/public-posts/"]

def _try_get_json(url, auth=None, timeout=5):
    try:
        resp = requests.get(
            url,
            auth=auth,
            timeout=timeout,
            headers={"Accept": "application/json"},
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None


def _try_post_json(url, payload, auth=None, timeout=5):
    try:
        resp = requests.post(
            url,
            json=payload,
            auth=auth,
            timeout=timeout,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        return resp.status_code in [200, 201, 202]
    except Exception:
        return False


def _normalize_author_id(value):
    raw = (value or "").strip().rstrip("/")
    if not raw:
        return ""
    # Treat /authors/<id> and /authors/api/authors/<id> as the same identity.
    return raw.replace("/authors/api/authors/", "/authors/")


def _remote_like_matches_user(raw_like, user):
    author = raw_like.get("author", {}) if isinstance(raw_like.get("author"), dict) else {}
    candidate_ids = {
        _normalize_author_id(author.get("id")),
        _normalize_author_id(author.get("url")),
    }
    local_ids = {
        _normalize_author_id(f"{_site_url()}/authors/{user.id}"),
        _normalize_author_id(f"{_site_url()}/authors/api/authors/{user.id}"),
    }
    return bool(candidate_ids.intersection(local_ids))


def _parse_datetime(value):
    from datetime import datetime
    from django.utils import timezone

    if not value:
        return timezone.now()
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return timezone.now()



def _normalize_remote_post(raw, node_url):
    author = raw.get("author") if isinstance(raw.get("author"), dict) else {}
    remote_post_id = raw.get("id") or raw.get("remote_id") or raw.get("url")
    comments_obj = raw.get("comments") if isinstance(raw.get("comments"), dict) else {}
    likes_obj = raw.get("likes") if isinstance(raw.get("likes"), dict) else {}

    # Base fields
    content_type = raw.get("contentType") or raw.get("content_type") or Post.ContentType.PLAIN
    content = raw.get("content") or ""

    # Image URL from remote JSON
    image_url = (raw.get("image") or "").strip()
    if image_url.startswith("/") and node_url:
        image_url = f"{node_url.rstrip('/')}{image_url}"

    # If remote post is an image, build a data URL
    if content_type.startswith("image/") and content:
        image_url = f"data:{content_type},{content}"

    return {
        "remote_id": str(remote_post_id) if remote_post_id else "",
        "title": raw.get("title") or "",
        "content": content,               
        "content_type": content_type,      
        "visibility": raw.get("visibility") or Post.Visibility.PUBLIC,
        "published": raw.get("published") or raw.get("created") or raw.get("updated"),
        "node_url": node_url.rstrip("/"),
        "remote_author_url": author.get("id") or author.get("url") or "",
        "remote_author_name": author.get("displayName") or author.get("username") or "Remote Author",
        "remote_author_host": author.get("host") or node_url.rstrip("/"),
        "remote_author_image": author.get("profileImage") or "",
        "remote_image": image_url,        
        "remote_comments_url": comments_obj.get("id") or "",
        "remote_likes_url": likes_obj.get("id") or "",
        "remote_comment_count": comments_obj.get("count", 0),
        "remote_like_count": likes_obj.get("count", 0),
    }


def _upsert_remote_post_cache(data):
    remote_id = data["remote_id"]
    if not remote_id:
        return None

    post, _ = Post.objects.update_or_create(
        remote_id=remote_id,
        defaults={
            "author": None,
            "is_remote": True,
            "node_url": data["node_url"],
            "remote_author_url": data["remote_author_url"],
            "remote_author_name": data["remote_author_name"],
            "remote_author_host": data["remote_author_host"],
            "remote_image": data.get("remote_image", ""),
            "title": data["title"],
            "content": data["content"],
            "content_type": data["content_type"][:50],
            "visibility": data["visibility"] if data["visibility"] in Post.Visibility.values else Post.Visibility.PUBLIC,
            "published": _parse_datetime(data["published"]),
            "deleted": False,
        },
    )
    
     # attach transient attrs used by templates/views
    post.remote_comments_url = data["remote_comments_url"]
    post.remote_likes_url = data["remote_likes_url"]
    post.remote_author_image = data.get("remote_author_image", "")
    post.remote_comment_count = data["remote_comment_count"]
    post.remote_like_count = data["remote_like_count"]
    return post

def _fetch_remote_public_posts():
    cached = []

    for node in getattr(settings, "REMOTE_NODES", []):
        node = node.rstrip("/")
        if node == _site_url():
            continue

        auth = _auth_for_node(node)

        for endpoint in _candidate_post_endpoints(node):
            data = _try_get_json(endpoint, auth=auth)
            if not data:
                continue

            items = data.get("items", data) if isinstance(data, dict) else data
            if not isinstance(items, list):
                continue

            for raw in items:
                if not isinstance(raw, dict):
                    continue
                if raw.get("visibility") != Post.Visibility.PUBLIC:
                    continue

                normalized = _normalize_remote_post(raw, node)
                post = _upsert_remote_post_cache(normalized)
                if post:
                    cached.append(post)
            break

    return cached

def _candidate_single_post_endpoints(node_url, remote_post_id):
    base = node_url.rstrip("/")
    rid = str(remote_post_id).strip("/")
    return [
        rid,
        f"{base}/{rid}/",
    ]

def _get_remote_post_or_404(post):
    if not post.is_remote or not post.node_url or not post.remote_id:
        return post

    auth = _auth_for_node(post.node_url)
    for endpoint in _candidate_single_post_endpoints(post.node_url, post.remote_id):
        data = _try_get_json(endpoint, auth=auth)
        if not data or not isinstance(data, dict):
            continue

        normalized = _normalize_remote_post(data, post.node_url)
        updated = _upsert_remote_post_cache(normalized)
        if updated:
            return updated

    return post

def _author_inbox_url(author_url):
    if not author_url:
        return None
    return f"{author_url.rstrip('/')}/inbox/"

def _local_author_payload(user):
    base = _site_url()
    return {
        "type": "author",
        "id": f"{base}/authors/{user.id}",
        "host": base,
        "displayName": getattr(user, "displayName", "") or getattr(user, "username", "Local User"),
        "url": f"{base}/authors/{user.id}",
    }
    
def _fetch_remote_comments(post, viewer=None):
    comments_url = getattr(post, "remote_comments_url", "") or ""

    if not comments_url:
        remote_id = str(post.remote_id).rstrip("/")
        if "/api/authors/" in remote_id and "/posts/" in remote_id:
            comments_url = remote_id.replace("/api/authors/", "/api/public/authors/") + "/comments/"
        else:
            comments_url = remote_id + "/comments/"

    auth = _auth_for_node(post.node_url.rstrip("/")) if post.node_url else None
    data = _try_get_json(comments_url, auth=auth)

    if not data:
        return []

    items = data.get("src", data.get("items", [])) if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []

    normalized = []
    for raw in items:
        author = raw.get("author", {}) if isinstance(raw.get("author"), dict) else {}
        likes_obj = raw.get("likes") if isinstance(raw.get("likes"), dict) else {}
        comment_id = str(raw.get("id") or "").strip()
        comment_likes_url = str(likes_obj.get("id") or "").strip()
        if not comment_likes_url and comment_id:
            base_comments_url = comments_url.rstrip("/")
            if comment_id.startswith("http://") or comment_id.startswith("https://"):
                comment_likes_url = f"{comment_id.rstrip('/')}/likes/"
            else:
                comment_likes_url = f"{base_comments_url}/{comment_id}/likes/"
        liked_by_me = False
        if viewer and comment_likes_url:
            likes_data = _try_get_json(
                comment_likes_url,
                auth=(_auth_for_node(post.node_url.rstrip("/")) if post.node_url else None),
            )
            likes_items = likes_data.get("src", likes_data.get("items", [])) if isinstance(likes_data, dict) else likes_data
            if isinstance(likes_items, list):
                liked_by_me = any(_remote_like_matches_user(item, viewer) for item in likes_items if isinstance(item, dict))

        normalized.append({
            "id": comment_id,
            "comment": raw.get("comment", ""),
            "content_type": raw.get("contentType", Comment.ContentType.PLAIN),
            "published": raw.get("published", ""),
            "author_name": author.get("displayName") or author.get("username") or "Remote Author",
            "like_count": likes_obj.get("count", 0),
            "likes_url": comment_likes_url,
            "liked_by_me": liked_by_me,
        })
    return normalized


def _fetch_remote_likes(post):
    likes_url = getattr(post, "remote_likes_url", "") or ""

    if not likes_url:
        remote_id = str(post.remote_id).rstrip("/")
        if "/api/authors/" in remote_id and "/posts/" in remote_id:
            likes_url = remote_id.replace("/api/authors/", "/api/public/authors/") + "/likes/"
        else:
            likes_url = remote_id + "/likes/"

    auth = _auth_for_node(post.node_url.rstrip("/")) if post.node_url else None
    data = _try_get_json(likes_url, auth=auth)
    if not data:
        return []

    items = data.get("src", data.get("items", [])) if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []

    normalized = []
    for raw in items:
        author = raw.get("author", {}) if isinstance(raw.get("author"), dict) else {}
        normalized.append({
            "id": raw.get("id"),
            "author_id": author.get("id") or author.get("url") or "",
            "author_name": author.get("displayName") or author.get("username") or "Remote Author",
            "summary": raw.get("summary", ""),
            "published": raw.get("published", ""),
        })
    return normalized


def _send_remote_comment(user, post, text):
    comments_url = getattr(post, "remote_comments_url", "") or ""

    if not comments_url:
        remote_id = str(post.remote_id).rstrip("/")
        if "/api/authors/" in remote_id and "/posts/" in remote_id:
            comments_url = remote_id.replace("/api/authors/", "/api/public/authors/") + "/comments/"
        else:
            comments_url = remote_id + "/comments/"

    payload = {
        "type": "comment",
        "id": f"{_site_url()}/federation/comments/{uuid.uuid4()}",
        "author": _local_author_payload(user),
        "comment": text,
        "contentType": "text/plain",
        "published": timezone.now().isoformat(),
    }

    auth = _auth_for_node(post.node_url.rstrip("/")) if post.node_url else None

    try:
        resp = requests.post(
            comments_url,
            json=payload,
            auth=auth,
            timeout=5,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        return resp.status_code in [200, 201, 202]
    except Exception as e:
        return False


def _send_remote_like(user, post):
    likes_url = getattr(post, "remote_likes_url", "") or ""

    if not likes_url:
        remote_id = str(post.remote_id).rstrip("/")
        if "/api/authors/" in remote_id and "/posts/" in remote_id:
            likes_url = remote_id.replace("/api/authors/", "/api/public/authors/") + "/likes/"
        else:
            likes_url = remote_id + "/likes/"

    stable_like_id = f"{_site_url()}/federation/likes/{user.id}/{post.id}"

    payload = {
        "type": "like",
        "id": stable_like_id,
        "author": _local_author_payload(user),
        "object": post.remote_id,
        "published": timezone.now().isoformat(),
    }

    auth = _auth_for_node(post.node_url.rstrip("/")) if post.node_url else None

    try:
        existing = _try_get_json(likes_url, auth=auth)
        existing_items = existing.get("src", existing.get("items", [])) if isinstance(existing, dict) else existing
        already_liked = isinstance(existing_items, list) and any(
            isinstance(item, dict) and (
                str(item.get("id") or "").strip() == stable_like_id
                or _remote_like_matches_user(item, user)
            )
            for item in existing_items
        )

        if already_liked:
            resp = requests.delete(
                likes_url,
                json=payload,
                auth=auth,
                timeout=5,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
            )
        else:
            resp = requests.post(
                likes_url,
                json=payload,
                auth=auth,
                timeout=5,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
            )
        return resp.status_code in [200, 201, 202, 204]
    except Exception as e:
        return False


def _send_remote_comment_like(user, post, remote_comment_id, remote_likes_url=""):
    likes_url = (remote_likes_url or "").strip()
    comment_object = (remote_comment_id or "").strip()

    if not likes_url and comment_object:
        if comment_object.startswith("http://") or comment_object.startswith("https://"):
            likes_url = f"{comment_object.rstrip('/')}/likes/"
        else:
            remote_id = str(post.remote_id or "").rstrip("/")
            if "/api/authors/" in remote_id and "/posts/" in remote_id:
                comments_base = remote_id.replace("/api/authors/", "/api/public/authors/") + "/comments"
            else:
                comments_base = remote_id + "/comments"
            likes_url = f"{comments_base.rstrip('/')}/{comment_object}/likes/"

    if not likes_url:
        return False

    if not comment_object:
        # Derive object from likes endpoint for remote servers that validate Like.object.
        comment_object = likes_url.rstrip("/").replace("/likes", "")

    comment_fingerprint = hashlib.sha256(comment_object.encode("utf-8")).hexdigest()[:24]
    stable_like_id = f"{_site_url()}/federation/likes/{user.id}/{post.id}/{comment_fingerprint}"
    payload = {
        "type": "like",
        "id": stable_like_id,
        "author": _local_author_payload(user),
        "object": comment_object,
        "published": timezone.now().isoformat(),
    }

    auth = _auth_for_node(post.node_url.rstrip("/")) if post.node_url else None
    try:
        existing = _try_get_json(likes_url, auth=auth)
        existing_items = existing.get("src", existing.get("items", [])) if isinstance(existing, dict) else existing
        already_liked = isinstance(existing_items, list) and any(
            isinstance(item, dict) and (
                str(item.get("id") or "").strip() == stable_like_id
                or _remote_like_matches_user(item, user)
            )
            for item in existing_items
        )

        if already_liked:
            resp = requests.delete(
                likes_url,
                json=payload,
                auth=auth,
                timeout=5,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
            )
        else:
            resp = requests.post(
                likes_url,
                json=payload,
                auth=auth,
                timeout=5,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
            )
        return resp.status_code in [200, 201, 202, 204]
    except Exception:
        return False

# ---------- Stream ----------

@login_required
def stream(request):
    user = request.user

    following_ids = Follower.objects.filter(
        follower=user,
        status="accepted",
    ).values_list("following_id", flat=True)

    local_posts = list(
        Post.objects.filter(deleted=False, is_remote=False)
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

    remote_posts = _fetch_remote_public_posts()

    # dedupe
    combined_posts = local_posts + list(remote_posts)
    seen = set()
    all_posts = []
    for p in combined_posts:
        canonical_id = str(getattr(p, "remote_id", None) or p.id)
        if canonical_id in seen:
            continue
        seen.add(canonical_id)
        all_posts.append(p)

    post_liked_ids = set(
        Like.objects.filter(author=user, post__in=[p for p in local_posts if not p.is_remote]).values_list("post_id", flat=True)
    )
    comment_liked_ids = set(
        Like.objects.filter(author=user, comment__post__in=[p for p in local_posts if not p.is_remote]).values_list("comment_id", flat=True)
    )

    for p in all_posts:
        if p.content_type == Post.ContentType.MARKDOWN:
            p.rendered = md.markdown(p.content or "", extensions=["extra"])
        else:
            p.rendered = None

        if p.is_remote:
            remote_comments = _fetch_remote_comments(p, viewer=user)
            remote_likes = _fetch_remote_likes(p)
            p.remote_comment_list = remote_comments[:3]
            p.remote_like_list = remote_likes
            p.comment_count = len(remote_comments)
            p.like_count = len(remote_likes)
            p.liked_by_me = any(_normalize_author_id(l.get("author_id")) in {
                _normalize_author_id(f"{_site_url()}/authors/{user.id}"),
                _normalize_author_id(f"{_site_url()}/authors/api/authors/{user.id}"),
            } for l in remote_likes)
            p.comment_list = []
        else:
            p.like_count = p.likes.count()
            p.comment_count = p.comments.count()
            p.liked_by_me = p.id in post_liked_ids
            p.comment_list = list(_visible_comments_for_viewer(user, p)[:3])
            p.remote_comment_list = []
            p.remote_like_list = []
            for c in p.comment_list:
                c.like_count = c.likes.count()
                c.liked_by_me = c.id in comment_liked_ids

    all_posts = sorted(
        all_posts,
        key=lambda p: p.effective_published or p.created,
        reverse=True,
    )

    return render(
        request,
        "posts/stream.html",
        {
            "posts": all_posts,
            # Keep legacy title for test and UI compatibility.
            "feed_title": "Public Stream",
        },
    )

# ---------- Detail ----------
def detail(request, post_id):
    if request.user.is_superuser:
        post = get_object_or_404(Post, id=post_id)
    else:
        post = get_object_or_404(Post, id=post_id, deleted=False)

    if post.content_type == Post.ContentType.MARKDOWN:
        rendered = _render_markdown(post.content)
    else:
        rendered = None

    if post.is_remote:
        remote_comments = _fetch_remote_comments(post, viewer=request.user)
        remote_likes = _fetch_remote_likes(post)
        post_liked_by_me = any(_normalize_author_id(l.get("author_id")) in {
            _normalize_author_id(f"{_site_url()}/authors/{request.user.id}"),
            _normalize_author_id(f"{_site_url()}/authors/api/authors/{request.user.id}"),
        } for l in remote_likes)

        return render(
            request,
            "posts/detail.html",
            {
                "post": post,
                "rendered": rendered,
                "comments": [],
                "remote_comments": remote_comments,
                "remote_likes": remote_likes,
                "post_liked_by_me": post_liked_by_me,
            },
        )

    # local post logic
    if post.visibility == Post.Visibility.PUBLIC:
        pass
    elif post.visibility == Post.Visibility.UNLISTED:
        pass
    elif post.visibility == Post.Visibility.FRIENDS:
        if not request.user.is_authenticated:
            return HttpResponseForbidden("Login required.")
        if (
            request.user != post.author
            and not _is_friend(request.user, post.author)
            and not post.comments.filter(author=request.user).exists()
        ):
            return HttpResponseForbidden("Not allowed.")
    else:
        return HttpResponseForbidden("Invalid visibility.")

    comments = _visible_comments_for_viewer(request.user, post)
    comment_liked_ids = set()
    post_liked_by_me = False

    if request.user.is_authenticated:
        comment_liked_ids = set(
            Like.objects.filter(author=request.user, comment__post=post).values_list("comment_id", flat=True)
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
            "remote_comments": [],
            "remote_likes": [],
            "post_liked_by_me": post_liked_by_me,
        },
    )


# ---------- Comment ----------

@login_required
def add_comment(request, post_id):
    post = get_object_or_404(Post, id=post_id, deleted=False)

    if request.method != "POST":
        raise Http404()

    text = (request.POST.get("comment") or "").strip()
    if not text:
        next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or redirect("posts:detail", post_id=post.id).url
        return redirect(next_url)

    if post.is_remote:
        ok = _send_remote_comment(request.user, post, text)
        if not ok:
            return HttpResponseForbidden("Could not send remote comment.")
    else:
        if not _can_interact_with_post(request.user, post):
            return HttpResponseForbidden("Not allowed.")

        Comment.objects.create(
            post=post,
            author=request.user,
            comment=text,
            content_type=Comment.ContentType.PLAIN,
        )

    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or redirect("posts:detail", post_id=post.id).url
    return redirect(next_url)

# ---------- Superuser helper ----------

def superuser_required(user):
    return user.is_superuser


@user_passes_test(superuser_required)
def author_posts(request, author_id):
    author = get_object_or_404(Author, id=author_id)
    posts = Post.objects.filter(author=author)
    return render(request, "posts/author_posts.html", {"author": author, "posts": posts})


# ---------- Like post ----------

@login_required
def like_post(request, post_id):
    post = get_object_or_404(Post, id=post_id, deleted=False)

    if request.method != "POST":
        raise Http404()

    if post.is_remote:
        ok = _send_remote_like(request.user, post)
        if not ok:
            return HttpResponseForbidden("Could not send remote like.")
    else:
        if not _can_interact_with_post(request.user, post):
            return HttpResponseForbidden("Not allowed.")
        existing = Like.objects.filter(author=request.user, post=post).first()
        if existing:
            existing.delete()
        else:
            Like.objects.create(author=request.user, post=post)

    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or redirect("posts:detail", post_id=post.id).url
    return redirect(next_url)


# ---------- Like comment ----------

@login_required
def like_comment(request, post_id, comment_id):
    post = get_object_or_404(Post, id=post_id, deleted=False)

    if request.method != "POST":
        raise Http404()

    if post.is_remote:
        return HttpResponseForbidden("Use remote comment like endpoint.")

    comment = get_object_or_404(Comment, id=comment_id, post=post)

    if not _can_interact_with_post(request.user, post):
        return HttpResponseForbidden("Not allowed.")

    existing = Like.objects.filter(author=request.user, comment=comment).first()
    if existing:
        existing.delete()
    else:
        Like.objects.create(author=request.user, comment=comment)

    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or redirect("posts:detail", post_id=post.id).url
    return redirect(next_url)


@login_required
def like_remote_comment(request, post_id):
    post = get_object_or_404(Post, id=post_id, deleted=False)

    if request.method != "POST":
        raise Http404()

    if not post.is_remote:
        return HttpResponseForbidden("Not a remote post.")

    remote_comment_id = (request.POST.get("remote_comment_id") or "").strip()
    remote_likes_url = (request.POST.get("remote_likes_url") or "").strip()

    ok = _send_remote_comment_like(request.user, post, remote_comment_id, remote_likes_url)
    if not ok:
        return HttpResponseForbidden("Could not send remote comment like.")

    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or redirect("posts:detail", post_id=post.id).url
    return redirect(next_url)


# ---------- Create ----------

@login_required
def create(request):
    if request.method == "POST":
        form = PostForm(request.POST, request.FILES)
        if form.is_valid():
            post = form.save(commit=False)
            post.author = request.user
            post.is_remote = False
            post.node_url = _site_url() or None
            post.remote_author_url = None
            post.remote_author_name = None
            post.remote_author_host = None
            # If an image was uploaded, treat this as an image post for federation.
            if post.image and not post.content:
                # Default to PNG; you can branch on file extension if you want.
                post.content_type = Post.ContentType.IMAGE_PNG
            post.published = timezone.now()
            post.save()

            post.remote_id = f"{_site_url()}/api/authors/{request.user.id}/posts/{post.id}/"
            post.save(update_fields=["remote_id"])

            return redirect("posts:stream")
    else:
        form = PostForm()

    return render(request, "posts/create.html", {"form": form, "mode": "Create"})


# ---------- Edit ----------

@login_required
def edit(request, post_id):
    post = get_object_or_404(Post, id=post_id, deleted=False)

    if post.is_remote:
        raise Http404()

    if post.author_id != request.user.id:
        raise Http404()

    if request.method == "POST":
        form = PostForm(request.POST, request.FILES, instance=post)
        if form.is_valid():
            updated = form.save(commit=False)
            updated.updated = timezone.now()
            updated.save()
            return redirect("posts:stream")
    else:
        form = PostForm(instance=post)

    return render(request, "posts/create.html", {"form": form, "mode": "Edit", "post": post})


# ---------- Delete ----------

@login_required
def delete(request, post_id):
    post = get_object_or_404(Post, id=post_id, deleted=False)

    if post.is_remote:
        raise Http404()

    if post.author_id != request.user.id:
        raise Http404()

    if request.method == "POST":
        post.deleted = True
        post.save(update_fields=["deleted", "updated"])
        return redirect("posts:stream")

    return render(request, "posts/delete_confirm.html", {"post": post})


# ---------- Followers / friends feed ----------

@login_required
def followers_feed(request):
    author = request.user

    following = Follower.objects.filter(
        follower=author,
        status="accepted",
    ).values_list("following", flat=True)

    followers = Follower.objects.filter(
        following=author,
        status="accepted",
    ).values_list("follower", flat=True)

    friends = Author.objects.filter(id__in=following).filter(id__in=followers)

    posts = Post.objects.filter(
        Q(author_id__in=following, visibility=Post.Visibility.UNLISTED, is_remote=False)
        | Q(author__in=friends, is_remote=False)
        | Q(author_id=author, is_remote=False)
    ).filter(deleted=False).order_by("-created")

    for p in posts:
        if p.content_type == Post.ContentType.MARKDOWN:
            p.rendered = md.markdown(p.content or "", extensions=["extra"])
        else:
            p.rendered = None
        p.like_count = p.likes.count()
        p.comment_count = p.comments.count()
        p.comment_list = list(_visible_comments_for_viewer(author, p)[:3])

    return render(
        request,
        "posts/stream.html",
        {
            "posts": posts,
            "feed_title": "Friends Feed",
        },
    )