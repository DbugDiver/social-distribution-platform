from django.urls import path

from . import views

'''
urlpatterns = [
    path("", views.home_feed, name="home"),
    path("authors/<uuid:pk>/", views.author_profile, name="author-profile"),
    path("profile/edit/", views.edit_profile, name="edit-profile"),
    path("authors/<uuid:pk>/follow/", views.send_a_follow_request, name="send-follow-request"),
    path("authors/<uuid:pk>/follow/accept/", views.accept_follow_request, name="accept-follow-request"),
    path("authors/<uuid:pk>/follow/reject/", views.reject_follow_request, name="reject-follow-request"),
    path("follow-requests-list/", views.follow_requests, name="follow-requests-list"),
    path("authors/<uuid:pk>/unfollow/", views.unfollow, name="unfollow"),
    path("friends/", views.mutual_following_became_friends, name="friends"),
]
'''
# changes made to merge backend and social distribution
urlpatterns = [
    path("", views.home_feed, name="home-feed"),
    path("<uuid:pk>/", views.author_profile, name="author-profile"),
    path("profile/edit/", views.edit_profile, name="edit-profile"),
    path("<uuid:pk>/follow/", views.send_a_follow_request, name="send-follow-request"),
    path("<uuid:pk>/follow/accept/", views.accept_follow_request, name="accept-follow-request"),
    path("<uuid:pk>/follow/reject/", views.reject_follow_request, name="reject-follow-request"),
    path("follow-requests-list/", views.follow_requests, name="follow-requests-list"),
    path("<uuid:pk>/unfollow/", views.unfollow, name="unfollow"),
    path("friends/", views.mutual_following_became_friends, name="friends"),
    path("friends-list/", views.friends_list, name="friends-list"),
    path("inbox/", views.inbox, name="inbox"),
]