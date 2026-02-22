from socket import timeout

import requests
from django.shortcuts import get_object_or_404, render

from .models import Author


def home_feed(request):
    """Main Page"""
    return render(request, "authors/home.html")


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


# def fetch_github_activity(github_url):
#     # Extract username from URL (e.g., https://github.com/torvalds -> torvalds)
#     username = github_url.strip('/').split('/')[-1]
#     response = requests.get(f'https://api.github.com/users/{username}/events/public')
#     if response.status_code == 200:
#         return response.json()[:5]  # Return the 5 most recent events
#     return []
