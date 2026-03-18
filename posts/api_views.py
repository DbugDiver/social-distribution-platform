from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.core.paginator import EmptyPage, Paginator
from django.db.models import Q
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse

from authors.models import Author, Follower
from .models import Comment, Like, Post

"""
Change citation (local project work):
- Added Part 1 JSON API endpoints for comments/likes/stream.
- Responses use FQID-style URLs and paginated collections.
- Includes local visibility enforcement (PUBLIC/FRIENDS/UNLISTED/DELETED).
"""


def _author_obj(author: Author, request):
    author_path = reverse("author-profile", kwargs={"pk": author.id})
    fqid = request.build_absolute_uri(author_path)
    return {
        "type": "author",
        "id": fqid,
        "host": author.host or request.build_absolute_uri("/"),
        "displayName": author.displayName or author.username,
        "url": fqid,
        "github": author.github or "",
        "profileImage": author.profileImage or "",
    }


def _is_friend(user: Author, other: Author):
    return (
        Follower.objects.filter(
            follower=user,
            following=other,
            status="accepted",
        ).exists()
        and Follower.objects.filter(
            follower=other,
            following=user,
            status="accepted",
        ).exists()
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
    # User Story 3: for FRIENDS posts, allow comment authors to see comments even if friendship changed later.
    if _can_view_post(user, post):
        return True
    if not user.is_authenticated:
        return False
    if post.visibility != Post.Visibility.FRIENDS:
        return False
    return post.comments.filter(author=user).exists()


def _visible_comments_queryset(user, post: Post):
    # User Story 3: friends can see all comments, non-friend commenters see only their own comment(s).
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
    return {
        "type": "comment",
        "author": _author_obj(comment.author, request),
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
    if like.post_id:
        object_path = reverse(
            "posts:api-post-detail",
            kwargs={"author_id": like.post.author_id, "post_id": like.post_id},
        )
        summary = (
            f"{like.author.displayName or like.author.username} likes your post"
        )
    else:
        object_path = reverse(
            "posts:api-comment-likes",
            kwargs={
                "author_id": like.comment.post.author_id,
                "post_id": like.comment.post_id,
                "comment_id": like.comment_id,
            },
        ).replace("/likes/", "/")
        summary = (
            f"{like.author.displayName or like.author.username} likes your comment"
        )

    like_path = reverse("posts:api-like-detail", kwargs={"like_id": like.id})
    return {
        "type": "like",
        "summary": summary,
        "author": _author_obj(like.author, request),
        "object": request.build_absolute_uri(object_path),
        "id": request.build_absolute_uri(like_path),
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
    payload = {
        "type": "entry",
        "id": request.build_absolute_uri(post_path),
        "title": post.title,
        "contentType": post.content_type,
        "content": post.content,
        "author": _author_obj(post.author, request),
        "visibility": post.visibility,
        "published": post.created.isoformat(),
        "updated": post.updated.isoformat(),
        "unlisted": post.visibility == Post.Visibility.UNLISTED,
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
    # User Story 3: comments may be visible to comment authors on FRIENDS posts.
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
        status_code = 201 if created else 200
        return JsonResponse(_like_obj(like, request), status=status_code)

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
    # User Story 3: comment authors can still see/like their own comment thread on FRIENDS posts.
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
        status_code = 201 if created else 200
        return JsonResponse(_like_obj(like, request), status=status_code)

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
    like = get_object_or_404(Like.objects.select_related("author", "post", "comment", "comment__post"), id=like_id)
    return JsonResponse(_like_obj(like, request), status=200)


@login_required
def stream_api(request):
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    following_ids = Follower.objects.filter(
        follower=request.user,
        status="accepted",
    ).values_list("following_id", flat=True)

    posts = (
        Post.objects.filter(deleted=False)
        .filter(
            Q(author=request.user)
            | Q(visibility=Post.Visibility.PUBLIC)
            | Q(
                author_id__in=following_ids,
                visibility__in=[Post.Visibility.FRIENDS, Post.Visibility.UNLISTED],
            )
        )
        .select_related("author")
        .order_by("-created")
    )

    base_path = reverse("posts:api-stream")
    payload = _paginated_collection(
        request=request,
        base_path=base_path,
        collection_type="entries",
        queryset=posts,
        serializer=lambda post, req: {
            "type": "entry",
            "id": req.build_absolute_uri(
                reverse(
                    "posts:api-post-detail",
                    kwargs={"author_id": post.author_id, "post_id": post.id},
                )
            ),
            "title": post.title,
            "contentType": post.content_type,
            "content": post.content,
            "author": _author_obj(post.author, req),
            "visibility": post.visibility,
            "published": post.created.isoformat(),
            "updated": post.updated.isoformat(),
        },
    )
    return JsonResponse(payload, status=200)


@api_view(["GET"])
def remote_posts_api(request):
    """Return JSON of remote posts for testing."""
    posts = Post.objects.filter(is_remote=True, deleted=False)
    data = [
        {
            "id": str(p.remote_id or p.id),
            "title": p.title,
            "content": p.content,
            "content_type": p.content_type,
            "visibility": p.visibility,
            "created": p.created.isoformat(),
            "author": {
                "id": str(p.author.remote_id or p.author.id),
                "username": p.author.username,
            },
            "comments": [
                {
                    "id": str(c.remote_id or c.id),
                    "comment": c.comment,
                    "author": {
                        "id": str(c.author.remote_id or c.author.id),
                        "username": c.author.username,
                    }
                } for c in p.comments.all()
            ]
        } for p in posts
    ]
    return Response(data)