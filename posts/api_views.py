from urllib.parse import urlencode
import json

from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.core.paginator import EmptyPage, Paginator
from django.db.models import Q, F
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt

from authors.models import Author, Follower
from node.registry import get_node_auth, get_configured_nodes
from .models import Comment, Like, Post

"""
Command: Create a federated stream_api view in Django that combines local and remote posts, 
supports pagination, and returns a response with type, id, page, size, count, and src.
Response: stream_api is based on the response generated
Citation: chatGpt 5.2, OpenAI, 2026-03-23, https://chatgpt.com/c/69c1857e-4d80-832c-9d52-009651074d80
"""

def _serialize_entry(post, request):
    """Shared serializer for entry objects to match the spec."""
    base_url = request.build_absolute_uri("/").rstrip("/")

    if post.is_remote:
        entry_id = post.remote_id or ""
        author_obj = _remote_author_obj_from_post(post)
        image_url = post.remote_image or ""
        web_url = post.remote_id or ""
        comments_web = ""
        likes_web = ""
        comments_api_url = ""
        likes_api_url = ""
        comments_count = 0
        likes_count = 0
        comments_src = []
        likes_src = []
    else:
        entry_id = request.build_absolute_uri(
            reverse(
                "posts:api-entry-detail",
                kwargs={"author_id": post.author_id, "post_id": post.id},
            )
        )
        author_obj = _author_obj(post.author, request)
        image_url = ""
        if post.image:
            try:
                image_url = request.build_absolute_uri(post.image.url)
            except (AttributeError, ValueError):
                pass
        web_url = f"{base_url}/authors/{post.author_id}/entries/{post.id}"
        comments_web = f"{web_url}/comments"
        likes_web = f"{web_url}/likes"

        comments_api_url = request.build_absolute_uri(
            reverse(
                "posts:api-entry-comments",
                kwargs={"author_id": post.author_id, "post_id": post.id},
            )
        )
        likes_api_url = request.build_absolute_uri(
            reverse(
                "posts:api-entry-likes",
                kwargs={"author_id": post.author_id, "post_id": post.id},
            )
        )

        # First page of comments (size 5)
        comments_qs = post.comments.select_related("author").order_by("-published")
        comments_count = comments_qs.count()
        comments_src = [_comment_obj(c, request) for c in comments_qs[:5]]

        # First page of likes (size 50)
        likes_qs = post.likes.select_related("author", "post").order_by("-created")
        likes_count = likes_qs.count()
        likes_src = [_like_obj(l, request) for l in likes_qs[:50]]

    return {
        "type": "entry",
        "id": entry_id,
        "web": web_url,
        "title": post.title,
        "description": getattr(post, "description", "") or "",
        "contentType": post.content_type,
        "content": post.content,
        "image": image_url,
        "author": author_obj,
        "visibility": post.visibility,
        "published": (post.published or post.created).isoformat(),
        "updated": post.updated.isoformat(),
        "comments": {
            "type": "comments",
            "id": comments_api_url,
            "web": comments_web,
            "page_number": 1,
            "size": 5,
            "count": comments_count,
            "src": comments_src,
        },
        "likes": {
            "type": "likes",
            "id": likes_api_url,
            "web": likes_web,
            "page_number": 1,
            "size": 50,
            "count": likes_count,
            "src": likes_src,
        },
    }
    
def _normalized_author_url(value):
    raw = (value or "").strip().rstrip("/")
    if not raw:
        return ""
    return raw.replace("/authors/api/authors/", "/authors/")


def _author_obj(author, request):
    if not author:
        base_url = request.build_absolute_uri("/").rstrip("/")
        return {
            "type": "author",
            "id": "",
            "host": f"{base_url}/api/",
            "displayName": "Unknown Author",
            "github": "",
            "profileImage": "",
            "web": "",
        }

    base_url = request.build_absolute_uri("/").rstrip("/")
    author_url = f"{base_url}/api/authors/{author.id}"

    profile_image = ""
    if getattr(author, "profileImage", None):
        try:
            profile_image = request.build_absolute_uri(author.profileImage.url)
        except Exception:
            profile_image = ""

    return {
        "type": "author",
        "id": author_url,
        "host": f"{base_url}/api/",
        "displayName": getattr(author, "displayName", "") or getattr(author, "username", "Unknown"),
        "github": getattr(author, "github", "") or "",
        "profileImage": profile_image,
        "web": f"{base_url}/authors/{author.id}",
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

    if post.visibility == Post.Visibility.PUBLIC:
        return True

    if post.visibility == Post.Visibility.UNLISTED:
        return True

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
    
def authors_list_api(request):
    """GET /api/authors/ - List all authors (paginated)"""
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    # Author model does not have `created`; use stable user timestamps/order.
    authors_qs = Author.objects.filter(is_remote=False).order_by("-date_joined", "username")
    base_path = reverse("posts:api-authors-list")
    
    payload = _paginated_collection(
        request=request,
        base_path=base_path,
        collection_type="authors",
        queryset=authors_qs,
        serializer=_author_obj,
    )
    return JsonResponse(payload, status=200)


def author_detail_api(request, author_id):
    """GET /api/authors/{author_id}/ - Get single author"""
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    author = get_object_or_404(Author, id=author_id, is_remote=False)
    return JsonResponse(_author_obj(author, request), status=200)


def post_detail_api(request, author_id, post_id):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    post = get_object_or_404(Post, id=post_id, author_id=author_id)
    if not _can_view_post(request.user, post):
        return JsonResponse({"detail": "Not allowed."}, status=403)

    post_path = reverse(
        "posts:api-entry-detail",
        kwargs={"author_id": post.author_id, "post_id": post.id},
    )
    comments_path = reverse(
        "posts:api-entry-comments",
        kwargs={"author_id": post.author_id, "post_id": post.id},
    )
    likes_path = reverse(
        "posts:api-entry-likes",
        kwargs={"author_id": post.author_id, "post_id": post.id},
    )

    image_url = ""
    if post.image:
        try:
            image_url = request.build_absolute_uri(post.image.url)
        except (AttributeError, ValueError):
            pass

    base_url = request.build_absolute_uri("/").rstrip("/")
    web_url = f"{base_url}/authors/{post.author_id}/entries/{post.id}"
    comments_web = f"{web_url}/comments"
    likes_web = f"{web_url}/likes"

    # First page of comments (size 5)
    comments_qs = post.comments.select_related("author").order_by("-published")
    comments_count = comments_qs.count()
    comments_src = [_comment_obj(c, request) for c in comments_qs[:5]]

    # First page of likes (size 50)
    likes_qs = post.likes.select_related("author", "post").order_by("-created")
    likes_count = likes_qs.count()
    likes_src = [_like_obj(l, request) for l in likes_qs[:50]]

    payload = {
        "type": "entry",
        "id": request.build_absolute_uri(post_path),
        "web": web_url,
        "title": post.title,
        "description": post.description,
        "contentType": post.content_type,
        "content": post.content,
        "author": _author_obj(post.author, request),
        "visibility": post.visibility,
        "published": (post.published or post.created).isoformat(),
        "updated": post.updated.isoformat(),
        "image": image_url,
        "comments": {
            "type": "comments",
            "id": request.build_absolute_uri(comments_path),
            "web": comments_web,
            "page_number": 1,
            "size": 5,
            "count": comments_count,
            "src": comments_src,
        },
        "likes": {
            "type": "likes",
            "id": request.build_absolute_uri(likes_path),
            "web": likes_web,
            "page_number": 1,
            "size": 50,
            "count": likes_count,
            "src": likes_src,
        },
    }
    return JsonResponse(payload, status=200)

@csrf_exempt
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
            "posts:api-entry-comments",
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


def comment_detail_api(request, author_id, comment_id):
    """GET /api/authors/{author_id}/commented/{comment_id}/ - Get single comment"""
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    comment = get_object_or_404(Comment, id=comment_id, post__author_id=author_id)
    if not _can_view_post_comments(request.user, comment.post):
        return JsonResponse({"detail": "Not allowed."}, status=403)

    return JsonResponse(_comment_obj(comment, request), status=200)


@csrf_exempt
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
            "posts:api-entry-likes",
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


@csrf_exempt
def comment_likes_api(request, author_id, comment_id):
    comment = get_object_or_404(Comment, id=comment_id, post__author_id=author_id)
    post = comment.post

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


def like_detail_api(request, author_id, like_id):
    """GET /api/authors/{author_id}/liked/{like_id}/ - Get single like
    
    author_id is the LIKE AUTHOR (who made the like), not the post author
    """
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    like = get_object_or_404(
        Like.objects.select_related("author", "post", "comment", "comment__post"),
        id=like_id,
        author_id=author_id,  # ← Fetch by LIKE AUTHOR
    )
    return JsonResponse(_like_obj(like, request), status=200)


def _auth_for_node(node_url):
    return get_node_auth(node_url)


def _remote_author_obj_from_post(post: Post):
    author_url = (post.remote_author_url or "").strip()
    host = (post.remote_author_host or "").strip()
    display_name = (post.remote_author_name or "Remote Author").strip()

    # Do not issue network calls while serializing the stream; use cached author fields.
    profile_image = (getattr(post, "remote_author_image", "") or "").strip()
    if profile_image.startswith("/") and host:
        profile_image = f"{host.rstrip('/')}{profile_image}"

    return {
        "type": "author",
        "id": author_url,
        "host": host,
        "displayName": display_name,
        "github": "",
        "profileImage": profile_image,
        "web": author_url,
    }
    
"""
Command:Create a modular Django API for a federated social network using helper functions for serialization, 
permission checks, and pagination. Include support for remote authors and ensure all collection responses 
follow a consistent format with type, id, page, size, count, and src
Response: All funtions/methods used in api_views are loosely referenced from the response given
Citation: chatGpt 5.2, OpenAI, 2026-03-23, https://chatgpt.com/c/69c18637-c8a4-8327-b809-aa8f0e34624d
"""
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
    allowed_remote_nodes = {node.rstrip("/") for node in get_configured_nodes(exclude_local=True)}

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
            | Q(is_remote=True, visibility=Post.Visibility.PUBLIC, node_url__in=allowed_remote_nodes)
            | Q(
                is_remote=True,
                remote_author_url__in=followed_remote_author_urls,
                visibility__in=[Post.Visibility.FRIENDS, Post.Visibility.UNLISTED],
                node_url__in=allowed_remote_nodes,
            )
        )
        .select_related("author")
        .order_by(F("published").desc(nulls_last=True), "-created")
    )

    base_path = reverse("posts:api-stream")
    payload = _paginated_collection(
        request=request,
        base_path=base_path,
        collection_type="entries",
        queryset=posts,
        serializer=_serialize_entry,
    )
    return JsonResponse(payload, status=200)

def author_entries_api(request, author_id):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    # Get all non-deleted posts for this author
    posts = (
        Post.objects.filter(
            author_id=author_id,
            deleted=False
        )
        .select_related("author")
        .order_by("-published", "-created")
    )

    # Base path for pagination
    base_path = reverse(
        "posts:api-author-entries",
        kwargs={"author_id": author_id}
    )

    # Use your existing pagination helper
    payload = _paginated_collection(
        request=request,
        base_path=base_path,
        collection_type="entries",
        queryset=posts,
        serializer=_serialize_entry,
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


def _comment_obj(comment: Comment, request):
    """Comment belongs to comment author, not post author"""
    post_path = reverse(
        "posts:api-entry-detail",
        kwargs={"author_id": comment.post.author_id, "post_id": comment.post_id},
    )
    
    # Determine comment author ID
    if comment.author:
        comment_author_id = comment.author_id
    else:
        # For remote comments, extract author ID from remote_author_url
        # e.g., "http://node/api/authors/111" → "111"
        comment_author_id = comment.remote_author_url.split('/authors/')[-1].rstrip('/')
    
    comment_path = reverse(
        "posts:api-comment-detail",
        kwargs={
            "author_id": comment_author_id,
            "comment_id": comment.id,
        },
    )
    likes_path = reverse(
        "posts:api-comment-likes",
        kwargs={
            "author_id": comment_author_id,
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
            "github": "",
            "profileImage": "",
            "web": comment.remote_author_url or "",
        }

    return {
        "type": "comment",
        "author": author_obj,
        "comment": comment.comment,
        "contentType": comment.content_type,
        "published": comment.published.isoformat(),
        "id": request.build_absolute_uri(comment_path),
        "object": request.build_absolute_uri(post_path),
        "likes": {
            "type": "likes",
            "id": request.build_absolute_uri(likes_path),
            "count": comment.likes.count(),
        },
    }


def _like_obj(like: Like, request):
    """Like belongs to like author"""
    if like.author:
        author_name = getattr(like.author, "displayName", "") or getattr(like.author, "username", "Unknown")
        author_obj = _author_obj(like.author, request)
        like_author_id = like.author_id
    else:
        author_name = like.remote_author_name or "Remote Author"
        author_obj = {
            "type": "author",
            "id": like.remote_author_url or "",
            "host": like.remote_author_host or "",
            "displayName": like.remote_author_name or "Remote Author",
            "github": "",
            "profileImage": "",
            "web": like.remote_author_url or "",
        }
        # For remote likes, extract author ID from remote_author_url
        like_author_id = like.remote_author_url.split('/authors/')[-1].rstrip('/')

    if like.post_id:
        object_path = reverse(
            "posts:api-entry-detail",
            kwargs={"author_id": like.post.author_id, "post_id": like.post_id},
        )
        summary = f"{author_name} likes your post"
    else:
        # For comment likes, determine comment author
        if like.comment.author:
            comment_author_id = like.comment.author_id
        else:
            comment_author_id = like.comment.remote_author_url.split('/authors/')[-1].rstrip('/')
        
        object_path = reverse(
            "posts:api-comment-detail",
            kwargs={
                "author_id": comment_author_id,
                "comment_id": like.comment_id,
            },
        )
        summary = f"{author_name} likes your comment"

    like_path = reverse(
        "posts:api-like-detail", 
        kwargs={
            "author_id": like_author_id,
            "like_id": like.id
        }
    )

    return {
        "type": "like",
        "summary": summary,
        "author": author_obj,
        "object": request.build_absolute_uri(object_path),
        "id": request.build_absolute_uri(like_path),
        "published": like.created.isoformat(),
    }

_comment_obj_public = _comment_obj
_like_obj_public = _like_obj


"""
Command: Generate a Django REST-style endpoint for public posts that includes pagination, 
and returns a response with keys: type, count, and src instead of items.
Response: public_posts_api is based on the response generated
Citation: chatGpt 5.2, OpenAI, 2026-03-23, https://chatgpt.com/share/69c18545-b8c8-8006-a673-67a5e0b634ae
"""
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

    items = [_serialize_entry(post, request) for post in posts]

    return JsonResponse({
        "type": "entries",
        "count": len(items),
        "src": items
    }, status=200)
    
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