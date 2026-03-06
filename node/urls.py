from django.urls import path
from . import views

urlpatterns = [
    path("", views.node_home, name="node-home"),
    path("dashboard/", views.node_admin_dashboard, name="node-admin-dashboard"),
    path("approvals/", views.approvals, name="approvals"),
    path("handle-approval/", views.handle_approval, name="handle_approval"),
]