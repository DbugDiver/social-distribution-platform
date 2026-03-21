from datetime import datetime
import uuid
from urllib.parse import unquote, urlparse
from socket import timeout
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from django.http import JsonResponse
import markdown as md  # If you are rendering markdown here
import requests
import json
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
    #return redirect("author-profile", pk=request.user.id)
    return redirect("posts:stream")


@login_required
def author_profile(request, pk):
    """Authors Page with github activity and posts feed"""
    author = get_object_or_404(Author, pk=pk)
    is_following = False
    follow_status = None

    if request.user != author:
        follow = Follower.objects.filter(
            follower=request.user,
            following=author
        ).first()

        if follow:
            follow_status = follow.status
            if follow.status == "accepted":
                is_following = True
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
        "is_following": is_following,
        "follow_status": follow_status,
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

@csrf_exempt
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
def edit_profile(request, author_id=None):
    """Edit Profile Logic"""
    # Merge-fix: default to current user, and block editing someone else's profile.
    
    # If admin, allow editing any author
    if request.user.is_superuser:
        author = get_object_or_404(Author, id=author_id)
    else:
        if author_id is not None and author_id != request.user.id:
            return redirect("author-profile", pk=request.user.id)
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
def send_a_follow_request(request):
    author = request.user
    next_url = (request.POST.get("next") or "").strip()

    def _redirect_back():
        if next_url.startswith("/"):
            return redirect(next_url)
        return redirect("author-search")

    is_remote = request.POST.get("is_remote") == "True"

    if not is_remote:
        # 🏠 LOCAL (your original logic)
        pk = request.POST.get("uuid")
        following = get_object_or_404(Author, pk=pk)

        if author == following:
            return _redirect_back()

        follow = Follower.objects.filter(
            follower=author,
            following=following
        ).first()

        if follow:
            if follow.status == "rejected":
                follow.status = "pending"
                follow.save()

                Notification.objects.create(
                    recipient=following,
                    sender=author,
                    notification_type="follow_request",
                    message=f"{author.displayName or author.username} wants to follow you.",
                )
        else:
            Follower.objects.create(
                follower=author,
                following=following,
                status="pending"
            )

            Notification.objects.create(
                recipient=following,
                sender=author,
                notification_type="follow_request",
                message=f"{author.displayName or author.username} wants to follow you.",
            )

        return _redirect_back()

    else:
        # 🌍 REMOTE FOLLOW
        author_url = request.POST.get("author_id")
        if not author_url:
            return _redirect_back()

        # Keep a local pending edge so callbacks can transition it to accepted.
        remote_target = _upsert_remote_author({"id": author_url, "url": author_url})
        if remote_target:
            relation, _ = Follower.objects.get_or_create(follower=author, following=remote_target)
            if relation.status != "accepted":
                relation.status = "pending"
                relation.save(update_fields=["status"])

        data = {
            "type": "follow",
            "actor": {
                "type": "author",
                "id": f"{settings.SITE_URL}/authors/api/authors/{author.id}",
                "displayName": author.displayName or author.username,
                "host": settings.SITE_URL,
                "url": f"{settings.SITE_URL}/authors/api/authors/{author.id}",
            },
            "object": author_url
        }

        inbox_url = author_url.rstrip("/") + "/inbox/"
        node_url = _host_from_author_url(author_url)
        auth = _auth_for_node(node_url) if node_url else None

        try:
            requests.post(inbox_url, json=data, auth=auth, timeout=5)
        except Exception:
            pass

        return _redirect_back()

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
    
    # Clean up the old follow_request notification
    Notification.objects.filter(
        recipient=author,
        sender=follower,
        notification_type__in=["follow_request", "follow"]
    ).delete()
    
    Notification.objects.create(
        recipient=follower,
        sender=author,
        notification_type="follow_accepted",
        message=f"{author.displayName or author.username} accepted your follow request",
    )

    if follower.is_remote and follower.remote_id:
        payload = {
            "type": "follow_accepted",
            "actor": _local_author_payload(author),
            "object": follower.remote_id,
        }
        _post_remote_inbox(follower.remote_id, payload)

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
    
    # Clean up the follow_request notification when rejecting
    Notification.objects.filter(
        recipient=author,
        sender=follower,
        notification_type__in=["follow_request", "follow"]
    ).delete()
    
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
    
    # Delete local follower relationship(s).
    Follower.objects.filter(follower=author, following=following).delete()

    # Robustness: if duplicate proxy rows exist for the same remote author, remove all of them.
    if following.is_remote and following.remote_id:
        Follower.objects.filter(
            follower=author,
            following__remote_id=following.remote_id,
        ).delete()
    
    # Clean up any notifications related to this follow relationship
    Notification.objects.filter(
        recipient=author,
        sender=following,
        notification_type__in=["follow_request", "follow", "follow_accepted"]
    ).delete()

    if following.is_remote and following.remote_id:
        Notification.objects.filter(
            recipient=author,
            sender__remote_id=following.remote_id,
            notification_type__in=["follow_request", "follow", "follow_accepted"],
        ).delete()

    # Federation: notify remote node so it can remove reciprocal relation there too.
    if following.is_remote and following.remote_id:
        payload = {
            "type": "unfollow",
            "actor": _local_author_payload(author),
            "object": following.remote_id,
        }
        _post_remote_inbox(following.remote_id, payload)
    
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


#-------------------------------Federation
def _try_get_json(url, auth=None, timeout=5):
    try:
        resp = requests.get(
            url,
            auth=auth,
            timeout=timeout,
            headers={"Accept": "application/json"},
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None


def _host_from_author_url(author_url):
    try:
        parsed = urlparse(author_url)
        if not parsed.scheme or not parsed.netloc:
            return None
        return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    except Exception:
        return None


def _auth_for_node(node_url):
    # Federation: per-node basic auth credentials are configured in settings via env.
    if not node_url:
        return None
    creds = getattr(settings, "REMOTE_NODE_CREDENTIALS", {}) or {}
    info = creds.get(node_url.rstrip("/"))
    if info and info.get("username") and info.get("password"):
        return (info["username"], info["password"])
    return None


def _remote_username_seed(remote_id):
    return f"remote_{str(abs(hash(remote_id)))[:20]}"


def _upsert_remote_author(author_payload):
    if not isinstance(author_payload, dict):
        return None

    remote_id = (author_payload.get("id") or author_payload.get("url") or "").strip()
    if not remote_id:
        return None

    host = (author_payload.get("host") or _host_from_author_url(remote_id) or "").rstrip("/")
    # Ensure we get displayName; it's the primary identifier for remote authors.
    display_name = (author_payload.get("displayName") or "").strip()
    if not display_name:
        display_name = (author_payload.get("username") or "").strip()

    # Fallback: resolve from remote author endpoint when payload omits names.
    if not display_name:
        node_url = _host_from_author_url(remote_id)
        remote_doc = _try_get_json(remote_id, auth=_auth_for_node(node_url))
        if isinstance(remote_doc, dict):
            display_name = (remote_doc.get("displayName") or remote_doc.get("username") or "").strip()

    if not display_name:
        display_name = "Remote Author"

    # We store a local proxy row for remote authors so follower relationships remain queryable.
    remote_author = Author.objects.filter(remote_id=remote_id).first()
    if remote_author:
        changed = False
        if display_name and remote_author.displayName != display_name:
            remote_author.displayName = display_name
            changed = True
        if host and remote_author.host != host:
            remote_author.host = host
            changed = True
        if changed:
            remote_author.save(update_fields=["displayName", "host"])
        return remote_author

    username = _remote_username_seed(remote_id)
    while Author.objects.filter(username=username).exists():
        username = f"{username}_{uuid.uuid4().hex[:6]}"

    remote_author = Author.objects.create(
        username=username,
        displayName=display_name,
        host=host,
        is_remote=True,
        remote_id=remote_id,
        is_approved=True,
    )
    remote_author.set_unusable_password()
    remote_author.save(update_fields=["password"])
    return remote_author


def _local_author_payload(author):
    base = settings.SITE_URL.rstrip("/")
    author_url = f"{base}/authors/api/authors/{author.id}"
    return {
        "type": "author",
        "id": author_url,
        "url": author_url,
        "host": base,
        "displayName": author.displayName or author.username,
    }


def _post_remote_inbox(author_url, payload):
    inbox_url = author_url.rstrip("/") + "/inbox/"
    node_url = _host_from_author_url(author_url)
    auth = _auth_for_node(node_url) if node_url else None
    try:
        resp = requests.post(inbox_url, json=payload, auth=auth, timeout=5)
        return resp.status_code in [200, 201, 202]
    except Exception:
        return False


@csrf_exempt
def api_author_inbox(request, pk):
    if request.method != "POST":
        return JsonResponse({"detail": "Method not allowed."}, status=405)

    target = get_object_or_404(Author, pk=pk)

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"detail": "Invalid JSON."}, status=400)

    activity_type = (payload.get("type") or "").lower()

    # Handles remote follow request delivered to this author's inbox.
    if activity_type == "follow":
        actor_payload = payload.get("actor") if isinstance(payload.get("actor"), dict) else {}
        remote_follower = _upsert_remote_author(actor_payload)
        if not remote_follower:
            return JsonResponse({"detail": "Invalid actor payload."}, status=400)

        relation, _ = Follower.objects.get_or_create(
            follower=remote_follower,
            following=target,
        )
        # If already accepted, do not create follow-request notifications.
        if relation.status == "accepted":
            return JsonResponse({"detail": "Already following."}, status=200)

        relation.status = "pending"
        relation.save(update_fields=["status"])

        # Keep exactly one active follow notification for this sender-recipient pair.
        Notification.objects.filter(
            recipient=target,
            sender=remote_follower,
            notification_type__in=["follow", "follow_request"],
        ).delete()
        Notification.objects.create(
            recipient=target,
            sender=remote_follower,
            notification_type="follow",
            message=f"{remote_follower.displayName or remote_follower.username} wants to follow you.",
        )

        return JsonResponse({"detail": "Follow request received."}, status=201)

    # Handles acceptance callback so the requester node can mark status=accepted locally.
    if activity_type == "follow_accepted":
        actor_payload = payload.get("actor") if isinstance(payload.get("actor"), dict) else {}
        remote_target = _upsert_remote_author(actor_payload)
        if not remote_target:
            return JsonResponse({"detail": "Invalid actor payload."}, status=400)

        # Robustness: if pending edge is missing locally (restarts/manual tests), create it.
        relation, _ = Follower.objects.get_or_create(
            follower=target,
            following=remote_target,
        )
        relation.status = "accepted"
        relation.save(update_fields=["status"])

        return JsonResponse({"detail": "Follow accepted received."}, status=200)

    # Handles remote unfollow callback so friendship state updates on this node.
    if activity_type == "unfollow":
        actor_payload = payload.get("actor") if isinstance(payload.get("actor"), dict) else {}
        remote_follower = _upsert_remote_author(actor_payload)
        if not remote_follower:
            return JsonResponse({"detail": "Invalid actor payload."}, status=400)

        Follower.objects.filter(follower=remote_follower, following=target).delete()
        if remote_follower.remote_id:
            Follower.objects.filter(
                follower__remote_id=remote_follower.remote_id,
                following=target,
            ).delete()

        Notification.objects.filter(
            recipient=target,
            sender=remote_follower,
            notification_type__in=["follow", "follow_request", "follow_accepted"],
        ).delete()
        if remote_follower.remote_id:
            Notification.objects.filter(
                recipient=target,
                sender__remote_id=remote_follower.remote_id,
                notification_type__in=["follow", "follow_request", "follow_accepted"],
            ).delete()

        return JsonResponse({"detail": "Unfollow received."}, status=200)

    return JsonResponse({"detail": "Unsupported activity type."}, status=400)

@login_required
def author_search(request):
    query = request.GET.get("q", "").strip()
    results = []
    seen_ids = set()

    if query:
        # LOCAL
        # Only list true local accounts here; remote proxy rows are merged below.
        local_users = Author.objects.filter(
            Q(username__icontains=query) | Q(displayName__icontains=query)
        ).filter(is_remote=False).exclude(id=request.user.id)

        for user in local_users:
            author_id = f"{settings.SITE_URL}/authors/api/authors/{user.id}"
            if author_id in seen_ids:
                continue
            seen_ids.add(author_id)

            results.append({
                "id": f"{settings.SITE_URL}/authors/api/authors/{user.id}",  
                "displayName": user.displayName or user.username,
                "host": settings.SITE_URL,
                "is_remote": False,
                "uuid": str(user.id)  
            })

        # REMOTE
        for node in settings.REMOTE_NODES:
            url = f"{node}/authors/api/authors/?search={query}"

            data = _try_get_json(url, auth=_auth_for_node(node))
            if not data:
                continue

            items = data.get("items", [])  #

            for author in items:
                author_id = (author.get("id") or "").strip()
                if not author_id or author_id in seen_ids:
                    continue

                author["is_remote"] = True
                seen_ids.add(author_id)
                results.append(author)

    context = {
        "query": query,
        "results": results,
    }

    return render(request, "authors/search_results.html", context)