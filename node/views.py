from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.shortcuts import render, redirect, get_object_or_404
import requests
from urllib.parse import quote

from authors.models import Author
from .forms import NodeForm
from .models import Node
from .registry import get_configured_nodes, get_node_auth
from posts.models import Post


def superuser_required(user):
    return user.is_superuser


def _auth_for_node(node_url):
    return get_node_auth(node_url)


def _fallback_username(entry):
    username = (entry.get("username") or "").strip()
    if username:
        return username

    display_name = (entry.get("displayName") or "").strip()
    if display_name:
        return display_name

    remote_id = str(entry.get("id") or "").rstrip("/")
    if remote_id:
        return remote_id.rsplit("/", 1)[-1]

    return ""


def _federated_authors():
    local_site = getattr(settings, "SITE_URL", "").rstrip("/")
    items = []
    seen = set()

    # Local authors first.
    for author in Author.objects.all().order_by("username"):
        items.append({
            "id": str(author.id),
            "username": author.username,
            "display_name": author.displayName or author.username,
            "is_approved": bool(author.is_approved),
            "is_remote": False,
            "host": local_site,
            "profile_url": f"/authors/{author.id}/",
        })
        seen.add((local_site, author.username.lower()))

    # Remote authors from configured peer nodes.
    for node in get_configured_nodes(exclude_local=True):
        node = (node or "").rstrip("/")
        if not node or node == local_site:
            continue

        probe_urls = [
            f"{node}/api/authors/?page=1&size=200&_federated=1",
            f"{node}/api/authors/?page=1&size=200",
            f"{node}/api/authors/",
        ]

        for probe in probe_urls:
            try:
                response = requests.get(
                    probe,
                    auth=_auth_for_node(node),
                    timeout=8,
                    headers={"Accept": "application/json"},
                )
                if response.status_code != 200:
                    continue

                if not response.headers.get("content-type", "").startswith("application/json"):
                    continue

                payload = response.json() or {}
                entries = payload.get("items", [])
                if not isinstance(entries, list):
                    continue

                for entry in entries:
                    if not isinstance(entry, dict):
                        continue

                    username = _fallback_username(entry)
                    if not username:
                        continue

                    key = (node, username.lower())
                    if key in seen:
                        continue
                    seen.add(key)

                    remote_id = str(entry.get("id") or "").rstrip("/")
                    profile_url = remote_id
                    if remote_id.endswith("/api/authors"):
                        profile_url = remote_id.replace("/authors/api/authors", "/authors")
                    elif "/authors/api/authors/" in remote_id:
                        profile_url = remote_id.replace("/authors/api/authors/", "/authors/")

                    items.append({
                        "id": remote_id,
                        "username": username,
                        "display_name": entry.get("displayName") or username,
                        # Default to approved for remote display if field absent.
                        "is_approved": bool(entry.get("is_approved", True)),
                        "is_remote": True,
                        "host": entry.get("host") or node,
                        "profile_url": profile_url,
                    })

                # Stop after first successful JSON collection for this node.
                break
            except Exception:
                continue

    return items

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
def manage_nodes(request):
    if request.method == "POST":
        form = NodeForm(request.POST)
        if form.is_valid():
            node = form.save(commit=False)
            node.host = (node.host or "").rstrip("/")
            if node.host == settings.SITE_URL.rstrip("/"):
                messages.error(request, "Cannot add this server as a remote node.")
            else:
                probe_urls = [
                    f"{node.host}/api/authors/?page=1&size=1&_federated=1",
                    f"{node.host}/api/authors/?search={quote('a')}",
                    f"{node.host}/api/authors/",
                ]
                is_reachable = False
                last_status = None
                for probe in probe_urls:
                    try:
                        resp = requests.get(
                            probe,
                            auth=(node.auth_username, node.auth_password),
                            timeout=5,
                            headers={"Accept": "application/json"},
                        )
                        last_status = resp.status_code
                        if resp.status_code in [200, 401, 403]:
                            is_reachable = True
                            break
                    except Exception:
                        continue

                if not is_reachable:
                    messages.error(
                        request,
                        "Could not reach remote host. Check host URL and that the peer app is online.",
                    )
                elif last_status in [401, 403]:
                    messages.error(
                        request,
                        "Remote host rejected credentials (401/403). Check auth username/password for that node.",
                    )
                else:
                    node.save()
                    messages.success(request, "Remote node saved.")
                    return redirect("manage-nodes")
        else:
            messages.error(request, f"Could not save node: {form.errors.as_text()}")
    else:
        form = NodeForm()

    nodes = Node.objects.all().order_by("host")
    return render(request, "node/management.html", {"form": form, "nodes": nodes})


@user_passes_test(superuser_required)
def delete_node(request, node_id):
    node = get_object_or_404(Node, pk=node_id)
    node.delete()
    messages.success(request, "Remote node removed.")
    return redirect("manage-nodes")

@user_passes_test(superuser_required)
def approvals(request):
    all_authors = _federated_authors()
    pending_users = [a for a in all_authors if not a.get("is_approved", True)]

    return render(
        request,
        "node/approvals.html",
        {
            "pending_users": pending_users,
        },
    )


@user_passes_test(superuser_required)
def manage_authors(request):
    authors = _federated_authors()
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