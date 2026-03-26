from django.urls import path
from . import api_views
from . import views

urlpatterns = [
    path("", api_views.api_get_all_authors),  # /api/authors/
    path("<uuid:pk>/", api_views.api_get_author),
    path("<uuid:pk>/follow/", api_views.api_follow_author),
    path("<uuid:pk>/inbox/", views.api_author_inbox),
    path("<uuid:pk>/accept/", api_views.api_accept_follow),
    path("<uuid:pk>/reject/", api_views.api_reject_follow),
    path("<uuid:pk>/following/", api_views.api_get_following),
    path("<uuid:pk>/unfollow/", api_views.api_unfollow),
    path("<uuid:pk>/friends/", api_views.api_get_friends),
]
''' old ones - >
  # User Story 2: REST GET author endpoint for alternate clients.
    path("api/authors/<uuid:pk>/", api_views.api_get_author),
    path("api/authors/<uuid:pk>/follow/", api_views.api_follow_author),
    path("api/authors/<uuid:pk>/inbox/", views.api_author_inbox),
    path("api/authors/<uuid:pk>/accept/", api_views.api_accept_follow),
    path("api/authors/<uuid:pk>/reject/", api_views.api_reject_follow),
    path("api/authors/<uuid:pk>/following/", api_views.api_get_following),
    path("api/authors/<uuid:pk>/unfollow/", api_views.api_unfollow),
    path("api/authors/<uuid:pk>/friends/", api_views.api_get_friends),
    path("api/authors/", api_views.api_get_all_authors),
'''