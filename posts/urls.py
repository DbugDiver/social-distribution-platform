from django.urls import path
from . import views
from . import api_views

app_name = "posts"

urlpatterns = [
    # =========================================================
    # HTML / local app routes
    # =========================================================
    path("", views.stream, name="stream"),
    path("friends/", views.followers_feed, name="friends-feed"),
    path("new/", views.create, name="create"),
    path("<uuid:post_id>/comment/", views.add_comment, name="add-comment"),
    path("<uuid:post_id>/like/", views.like_post, name="like-post"),
    path(
        "<uuid:post_id>/comments/<uuid:comment_id>/like/",
        views.like_comment,
        name="like-comment",
    ),
    path(
        "<uuid:post_id>/remote-comment-like/",
        views.like_remote_comment,
        name="like-remote-comment",
    ),
    path("<uuid:post_id>/", views.detail, name="detail"),
    path("<uuid:post_id>/edit/", views.edit, name="edit"),
    path("<uuid:author_id>/posts/", views.author_posts, name="author-posts"),
    path("<uuid:post_id>/delete/", views.delete, name="delete"),

    # =========================================================
    # API routes - Federation Spec Compliant
    # =========================================================

    # ===== Authors =====
    path("api/authors/", api_views.authors_list_api, name="api-authors-list"),
    path("api/authors/<uuid:author_id>/", api_views.author_detail_api, name="api-author-detail"),

    # ===== Entries (Posts) =====
    path("api/authors/<uuid:author_id>/entries/", api_views.author_entries_api, name="api-author-entries"),
    path("api/authors/<uuid:author_id>/entries/<uuid:post_id>/", api_views.post_detail_api, name="api-entry-detail"),

    # ===== Comments =====
    path("api/authors/<uuid:author_id>/entries/<uuid:post_id>/comments/", api_views.post_comments_api, name="api-entry-comments"),
    path("api/authors/<uuid:author_id>/entries/<uuid:post_id>/comments/<uuid:comment_id>/", api_views.comment_detail_api, name="api-comment-detail"),

    # ===== Likes on Entries =====
    path("api/authors/<uuid:author_id>/entries/<uuid:post_id>/likes/", api_views.post_likes_api, name="api-entry-likes"),

    # ===== Likes on Comments =====
    path("api/authors/<uuid:author_id>/entries/<uuid:post_id>/comments/<uuid:comment_id>/likes/", api_views.comment_likes_api, name="api-comment-likes"),

    # ===== Author Liked Collection =====
    path("api/authors/<uuid:author_id>/liked/", api_views.author_liked_api, name="api-author-liked"),
    path("api/authors/<uuid:author_id>/liked/<uuid:like_id>/", api_views.like_detail_api, name="api-like-detail"),

    # ===== Public Stream =====
    path("api/stream/", api_views.stream_api, name="api-stream"),
    path("api/entries/public/", api_views.public_posts_api, name="api-public-entries"),

    # ===== Public Endpoints =====
    path("api/public/authors/<uuid:author_id>/entries/<uuid:post_id>/comments/", api_views.public_post_comments_api, name="api-public-entry-comments"),
    path("api/public/authors/<uuid:author_id>/entries/<uuid:post_id>/likes/", api_views.public_post_likes_api, name="api-public-entry-likes"),
    path("api/public/authors/<uuid:author_id>/entries/<uuid:post_id>/comments/<uuid:comment_id>/likes/", api_views.public_comment_likes_api, name="api-public-comment-likes"),
]