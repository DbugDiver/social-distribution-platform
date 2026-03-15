from django.contrib.auth.decorators import user_passes_test
from django.shortcuts import render, redirect, get_object_or_404
from authors.models import Author
from posts.models import Post


def superuser_required(user):
    return user.is_superuser

@user_passes_test(superuser_required)
def node_home(request):
    return redirect("node-admin-dashboard")

@user_passes_test(superuser_required)
def add_author_page(request):
    if request.method == "POST":

        username = request.POST.get("username")
        password = request.POST.get("password")

        if Author.objects.filter(username=username).exists():
            return render(
                request,
                "node/add_author.html",
                {"show_message": True}
            )
        Author.objects.create_user(
            username=username,
            password=password,
            is_approved=True
        )
        return redirect("manage-authors")

    return render(request, "node/add_author.html")
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
def manage_authors(request):
    authors = Author.objects.all()

    return render(request, "node/manage_authors.html", {"authors": authors})
@user_passes_test(superuser_required)
def delete_author(request, author_id):
    author = get_object_or_404(Author, id=author_id)

    # prevent deleting yourself (recommended)
    if author == request.user:
        return redirect("manage-authors")

    author.delete()   # deletes profile + posts if cascade is set

    return redirect("manage-authors")

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