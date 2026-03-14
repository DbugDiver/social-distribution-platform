from django.urls import path
from . import views

urlpatterns = [
    path("", views.node_home, name="node-home"),
    path("dashboard/", views.node_admin_dashboard, name="node-admin-dashboard"),
    path("approvals/", views.approvals, name="approvals"),
    path("authors/", views.manage_authors, name="manage-authors"),
    path("add-author/", views.add_author_page, name="add-author"),
    path("handle-approval/", views.handle_approval, name="handle_approval"),
    path("delete-author/<uuid:author_id>/", views.delete_author, name="delete-author"),
]