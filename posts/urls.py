from django.urls import path
from . import views

app_name = "posts"

urlpatterns = [
    path("", views.stream, name="stream"),
    path("new/", views.create, name="create"),
    path("<uuid:post_id>/", views.detail, name="detail"),
    path("<uuid:post_id>/edit/", views.edit, name="edit"),
    path("<uuid:post_id>/delete/", views.delete, name="delete"),
]