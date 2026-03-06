from django.contrib.auth.decorators import user_passes_test
from django.shortcuts import render
from authors.models import Author
from posts.models import Post
from django.shortcuts import redirect

def superuser_required(user):
    return user.is_superuser

@user_passes_test(superuser_required)
def node_home(request):
    return redirect("node-admin-dashboard")

@user_passes_test(superuser_required)
def node_admin_dashboard(request):
    context = {
        "total_authors": Author.objects.count(),
        "total_posts": Post.objects.count(),
        "superusers": Author.objects.filter(is_superuser=True).count(),
    }
    return render(request, "node/dashboard.html", context)

@user_passes_test(superuser_required)
def approvals(request):
    pending_users = Author.objects.filter(is_approved=False)
    return render(request, "node/approvals.html", {
        "pending_users": pending_users
    })

@user_passes_test(superuser_required)
def handle_approval(request):
    if request.method == "POST":
        author_id = request.POST.get("author_id")
        action = request.POST.get("action")
        author = Author.objects.get(id=author_id)

        if action == "accept":
            author.is_approved = True
            author.save()

        elif action == "reject":
            author.delete()

    return redirect("approvals")