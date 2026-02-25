from socket import timeout

import requests
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from .forms import AuthorUpdateForm
from .models import Author


def home_feed(request):
    """Main Page"""
    return render(request, "posts/stream.html")


def author_profile(request, pk):
    """Authors Page with github activity"""
    author = get_object_or_404(Author, pk=pk)

    # Get Public entries [placeholder for now]
    entries = []

    # Fetch Github activity
    github_events = []
    if author.github:
        # Extract GitHub username from URL
        gh_username = author.github.strip("/").split("/")[-1]
        try:
            gh_res = requests.get(
                f"https://api.github.com/users/{gh_username}/events/public", timeout=2
            )
            if gh_res.status_code == 200:
                # Getting the 5 most recent events
                github_events = gh_res.json()[:5]
        except:
            pass

    context = {
        "profile_user": author,
        "entries": entries,
        "github_events": github_events,
    }

    return render(request, "authors/profile.html", context)


@login_required
def edit_profile(request):
    """Edit Profile Logic"""
    # Grab the currently logged-in user
    author = request.user

    if request.method == "POST":
        form = AuthorUpdateForm(request.POST, instance=author)
        if form.is_valid():
            form.save()
            return redirect("author-profile", pk=author.pk)
    else:
        # If it's a GET request, load the form pre-filled with their current info
        form = AuthorUpdateForm(instance=author)

    return render(request, "authors/edit_profile.html", {"form": form})
