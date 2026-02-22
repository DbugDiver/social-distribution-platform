from django.urls import path

from . import views

urlpatterns = [
    path("", views.home_feed, name="home"),
    path("authors/<uuid:pk>/", views.author_profile, name="author-profile"),
]
