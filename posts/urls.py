from django.urls import path
from . import views
from . import api_views

# Change citation (local project work):
# Added web and API routes for comments/likes and stream retrieval.

app_name = "posts"

urlpatterns = [
    # Changed section: HTML routes for post stream and interactions.
    path("", views.stream, name="stream"),
    path("friends/", views.followers_feed, name="friends-feed"),
    path("new/", views.create, name="create"),
    path("<uuid:post_id>/comment/", views.add_comment, name="add-comment"),
    path("<uuid:post_id>/like/", views.like_post, name="like-post"),
    path("<uuid:post_id>/comments/<uuid:comment_id>/like/", views.like_comment, name="like-comment"),
    path("<uuid:post_id>/", views.detail, name="detail"),
    path("<uuid:post_id>/edit/", views.edit, name="edit"),
    path("<uuid:post_id>/delete/", views.delete, name="delete"),

    # Changed section: API routes for entries/comments/likes (Part 1 local node scope).
    path("api/stream/", api_views.stream_api, name="api-stream"),
    path(
        "api/authors/<uuid:author_id>/posts/<uuid:post_id>/",
        api_views.post_detail_api,
        name="api-post-detail",
    ),
    path(
        "api/authors/<uuid:author_id>/posts/<uuid:post_id>/comments/",
        api_views.post_comments_api,
        name="api-post-comments",
    ),
    path(
        "api/authors/<uuid:author_id>/posts/<uuid:post_id>/likes/",
        api_views.post_likes_api,
        name="api-post-likes",
    ),
    path(
        "api/authors/<uuid:author_id>/posts/<uuid:post_id>/comments/<uuid:comment_id>/likes/",
        api_views.comment_likes_api,
        name="api-comment-likes",
    ),
    path(
        "api/authors/<uuid:author_id>/liked/",
        api_views.author_liked_api,
        name="api-author-liked",
    ),
    path("api/likes/<uuid:like_id>/", api_views.like_detail_api, name="api-like-detail"),
]