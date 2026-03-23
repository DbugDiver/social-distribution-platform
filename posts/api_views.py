from urllib.parse import urlencode, urlparse
import json
import requests
import base64
import mimetypes

from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.core.paginator import EmptyPage, Paginator
from django.db.models import Q
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt

from authors.models import Author, Follower
from .models import Comment, Like, Post


def _normalized_author_url(value):
    raw = (value or "").strip().rstrip("/")
    if not raw:
        return ""
    return raw.replace("/authors/api/authors/", "/authors/")


def _author_obj(author: Author, request):
    # Federation safety: remote artifacts can exist without a local Author relation.
    # Return a minimal placeholder instead of raising when author is None.
    if not author:
        return {
            "type": "author",
            "id": "",
            "host": request.build_absolute_uri("/"),
            "displayName": "Remote Author",
            "url": "",
            "github": "",
            "profileImage": "",
        }

    author_path = reverse("author-profile", kwargs={"pk": author.id})
    fqid = request.build_absolute_uri(author_path)
    profile_image = ""
    if getattr(author, "profileImage", None):
        try:
            profile_image = request.build_absolute_uri(author.profileImage.url)
        except Exception:
            profile_image = ""

    return {
        "type": "author",
        "id": fqid,
        "host": getattr(author, "host", "") or request.build_absolute_uri("/"),
        "displayName": getattr(author, "displayName", "") or getattr(author, "username", "Unknown"),
        "url": fqid,
        "github": getattr(author, "github", "") or "",
        "profileImage": profile_image,
    }


def _is_friend(user: Author, other: Author):
    return (
        Follower.objects.filter(follower=user, following=other, status="accepted").exists()
        and Follower.objects.filter(follower=other, following=user, status="accepted").exists()
    )


def _follows(user: Author, other: Author):
    return Follower.objects.filter(
        follower=user,
        following=other,
        status="accepted",
    ).exists()


def _can_view_post(user, post: Post):
    if post.deleted:
        return False
    if post.visibility in [Post.Visibility.PUBLIC, Post.Visibility.UNLISTED]:
        return user.is_authenticated
    if not user.is_authenticated:
        return False
    if user.id == post.author_id:
        return True
    return _is_friend(user, post.author)


def _can_view_post_comments(user, post: Post):
    if _can_view_post(user, post):
        return True
    if not user.is_authenticated:
        return False
    if post.visibility != Post.Visibility.FRIENDS:
        return False
    return post.comments.filter(author=user).exists()


def _visible_comments_queryset(user, post: Post):
    base_qs = post.comments.select_related("author")
    if post.visibility != Post.Visibility.FRIENDS:
        return base_qs
    if not user.is_authenticated:
        return base_qs.none()
    if user.id == post.author_id or _is_friend(user, post.author):
        return base_qs
    return base_qs.filter(author=user)


def _pagination_params(request):
    try:
        page = max(1, int(request.GET.get("page", 1)))
    except (TypeError, ValueError):
        page = 1

    try:
        size = max(1, min(100, int(request.GET.get("size", 5))))
    except (TypeError, ValueError):
        size = 5

    return page, size


def _paginated_collection(*, request, base_path, collection_type, queryset, serializer):
    page, size = _pagination_params(request)
    paginator = Paginator(queryset, size)

    try:
        page_obj = paginator.page(page)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages) if paginator.num_pages else []

    if page_obj:
        src = [serializer(obj, request) for obj in page_obj.object_list]
        current_page = page_obj.number
    else:
        src = []
        current_page = 1

    query = urlencode({"page": current_page, "size": size})
    collection_id = request.build_absolute_uri(f"{base_path}?{query}")

    return {
        "type": collection_type,
        "id": collection_id,
        "page": current_page,
        "size": size,
        "count": paginator.count,
        "src": src,
    }


def _comment_obj(comment: Comment, request):
    post_path = reverse(
        "posts:api-post-detail",
        kwargs={"author_id": comment.post.author_id, "post_id": comment.post_id},
    )
    comment_path = reverse(
        "posts:api-comment-likes",
        kwargs={
            "author_id": comment.post.author_id,
            "post_id": comment.post_id,
            "comment_id": comment.id,
        },
    ).replace("/likes/", "/")
    likes_path = reverse(
        "posts:api-comment-likes",
        kwargs={
            "author_id": comment.post.author_id,
            "post_id": comment.post_id,
            "comment_id": comment.id,
        },
    )

    if comment.author:
        author_obj = _author_obj(comment.author, request)
    else:
        author_obj = {
            "type": "author",
            "id": comment.remote_author_url or "",
            "host": comment.remote_author_host or "",
            "displayName": comment.remote_author_name or "Remote Author",
            "url": comment.remote_author_url or "",
            "github": "",
            "profileImage": "",
        }

    return {
        "type": "comment",
        "author": author_obj,
        "comment": comment.comment,
        "contentType": comment.content_type,
        "published": comment.published.isoformat(),
        "id": request.build_absolute_uri(comment_path),
        "post": request.build_absolute_uri(post_path),
        "likes": {
            "type": "likes",
            "id": request.build_absolute_uri(likes_path),
            "count": comment.likes.count(),
        },
    }


def _like_obj(like: Like, request):
    if like.author:
        author_name = getattr(like.author, "displayName", "") or getattr(like.author, "username", "Unknown")
        author_obj = _author_obj(like.author, request)
    else:
        author_name = like.remote_author_name or "Remote Author"
        author_obj = {
            "type": "author",
            "id": like.remote_author_url or "",
            "host": like.remote_author_host or "",
            "displayName": like.remote_author_name or "Remote Author",
            "url": like.remote_author_url or "",
            "github": "",
            "profileImage": "",
        }

    if like.post_id:
        object_path = reverse(
            "posts:api-post-detail",
            kwargs={"author_id": like.post.author_id, "post_id": like.post_id},
        )
        summary = f"{author_name} likes your post"
    else:
        object_path = reverse(
            "posts:api-comment-likes",
            kwargs={
                "author_id": like.comment.post.author_id,
                "post_id": like.comment.post_id,
                "comment_id": like.comment_id,
            },
        ).replace("/likes/", "/")
        summary = f"{author_name} likes your comment"

    like_path = reverse("posts:api-like-detail", kwargs={"like_id": like.id})

    return {
        "type": "like",
        "summary": summary,
        "author": author_obj,
        "object": request.build_absolute_uri(object_path),
        "id": request.build_absolute_uri(like_path) if not like.remote_id else str(like.remote_id),
        "published": like.created.isoformat(),
    }


@login_required
def post_detail_api(request, author_id, post_id):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    post = get_object_or_404(Post, id=post_id, author_id=author_id)
    if not _can_view_post(request.user, post):
        return JsonResponse({"detail": "Not allowed."}, status=403)

    post_path = reverse(
        "posts:api-post-detail",
        kwargs={"author_id": post.author_id, "post_id": post.id},
    )
    comments_path = reverse(
        "posts:api-post-comments",
        kwargs={"author_id": post.author_id, "post_id": post.id},
    )
    likes_path = reverse(
        "posts:api-post-likes",
        kwargs={"author_id": post.author_id, "post_id": post.id},
    )

    if post.is_remote:
        image_url = post.remote_image or ""
    else:
        image_url = request.build_absolute_uri(post.image.url) if post.image else ""

    payload = {
        "type": "entry",
        "id": request.build_absolute_uri(post_path),
        "title": post.title,
        "contentType": post.content_type,
        "content": post.content,
        "author": _author_obj(post.author, request),
        "visibility": post.visibility,
        "published": (post.published or post.created).isoformat(),
        "updated": post.updated.isoformat(),
        "unlisted": post.visibility == Post.Visibility.UNLISTED,
        "image": image_url,
        "comments": {
            "type": "comments",
            "id": request.build_absolute_uri(comments_path),
            "count": post.comments.count(),
        },
        "likes": {
            "type": "likes",
            "id": request.build_absolute_uri(likes_path),
            "count": post.likes.count(),
        },
    }
    return JsonResponse(payload, status=200)


@login_required
def post_comments_api(request, author_id, post_id):
    post = get_object_or_404(Post, id=post_id, author_id=author_id)

    if not _can_view_post_comments(request.user, post):
        return JsonResponse({"detail": "Not allowed."}, status=403)

    if request.method == "POST":
        if post.deleted:
            return JsonResponse({"detail": "Deleted entries cannot be commented on."}, status=400)

        text = (request.POST.get("comment") or "").strip()
        content_type = request.POST.get("contentType", Comment.ContentType.PLAIN)

        if not text:
            return JsonResponse({"detail": "comment is required."}, status=400)

        if content_type not in [Comment.ContentType.PLAIN, Comment.ContentType.MARKDOWN]:
            return JsonResponse({"detail": "Invalid contentType."}, status=400)

        comment = Comment.objects.create(
            post=post,
            author=request.user,
            comment=text,
            content_type=content_type,
        )
        return JsonResponse(_comment_obj(comment, request), status=201)

    if request.method == "GET":
        base_path = reverse(
            "posts:api-post-comments",
            kwargs={"author_id": post.author_id, "post_id": post.id},
        )
        payload = _paginated_collection(
            request=request,
            base_path=base_path,
            collection_type="comments",
            queryset=_visible_comments_queryset(request.user, post),
            serializer=_comment_obj,
        )
        return JsonResponse(payload, status=200)

    return HttpResponseNotAllowed(["GET", "POST"])


@login_required
def post_likes_api(request, author_id, post_id):
    post = get_object_or_404(Post, id=post_id, author_id=author_id)

    if not _can_view_post(request.user, post):
        return JsonResponse({"detail": "Not allowed."}, status=403)

    if request.method == "POST":
        if post.deleted:
            return JsonResponse({"detail": "Deleted entries cannot be liked."}, status=400)

        like, created = Like.objects.get_or_create(author=request.user, post=post)
        return JsonResponse(_like_obj(like, request), status=201 if created else 200)

    if request.method == "GET":
        base_path = reverse(
            "posts:api-post-likes",
            kwargs={"author_id": post.author_id, "post_id": post.id},
        )
        payload = _paginated_collection(
            request=request,
            base_path=base_path,
            collection_type="likes",
            queryset=post.likes.select_related("author", "post"),
            serializer=_like_obj,
        )
        return JsonResponse(payload, status=200)

    return HttpResponseNotAllowed(["GET", "POST"])


@login_required
def comment_likes_api(request, author_id, post_id, comment_id):
    post = get_object_or_404(Post, id=post_id, author_id=author_id)
    comment = get_object_or_404(Comment, id=comment_id, post=post)

    if not _can_view_post_comments(request.user, post):
        return JsonResponse({"detail": "Not allowed."}, status=403)

    if (
        post.visibility == Post.Visibility.FRIENDS
        and request.user.is_authenticated
        and request.user.id != post.author_id
        and not _is_friend(request.user, post.author)
        and comment.author_id != request.user.id
    ):
        return JsonResponse({"detail": "Not allowed."}, status=403)

    if request.method == "POST":
        if post.deleted:
            return JsonResponse({"detail": "Deleted entries cannot be liked."}, status=400)

        like, created = Like.objects.get_or_create(author=request.user, comment=comment)
        return JsonResponse(_like_obj(like, request), status=201 if created else 200)

    if request.method == "GET":
        base_path = reverse(
            "posts:api-comment-likes",
            kwargs={
                "author_id": post.author_id,
                "post_id": post.id,
                "comment_id": comment.id,
            },
        )
        payload = _paginated_collection(
            request=request,
            base_path=base_path,
            collection_type="likes",
            queryset=comment.likes.select_related("author", "comment", "comment__post"),
            serializer=_like_obj,
        )
        return JsonResponse(payload, status=200)

    return HttpResponseNotAllowed(["GET", "POST"])


@login_required
def author_liked_api(request, author_id):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    author = get_object_or_404(Author, id=author_id)

    if request.user.id != author.id:
        return JsonResponse({"detail": "Only owners can view liked things."}, status=403)

    likes_qs = Like.objects.filter(author=author).select_related(
        "author", "post", "comment", "comment__post"
    )
    page, size = _pagination_params(request)
    paginator = Paginator(likes_qs, size)

    try:
        page_obj = paginator.page(page)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages) if paginator.num_pages else []

    items = []
    if page_obj:
        for like in page_obj.object_list:
            if like.post_id and _can_view_post(request.user, like.post):
                items.append(_like_obj(like, request))
            if like.comment_id and _can_view_post(request.user, like.comment.post):
                items.append(_like_obj(like, request))

    liked_path = reverse("posts:api-author-liked", kwargs={"author_id": author.id})
    query = urlencode({"page": page_obj.number if page_obj else 1, "size": size})

    payload = {
        "type": "likes",
        "id": request.build_absolute_uri(f"{liked_path}?{query}"),
        "page": page_obj.number if page_obj else 1,
        "size": size,
        "count": len(items),
        "src": items,
    }
    return JsonResponse(payload, status=200)


def like_detail_api(request, like_id):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    like = get_object_or_404(
        Like.objects.select_related("author", "post", "comment", "comment__post"),
        id=like_id,
    )
    return JsonResponse(_like_obj(like, request), status=200)


def _auth_for_node(node_url):
    if not node_url:
        return None
    creds = getattr(settings, "REMOTE_NODE_CREDENTIALS", {}) or {}
    info = creds.get(node_url.rstrip("/"))
    if info and info.get("username") and info.get("password"):
        return (info["username"], info["password"])
    return None


def _remote_author_obj_from_post(post: Post):
    author_url = (post.remote_author_url or "").strip()
    host = (post.remote_author_host or "").strip()
    display_name = (post.remote_author_name or "Remote Author").strip()
    github = ""
    profile_image = ""

    if author_url:
        try:
            parsed = urlparse(author_url)
            node_url = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else host
            fetch_url = author_url

            # Remote posts may provide HTML profile URL; convert to API author URL for JSON fields.
            if "/authors/api/authors/" not in fetch_url and "/authors/" in fetch_url:
                fetch_url = fetch_url.replace("/authors/", "/authors/api/authors/")

            resp = requests.get(
                fetch_url,
                auth=_auth_for_node(node_url),
                timeout=3,
                headers={"Accept": "application/json"},
            )
            if resp.status_code == 200 and isinstance(resp.json(), dict):
                data = resp.json()
                display_name = (data.get("displayName") or data.get("username") or display_name).strip()
                github = (data.get("github") or "").strip()
                profile_image = (data.get("profileImage") or "").strip()
                if profile_image.startswith("/") and node_url:
                    profile_image = f"{node_url}{profile_image}"
        except Exception:
            pass

    return {
        "type": "author",
        "id": author_url,
        "host": host,
        "displayName": display_name,
        "url": author_url,
        "github": github,
        "profileImage": profile_image,
    }


@login_required
def stream_api(request):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    # Pull latest remote public posts into local cache for API consumers.
    try:
        from .views import _fetch_remote_public_posts
        _fetch_remote_public_posts()
    except Exception:
        pass

    following_ids = Follower.objects.filter(
        follower=request.user,
        status="accepted",
    ).values_list("following_id", flat=True)

    followed_remote_author_urls = set(
        Author.objects.filter(id__in=following_ids, is_remote=True)
        .exclude(remote_id__isnull=True)
        .exclude(remote_id="")
        .values_list("remote_id", flat=True)
    )

    posts = (
        Post.objects.filter(deleted=False)
        .filter(
            Q(is_remote=False, author=request.user)
            | Q(is_remote=False, visibility=Post.Visibility.PUBLIC)
            | Q(
                is_remote=False,
                author_id__in=following_ids,
                visibility__in=[Post.Visibility.FRIENDS, Post.Visibility.UNLISTED],
            )
            | Q(is_remote=True, visibility=Post.Visibility.PUBLIC)
            | Q(
                is_remote=True,
                remote_author_url__in=followed_remote_author_urls,
                visibility__in=[Post.Visibility.FRIENDS, Post.Visibility.UNLISTED],
            )
        )
        .select_related("author")
        .order_by("-created")
    )

    def serialize_post(post, req):
        # Default fields
        content_type = post.content_type
        content = post.content

        # Local image → convert to base64
        if not post.is_remote and post.image:
            try:
                mime, _ = mimetypes.guess_type(post.image.name)
                mime = mime or "image/png"
                with post.image.open("rb") as f:
                    b64 = base64.b64encode(f.read()).decode("utf-8")

                content_type = f"{mime};base64"
                content = b64
            except Exception:
                pass

        # Remote image → leave as-is (remote nodes already encoded it)
        image_url = (
            post.remote_image
            if post.is_remote
            else (req.build_absolute_uri(post.image.url) if post.image else "")
        )

        return {
            "type": "entry",
            "id": (
                (post.remote_id or "")
                if post.is_remote
                else req.build_absolute_uri(
                    reverse(
                        "posts:api-post-detail",
                        kwargs={"author_id": post.author_id, "post_id": post.id},
                    )
                )
            ),
            "title": post.title,
            "contentType": content_type,
            "content": content,
            "image": image_url,
            "author": (
                _remote_author_obj_from_post(post)
                if post.is_remote
                else _author_obj(post.author, req)
            ),
            "visibility": post.visibility,
            "published": (post.published or post.created).isoformat(),
            "updated": post.updated.isoformat(),
        }

    base_path = reverse("posts:api-stream")
    payload = _paginated_collection(
        request=request,
        base_path=base_path,
        collection_type="entries",
        queryset=posts,
        serializer=serialize_post,
    )
    return JsonResponse(payload, status=200)


def _remote_author_obj_from_payload(author_payload):
    if not isinstance(author_payload, dict):
        return {
            "id": "",
            "displayName": "Remote Author",
            "host": "",
        }
    identifier = (author_payload.get("id") or author_payload.get("url") or "").strip()
    return {
        "id": identifier,
        "displayName": author_payload.get("displayName") or author_payload.get("username") or "Remote Author",
        "host": author_payload.get("host", ""),
    }


def _public_post_or_404(author_id, post_id):
    return get_object_or_404(
        Post.objects.select_related("author"),
        id=post_id,
        author_id=author_id,
        deleted=False,
        visibility=Post.Visibility.PUBLIC,
        is_remote=False,
    )


def _comment_obj_public(comment: Comment, request):
    if comment.author:
        author_obj = _author_obj(comment.author, request)
    else:
        author_obj = {
            "type": "author",
            "id": comment.remote_author_url or "",
            "host": comment.remote_author_host or "",
            "displayName": comment.remote_author_name or "Remote Author",
            "url": comment.remote_author_url or "",
            "github": "",
            "profileImage": "",
        }

    likes_path = reverse(
        "posts:api-public-comment-likes",
        kwargs={
            "author_id": comment.post.author_id,
            "post_id": comment.post_id,
            "comment_id": comment.id,
        },
    )

    return {
        "type": "comment",
        "author": author_obj,
        "comment": comment.comment,
        "contentType": comment.content_type,
        "published": comment.published.isoformat(),
        "id": str(comment.remote_id or comment.id),
        "likes": {
            "type": "likes",
            "count": comment.likes.count(),
            "id": request.build_absolute_uri(likes_path),
        },
    }


def _like_obj_public(like: Like, request):
    if like.author:
        author_obj = _author_obj(like.author, request)
        author_name = getattr(like.author, "displayName", "") or getattr(like.author, "username", "Unknown")
    else:
        author_obj = {
            "type": "author",
            "id": like.remote_author_url or "",
            "host": like.remote_author_host or "",
            "displayName": like.remote_author_name or "Remote Author",
            "url": like.remote_author_url or "",
            "github": "",
            "profileImage": "",
        }
        author_name = like.remote_author_name or "Remote Author"

    if like.post_id:
        summary = f"{author_name} likes your post"
        object_id = request.build_absolute_uri(
            reverse(
                "posts:api-post-detail",
                kwargs={"author_id": like.post.author_id, "post_id": like.post_id},
            )
        )
    else:
        summary = f"{author_name} likes your comment"
        object_id = ""

    return {
        "type": "like",
        "summary": summary,
        "author": author_obj,
        "object": object_id,
        "id": str(like.remote_id or like.id),
        "published": like.created.isoformat(),
    }

def public_posts_api(request):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    posts = (
        Post.objects.filter(
            deleted=False,
            is_remote=False,
            visibility=Post.Visibility.PUBLIC,
        )
        .select_related("author")
        .order_by("-created")
    )

    items = []
    for post in posts:
        author_obj = _author_obj(post.author, request)

        comments_url = request.build_absolute_uri(
            reverse(
                "posts:api-public-post-comments",
                kwargs={"author_id": post.author_id, "post_id": post.id},
            )
        )
        likes_url = request.build_absolute_uri(
            reverse(
                "posts:api-public-post-likes",
                kwargs={"author_id": post.author_id, "post_id": post.id},
            )
        )

        content_type = post.content_type
        content = post.content
        if post.is_remote:
            image_url = post.remote_image or ""
        else:
            image_url = request.build_absolute_uri(post.image.url) if post.image else ""

        if post.image and content_type.startswith("image/"):
            try:
                with post.image.open("rb") as f:
                    b64 = base64.b64encode(f.read()).decode("utf-8")
                content = b64
            except Exception:
                # Fallback: leave content empty if something goes wrong
                content = ""

        items.append({
            "type": "post",
            "id": request.build_absolute_uri(
                reverse(
                    "posts:api-post-detail",
                    kwargs={"author_id": post.author_id, "post_id": post.id},
                )
            ),
            "title": post.title,
            "contentType": content_type,
            "content": content,
            "image": image_url,
            "author": author_obj,
            "visibility": post.visibility,
            "published": (post.published or post.created).isoformat(),
            "updated": post.updated.isoformat(),
            "unlisted": post.visibility == Post.Visibility.UNLISTED,
            "comments": {
                "type": "comments",
                "id": comments_url,
                "count": post.comments.count(),
            },
            "likes": {
                "type": "likes",
                "id": likes_url,
                "count": post.likes.count(),
            },
        })

    return JsonResponse({"type": "posts", "items": items}, status=200)


@csrf_exempt
def public_post_comments_api(request, author_id, post_id):
    post = _public_post_or_404(author_id, post_id)

    if request.method == "GET":
        comments = post.comments.select_related("author").order_by("-published")
        return JsonResponse(
            {
                "type": "comments",
                "count": comments.count(),
                "src": [_comment_obj_public(comment, request) for comment in comments],
            },
            status=200,
        )

    if request.method == "POST":
        if request.content_type == "application/json":
            try:
                payload = json.loads(request.body.decode("utf-8"))
            except Exception:
                return JsonResponse({"detail": "Invalid JSON."}, status=400)

            text = (payload.get("comment") or "").strip()
            content_type = payload.get("contentType", Comment.ContentType.PLAIN)
            author_payload = _remote_author_obj_from_payload(payload.get("author"))

            if not text:
                return JsonResponse({"detail": "comment is required."}, status=400)

            if content_type not in [Comment.ContentType.PLAIN, Comment.ContentType.MARKDOWN]:
                content_type = Comment.ContentType.PLAIN

            remote_id = payload.get("id") or None

            if remote_id:
                comment, created = Comment.objects.get_or_create(
                    remote_id=remote_id,
                    defaults={
                        "post": post,
                        "author": None,
                        "comment": text,
                        "content_type": content_type,
                        "is_remote": True,
                        "remote_author_url": author_payload["id"],
                        "remote_author_name": author_payload["displayName"],
                        "remote_author_host": author_payload["host"],
                    },
                )
                return JsonResponse(_comment_obj_public(comment, request), status=201 if created else 200)

            comment = Comment.objects.create(
                post=post,
                author=None,
                comment=text,
                content_type=content_type,
                is_remote=True,
                remote_author_url=author_payload["id"],
                remote_author_name=author_payload["displayName"],
                remote_author_host=author_payload["host"],
            )
            return JsonResponse(_comment_obj_public(comment, request), status=201)

        text = (request.POST.get("comment") or "").strip()
        content_type = request.POST.get("contentType", Comment.ContentType.PLAIN)

        if not request.user.is_authenticated:
            return JsonResponse({"detail": "Login required."}, status=403)

        if not text:
            return JsonResponse({"detail": "comment is required."}, status=400)

        if content_type not in [Comment.ContentType.PLAIN, Comment.ContentType.MARKDOWN]:
            content_type = Comment.ContentType.PLAIN

        comment = Comment.objects.create(
            post=post,
            author=request.user,
            comment=text,
            content_type=content_type,
            is_remote=False,
        )
        return JsonResponse(_comment_obj_public(comment, request), status=201)

    return HttpResponseNotAllowed(["GET", "POST"])


@csrf_exempt
def public_post_likes_api(request, author_id, post_id):
    post = _public_post_or_404(author_id, post_id)

    if request.method == "GET":
        likes = post.likes.select_related("author", "post").order_by("-created")
        return JsonResponse(
            {
                "type": "likes",
                "count": likes.count(),
                "src": [_like_obj_public(like, request) for like in likes],
            },
            status=200,
        )

    if request.method == "POST":
        if request.content_type == "application/json":
            try:
                payload = json.loads(request.body.decode("utf-8"))
            except Exception:
                return JsonResponse({"detail": "Invalid JSON."}, status=400)

            author_payload = _remote_author_obj_from_payload(payload.get("author"))
            remote_id = payload.get("id") or payload.get("remote_id") or None
            normalized_author = _normalized_author_url(author_payload["id"])

            if remote_id:
                like, created = Like.objects.get_or_create(
                    remote_id=remote_id,
                    defaults={
                        "is_remote": True,
                        "author": None,
                        "post": post,
                        "remote_author_url": normalized_author or author_payload["id"],
                        "remote_author_name": author_payload["displayName"],
                        "remote_author_host": author_payload["host"],
                    },
                )
                return JsonResponse(_like_obj_public(like, request), status=201 if created else 200)

            like = Like.objects.create(
                is_remote=True,
                author=None,
                post=post,
                remote_author_url=normalized_author or author_payload["id"],
                remote_author_name=author_payload["displayName"],
                remote_author_host=author_payload["host"],
            )
            return JsonResponse(_like_obj_public(like, request), status=201)

        if not request.user.is_authenticated:
            return JsonResponse({"detail": "Login required."}, status=403)

        like, created = Like.objects.get_or_create(author=request.user, post=post)
        return JsonResponse(_like_obj_public(like, request), status=201 if created else 200)

    if request.method == "DELETE":
        if request.content_type == "application/json":
            try:
                payload = json.loads(request.body.decode("utf-8"))
            except Exception:
                return JsonResponse({"detail": "Invalid JSON."}, status=400)

            author_payload = _remote_author_obj_from_payload(payload.get("author"))
            remote_id = payload.get("id") or payload.get("remote_id") or None
            normalized_author = _normalized_author_url(author_payload["id"])

            qs = Like.objects.filter(post=post, is_remote=True)
            if remote_id:
                qs = qs.filter(remote_id=remote_id)

            deleted, _ = qs.delete()
            if deleted == 0 and normalized_author:
                # Fallback for legacy rows saved with a different URL shape.
                fallback_qs = Like.objects.filter(post=post, is_remote=True)
                fallback_ids = [
                    like.id for like in fallback_qs
                    if _normalized_author_url(like.remote_author_url) == normalized_author
                ]
                if fallback_ids:
                    deleted, _ = Like.objects.filter(id__in=fallback_ids).delete()
            return JsonResponse({"deleted": deleted}, status=200)

        if not request.user.is_authenticated:
            return JsonResponse({"detail": "Login required."}, status=403)

        deleted, _ = Like.objects.filter(author=request.user, post=post).delete()
        return JsonResponse({"deleted": deleted}, status=200)

    return HttpResponseNotAllowed(["GET", "POST", "DELETE"])


@csrf_exempt
def public_comment_likes_api(request, author_id, post_id, comment_id):
    post = _public_post_or_404(author_id, post_id)
    comment = get_object_or_404(Comment, id=comment_id, post=post)

    if request.method == "GET":
        likes = comment.likes.select_related("author", "comment", "comment__post").order_by("-created")
        return JsonResponse(
            {
                "type": "likes",
                "count": likes.count(),
                "src": [_like_obj_public(like, request) for like in likes],
            },
            status=200,
        )

    if request.method == "POST":
        if request.content_type == "application/json":
            try:
                payload = json.loads(request.body.decode("utf-8"))
            except Exception:
                return JsonResponse({"detail": "Invalid JSON."}, status=400)

            author_payload = _remote_author_obj_from_payload(payload.get("author"))
            remote_id = payload.get("id") or payload.get("remote_id") or None
            normalized_author = _normalized_author_url(author_payload["id"])

            if remote_id:
                like, created = Like.objects.get_or_create(
                    remote_id=remote_id,
                    defaults={
                        "is_remote": True,
                        "author": None,
                        "comment": comment,
                        "remote_author_url": normalized_author or author_payload["id"],
                        "remote_author_name": author_payload["displayName"],
                        "remote_author_host": author_payload["host"],
                    },
                )
                return JsonResponse(_like_obj_public(like, request), status=201 if created else 200)

            like = Like.objects.create(
                is_remote=True,
                author=None,
                comment=comment,
                remote_author_url=normalized_author or author_payload["id"],
                remote_author_name=author_payload["displayName"],
                remote_author_host=author_payload["host"],
            )
            return JsonResponse(_like_obj_public(like, request), status=201)

        if not request.user.is_authenticated:
            return JsonResponse({"detail": "Login required."}, status=403)

        like, created = Like.objects.get_or_create(author=request.user, comment=comment)
        return JsonResponse(_like_obj_public(like, request), status=201 if created else 200)

    if request.method == "DELETE":
        if request.content_type == "application/json":
            try:
                payload = json.loads(request.body.decode("utf-8"))
            except Exception:
                return JsonResponse({"detail": "Invalid JSON."}, status=400)

            author_payload = _remote_author_obj_from_payload(payload.get("author"))
            remote_id = payload.get("id") or payload.get("remote_id") or None
            normalized_author = _normalized_author_url(author_payload["id"])

            qs = Like.objects.filter(comment=comment, is_remote=True)
            if remote_id:
                qs = qs.filter(remote_id=remote_id)

            deleted, _ = qs.delete()
            if deleted == 0 and normalized_author:
                # Fallback for legacy rows saved with a different URL shape.
                fallback_qs = Like.objects.filter(comment=comment, is_remote=True)
                fallback_ids = [
                    like.id for like in fallback_qs
                    if _normalized_author_url(like.remote_author_url) == normalized_author
                ]
                if fallback_ids:
                    deleted, _ = Like.objects.filter(id__in=fallback_ids).delete()
            return JsonResponse({"deleted": deleted}, status=200)

        if not request.user.is_authenticated:
            return JsonResponse({"detail": "Login required."}, status=403)

        deleted, _ = Like.objects.filter(author=request.user, comment=comment).delete()
        return JsonResponse({"deleted": deleted}, status=200)

    return HttpResponseNotAllowed(["GET", "POST", "DELETE"])