from datetime import datetime
from socket import timeout

import markdown as md  # If you are rendering markdown here
import requests
from django.contrib.auth import authenticate, login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.core.cache import cache
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render

from posts.models import Like, Post

from .forms import AuthorUpdateForm
from .models import Author, Follower, Notification


@login_required
def home_feed(request):
    """Main Page"""
    return redirect("author-profile", pk=request.user.id)


@login_required
def author_profile(request, pk):
    """Authors Page with github activity and posts feed"""
    author = get_object_or_404(Author, pk=pk)

    # 1. Determine if the person viewing is a mutual friend of the profile owner
    is_friend = False
    if request.user != author:
        is_friend = (
            Follower.objects.filter(
                follower=request.user, following=author, status="accepted"
            ).exists()
            and Follower.objects.filter(
                follower=author, following=request.user, status="accepted"
            ).exists()
        )

    # 2. Fetch the correct posts based on who is looking
    if request.user == author:
        # Looking at my own profile: I see all my own posts
        posts = Post.objects.filter(author=author, deleted=False).order_by("-created")
    elif is_friend:
        # A friend is looking: They see Public, Friends-only, and Unlisted posts
        posts = Post.objects.filter(
            author=author,
            deleted=False,
            visibility__in=["PUBLIC", "FRIENDS", "UNLISTED"],
        ).order_by("-created")
    else:
        # A stranger is looking: They only see Public posts
        posts = Post.objects.filter(
            author=author, deleted=False, visibility="PUBLIC"
        ).order_by("-created")

    # 3. Add like/comment counts for the template
    post_liked_ids = set(
        Like.objects.filter(author=request.user, post__in=posts).values_list(
            "post_id", flat=True
        )
    )

    for p in posts:
        # Convert markdown if needed
        if getattr(p, "content_type", "") == "text/markdown" and md:
            p.rendered = md.markdown(p.content or "", extensions=["extra"])
        else:
            p.rendered = None

        p.like_count = p.likes.count()
        p.comment_count = p.comments.count()
        p.liked_by_me = p.id in post_liked_ids

    # 4. Fetch and Format Github activity
    github_events = []
    if author.github:
        gh_username = author.github.strip("/").split("/")[-1]

        # Check if we already have this user's data saved in the cache
        cache_key = f"github_events_{gh_username}"
        cached_events = cache.get(cache_key)

        if cached_events:
            github_events = cached_events
        else:
            try:
                gh_res = requests.get(
                    f"https://api.github.com/users/{gh_username}/events/public",
                    timeout=2,
                )
                if gh_res.status_code == 200:
                    raw_events = gh_res.json()[:5]

                    # Parse the raw data into clean, template-ready dictionaries
                    for event in raw_events:
                        event_type = event.get("type", "UnknownEvent")
                        repo_name = event.get("repo", {}).get("name", "unknown/repo")
                        payload = event.get("payload", {})

                        # Fix 1: Convert GitHub's text date into a real Python datetime object
                        raw_date = event.get("created_at", "")
                        if raw_date:
                            try:
                                parsed_date = datetime.strptime(
                                    raw_date, "%Y-%m-%dT%H:%M:%SZ"
                                )
                            except ValueError:
                                parsed_date = None
                        else:
                            parsed_date = None

                        clean_event = {
                            "repo_name": repo_name,
                            "repo_url": f"https://github.com/{repo_name}",
                            "created_at": parsed_date,  # Now passes a real date!
                            "action_text": f"triggered a {event_type} on",
                            "extra_info": None,
                            "icon": "🤖",
                        }

                        if event_type == "PushEvent":
                            commits = payload.get("commits", [])
                            # Fix 2: Check GitHub's 'size' key if the commits list is empty
                            clean_event["action_text"] = f"pushed commit to"
                            clean_event["icon"] = "🛠️"

                            if commits and "message" in commits[0]:
                                clean_event["extra_info"] = commits[0]["message"][:100]

                        elif event_type == "PullRequestEvent":
                            action = payload.get("action", "opened")
                            clean_event["action_text"] = f"{action} a pull request on"
                            clean_event["icon"] = "🔄"
                            clean_event["extra_info"] = payload.get(
                                "pull_request", {}
                            ).get("title", "")[:100]

                        elif event_type == "IssuesEvent":
                            action = payload.get("action", "opened")
                            clean_event["action_text"] = f"{action} an issue on"
                            clean_event["icon"] = "⚠️"
                            clean_event["extra_info"] = payload.get("issue", {}).get(
                                "title", ""
                            )[:100]

                        elif event_type == "WatchEvent":
                            clean_event["action_text"] = "starred the repository"
                            clean_event["icon"] = "⭐️"

                        elif event_type == "CreateEvent":
                            ref_type = payload.get("ref_type", "repository")
                            clean_event["action_text"] = f"created a {ref_type} for"
                            clean_event["icon"] = "🌱"

                        github_events.append(clean_event)

                    # Save the parsed events to the cache for 300 seconds (5 minutes)
                    cache.set(cache_key, github_events, 300)
            except:
                pass

    context = {
        "profile_user": author,
        "posts": posts,
        "github_events": github_events,
    }

    return render(request, "authors/profile.html", context)


def custom_login(request):
    form = AuthenticationForm()
    if request.method == "POST":
        username = request.POST.get("username")
        password = request.POST.get("password")
        user = authenticate(request, username=username, password=password)
        form = AuthenticationForm(initial={"username": username})
        if user is not None:
            if not user.is_approved:
                return render(
                    request,
                    "registration/login.html",
                    {"show_pending": True, "form": form},
                )
            login(request, user)
            return redirect("home-feed")

        return render(
            request, "registration/login.html", {"show_invalid": True, "form": form}
        )
    return render(request, "registration/login.html", {"form": form})


def signup_author(request):
    if request.method == "POST":
        username = request.POST.get("username")
        password = request.POST.get("password")

        all_authors = Author.objects.values_list("username", flat=True)
        form = AuthenticationForm(initial={"username": username})
        if username in all_authors:
            return render(
                request, "registration/login.html", {"show_message": True, "form": form}
            )

        Author.objects.create_user(
            username=username, password=password, is_approved=False
        )

        return render(
            request, "registration/login.html", {"show_signup": True, "form": form}
        )


@login_required
def edit_profile(request):
    """Edit Profile Logic"""
    author = request.user

    if request.method == "POST":
        form = AuthorUpdateForm(request.POST, request.FILES, instance=author)

        github_link = request.POST.get("github", "").strip()

        # Check the GitHub link
        if github_link and not github_link.startswith(
            ("https://github.com/", "http://github.com/")
        ):
            # It's invalid! Return the page WITH the form so they don't lose their other edits
            return render(
                request,
                "authors/edit_profile.html",
                {
                    "form": form,
                    "error": "Please enter a valid GitHub profile link starting with https://github.com/",
                },
            )

        # Handle the image upload
        if "profileImage" in request.FILES:
            author.profileImage = request.FILES["profileImage"]

        # If we get down here, the GitHub link is either valid or empty.
        if form.is_valid():
            form.save()
            author.save()  # Because github is in your form, form.save() saves it automatically! No need for author.github = github_link
            return redirect("author-profile", pk=author.pk)

    else:
        # If it's a GET request, load the form pre-filled with their current info
        form = AuthorUpdateForm(instance=author)

    return render(request, "authors/edit_profile.html", {"form": form})


@login_required
def send_a_follow_request(request, pk):
    """Send a follow request to another author"""
    author = request.user  # get the currently logged in user

    following = get_object_or_404(
        Author, pk=pk
    )  # get the author that the user wants to follow, if the author does not exist, return a 404 error

    if author == following:
        return redirect("author-profile", pk=pk)
    # check if the follow request already exists
    follow_object, created = Follower.objects.get_or_create(
        follower=author, following=following, defaults={"status": "pending"}
    )  # create a new follow request for the following author, if it already exists, do nothing
    if created:
        Notification.objects.create(
            recipient=following,
            sender=author,
            notification_type="follow_request",
            message=f"{author.displayName or author.username} wants to follow you.",
        )
    return redirect("author-profile", pk=pk)


@login_required
def accept_follow_request(request, pk):
    """accept a follow request from another author"""
    author = request.user  # get the currently logged in user

    follower = get_object_or_404(
        Author, pk=pk
    )  # get the author that sent the follow request, if the author does not exist, return a 404 error
    follow_request = get_object_or_404(
        Follower, follower=follower, following=author
    )  # get the follow request, if it does not exist, return a 404 error
    follow_request.status = (
        "accepted"  # update the status of the follow request to accepted
    )
    follow_request.save()  # save the changes to the database
    Notification.objects.create(
        recipient=follower,
        sender=author,
        notification_type="follow_accepted",
        message=f"{author.displayName} accepted your follow request",
    )

    return redirect("author-profile", pk=author.pk)


@login_required
def reject_follow_request(request, pk):
    """reject a follow request from another author"""
    author = request.user  # get the currently logged in user

    follower = get_object_or_404(
        Author, pk=pk
    )  # get the author that sent the follow request, if the author does not exist, return a 404 error
    follow_request = get_object_or_404(
        Follower, follower=follower, following=author
    )  # get the follow request, if it does not exist, return a 404 error
    follow_request.status = (
        "rejected"  # update the status of the follow request to rejected
    )
    follow_request.save()  # save the changes to the database
    return redirect("author-profile", pk=author.pk)


@login_required
# As an author, I want to know if I have "follow requests," so I can approve them
def follow_requests(request):
    """View all pending follow requests for the logged-in author"""
    author = request.user
    pending_follow_requests = Follower.objects.filter(
        following=author, status="pending"
    )
    context = {
        "pending_follow_requests": pending_follow_requests,
    }
    return render(request, "authors/follow_requests.html", context)


@login_required
def unfollow(request, pk):
    """UNfollow an author that you are currently following"""
    author = request.user  # get the currently logged in user

    following = get_object_or_404(
        Author, pk=pk
    )  # get the author that the user wants to unfollow, if the author does not exist, return a 404 error
    Follower.objects.filter(follower=author, following=following).delete()
    return redirect("author-profile", pk=pk)


# As an author, if I am following another author, and they are following me (only after both follow requests are approved), I want us to be considered friends, so that they can see my friends-only entries.
@login_required
def mutual_following_became_friends(request):
    """View all friends of the logged-in author"""
    author = request.user  # get the currently logged in user
    following = Follower.objects.filter(
        follower=author, status="accepted"
    ).values_list(
        "following", flat=True
    )  # get all the authors that the author that is logged-in is following and those that have accepted thier follow request
    followers = Follower.objects.filter(
        following=author, status="accepted"
    ).values_list(
        "follower", flat=True
    )  # get all the authors that are following the author that is currently logged in and that have accepted the follow request ie they are both following each other
    friends = Author.objects.filter(
        id__in=following
    ).filter(
        id__in=followers
    )  # get all the authors that are both following the logged-in author and that are being followed by the logged-in author, these are the friends of the logged-in author
    context = {
        "friends": friends
    }  # create a context dictionary to pass the friends to the template
    return render(request, "authors/friends.html", context)


@login_required
def friends_list(request):
    """Show friends (mutual followers) and all authors the user is following"""
    author = request.user

    # Mutual friends
    following_ids = Follower.objects.filter(
        follower=author, status="accepted"
    ).values_list("following", flat=True)
    followers_ids = Follower.objects.filter(
        following=author, status="accepted"
    ).values_list("follower", flat=True)
    friends = Author.objects.filter(id__in=following_ids).filter(id__in=followers_ids)

    # Everyone the user is following (accepted only)
    following = Author.objects.filter(id__in=following_ids)

    context = {
        "friends": friends,
        "following": following,
    }

    return render(request, "authors/friends_list.html", context)


@login_required
def inbox(request):
    """Show notifications for logged-in user"""
    author = request.user
    notifications = (
        Notification.objects.filter(recipient=author)
        .select_related("sender")
        .order_by("-created_at")
    )
    context = {"notifications": notifications}
    return render(request, "authors/inbox.html", context)


@login_required
def author_search(request):
    query = request.GET.get("q", "")
    results = []
    if query:
        results = Author.objects.filter(
            Q(username__icontains=query) | Q(displayName__icontains=query)
        ).exclude(id=request.user.id)
    context = {"query": query, "results": results}
    return render(request, "authors/search_results.html", context)
