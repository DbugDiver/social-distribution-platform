from datetime import datetime
import time
import hashlib
import re
from functools import lru_cache
from urllib.parse import urljoin
from urllib.parse import quote
import markdown as md
import requests
import uuid
from django.conf import settings
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.cache import cache
from django.db.models import Q
from django.http import Http404, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from authors.models import Author, Follower
from node.registry import get_configured_nodes, get_node_auth
from .forms import PostForm
from .models import Comment, Like, Post

# ---------- Markdown ----------

def _render_markdown(text: str) -> str:
    try:
        import markdown
    except ImportError:
        return text
    return markdown.markdown(text, extensions=["extra", "sane_lists"])


# ---------- Friendship / visibility ----------

def _is_friend(user, other):
    return (
        Follower.objects.filter(follower=user, following=other, status="accepted").exists()
        and Follower.objects.filter(follower=other, following=user, status="accepted").exists()
    )


def _can_interact_with_post(user, post):
    if post.deleted:
        return False

    if post.visibility in [Post.Visibility.PUBLIC, Post.Visibility.UNLISTED]:
        return user.is_authenticated

    if not user.is_authenticated:
        return False

    if post.is_remote:
        # For remote FRIENDS-only, we cannot reliably compute friendship unless you
        # also federate follower syncing. Allow only public/unlisted here.
        return False

    if user == post.author:
        return True

    return _is_friend(user, post.author)


def _visible_comments_for_viewer(user, post):
    comments = post.comments.select_related("author").prefetch_related("likes")

    if post.visibility != Post.Visibility.FRIENDS:
        return comments

    if not user.is_authenticated:
        return comments.none()

    if post.is_remote:
        return comments.none()

    if user == post.author or _is_friend(user, post.author):
        return comments

    return comments.filter(author=user)


# ---------- Local author identity helpers ----------

def _site_url():
    return getattr(settings, "SITE_URL", "").rstrip("/")

# ---------- Federation HTTP helpers ----------

@lru_cache(maxsize=128)
def _cached_auth_for_node(node_url):
    return get_node_auth(node_url)


def _auth_for_node(node_url):
    normalized = (node_url or "").rstrip("/")
    if not normalized:
        return None
    return _cached_auth_for_node(normalized)


def _candidate_post_endpoints(node_url):
    base = node_url.rstrip("/")
    return [
        f"{base}/api/entries/public/",
        f"{base}/api/public-entries/",
        f"{base}/api/public-posts/",
        f"{base}/api/entries/",
        f"{base}/api/posts/",
    ]

def _extract_collection_items(data):
    if isinstance(data, list):
        return data

    if not isinstance(data, dict):
        return []

    for key in ("src", "items", "posts", "results"):
        value = data.get(key)
        if isinstance(value, list):
            return value

    return []


def _normalize_visibility_value(value):
    raw = (value or Post.Visibility.PUBLIC).strip().upper()
    if raw == "PUBLIC":
        return Post.Visibility.PUBLIC
    if raw in {"FRIENDS", "FRIENDS_ONLY"}:
        return Post.Visibility.FRIENDS
    if raw == "UNLISTED":
        return Post.Visibility.UNLISTED
    return Post.Visibility.PUBLIC

def _try_get_json(url, auth=None, timeout=2):
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


def _try_post_json(url, payload, auth=None, timeout=5):
    try:
        resp = requests.post(
            url,
            json=payload,
            auth=auth,
            timeout=timeout,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        return resp.status_code in [200, 201, 202]
    except Exception:
        return False


def _normalize_author_id(value):
    raw = (value or "").strip().rstrip("/")
    if not raw:
        return ""
    # Treat /authors/<id> and /authors/api/authors/<id> as the same identity.
    return raw.replace("/authors/api/authors/", "/authors/")


def _remote_like_matches_user(raw_like, user):
    author = raw_like.get("author", {}) if isinstance(raw_like.get("author"), dict) else {}
    candidate_ids = {
        _normalize_author_id(author.get("id")),
        _normalize_author_id(author.get("url")),
    }
    local_ids = {
        _normalize_author_id(f"{_site_url()}/api/authors/{user.id}"),
        _normalize_author_id(f"{_site_url()}/authors/{user.id}"),
    }
    return bool(candidate_ids.intersection(local_ids))


def _normalized_local_author_ids(user):
    base = _site_url()
    return {
        _normalize_author_id(f"{base}/api/authors/{user.id}"),
        _normalize_author_id(f"{base}/authors/{user.id}"),
    }


def _remote_like_entry_matches_user(entry, user):
    if not isinstance(entry, dict):
        return False

    author = entry.get("author", {}) if isinstance(entry.get("author"), dict) else {}

    candidates = {
        _normalize_author_id(entry.get("author_id", "")),
        _normalize_author_id(author.get("id", "")),
        _normalize_author_id(author.get("url", "")),
    }

    local_ids = _normalized_local_author_ids(user)
    return bool(candidates.intersection(local_ids))


def _parse_datetime(value):
    from datetime import datetime
    from django.utils import timezone

    if not value:
        return timezone.now()
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return timezone.now()


def _looks_like_base64_image_blob(value):
    """Detect base64-encoded image data with high confidence.

    Checks for:
    - Known image format signatures (JPEG, PNG, GIF, WebP, BMP, etc)
    - Long continuous alphanumeric strings that match base64 pattern
    - Excludes data URLs and real URLs
    """
    raw = (value or "").strip()

    # Skip if too short, already a data URL, or looks like a real URL
    if len(raw) < 100:
        return False
    if raw.startswith(("data:", "http://", "https://")):
        return False
    # Skip slash-prefixed paths, but NOT /9j/ or similar base64 signatures
    if raw.startswith("/") and not any(c.isdigit() for c in raw[1:6]):
        return False

    # Check for common image base64 signatures: jpeg, png, gif, webp, bmp, tiff
    if raw.startswith(("/9j/", "iVBOR", "R0lGOD", "UklGR", "QkI", "TU4g", "II4g")):
        return True

    # Additional check: long base64-like string (mostly alphanumeric + /+= with good entropy)
    if len(raw) > 150:
        # Remove padding and common separators
        clean = raw.replace("=", "").replace("+", "").replace("/", "").replace("\n", "").replace("\r", "").replace(" ", "")

        # If it's a very long continuous alphanumeric string, likely base64 encoded binary
        if re.match(r"^[A-Za-z0-9]{100,}$", clean):
            # Count uppercase/lowercase to filter out things like "aaaaaa..."
            upper = sum(1 for c in raw if c.isupper())
            lower = sum(1 for c in raw if c.islower())
            nums = sum(1 for c in raw if c.isdigit())

            # Real base64 has good mix of cases and numbers
            if upper > 5 and lower > 5 and (upper + lower + nums) / len(raw) > 0.9:
                return True

    return False



def _normalize_remote_post(raw, node_url):
    author = raw.get("author") if isinstance(raw.get("author"), dict) else {}
    remote_post_id = raw.get("id") or raw.get("remote_id") or raw.get("source") or raw.get("origin") or raw.get("url")

    comments_raw = raw.get("comments")
    if isinstance(comments_raw, dict):
        remote_comments_url = comments_raw.get("id") or comments_raw.get("url") or ""
        remote_comment_count = comments_raw.get("count", raw.get("count", 0))
    elif isinstance(comments_raw, str):
        remote_comments_url = comments_raw.strip()
        remote_comment_count = raw.get("count", 0)
    else:
        remote_comments_url = ""
        remote_comment_count = raw.get("count", 0)

    likes_raw = raw.get("likes")
    if isinstance(likes_raw, dict):
        remote_likes_url = likes_raw.get("id") or likes_raw.get("url") or ""
        remote_like_count = likes_raw.get("count", 0)
    elif isinstance(likes_raw, str):
        remote_likes_url = likes_raw.strip()
        remote_like_count = 0
    else:
        remote_likes_url = ""
        remote_like_count = 0

    image_url = (raw.get("image") or "").strip()
    content = raw.get("content") or ""
    if isinstance(content, str):
        content = content.strip()

    if not image_url and isinstance(content, str):
        if content.startswith("data:image/"):
            image_url = content
            content = ""
        elif _looks_like_base64_image_blob(content):
            image_url = f"data:image/jpeg;base64,{content}"
            content = ""

    if image_url and _looks_like_base64_image_blob(image_url):
        image_url = f"data:image/jpeg;base64,{image_url}"

    if image_url.startswith("/") and node_url:
        image_url = f"{node_url.rstrip('/')}{image_url}"

    remote_author_image = (author.get("profileImage") or "").strip()
    if remote_author_image.startswith("/") and node_url:
        remote_author_image = f"{node_url.rstrip('/')}{remote_author_image}"

    # FIX (Change 4): normalize remote_author_url so URL-variant matching works correctly
    raw_author_url = author.get("id") or author.get("url") or ""

    return {
        "remote_id": str(remote_post_id).strip() if remote_post_id else "",
        "title": raw.get("title") or raw.get("description") or "",
        "content": content,
        "content_type": raw.get("contentType") or raw.get("content_type") or Post.ContentType.PLAIN,
        "visibility": _normalize_visibility_value(raw.get("visibility")),
        "published": raw.get("published") or raw.get("created") or raw.get("updated"),
        "node_url": node_url.rstrip("/"),
        "remote_author_url": _normalize_author_id(raw_author_url),
        "remote_author_name": author.get("displayName") or author.get("username") or "Remote Author",
        "remote_author_host": author.get("host") or node_url.rstrip("/"),
        "remote_author_image": remote_author_image,
        "remote_image": image_url,
        "remote_comments_url": remote_comments_url,
        "remote_likes_url": remote_likes_url,
        "remote_comment_count": remote_comment_count,
        "remote_like_count": remote_like_count,
    }

# FIX (Change 1): always set deleted=False on upsert so FRIENDS->PUBLIC posts come back
def _upsert_remote_post_cache(data):
    remote_id = data["remote_id"]
    if not remote_id:
        return None

    post, created = Post.objects.update_or_create(
        remote_id=remote_id,
        defaults={
            "author": None,
            "is_remote": True,
            "node_url": data["node_url"],
            "remote_author_url": data["remote_author_url"],
            "remote_author_name": data["remote_author_name"],
            "remote_author_host": data["remote_author_host"],
            "remote_image": data.get("remote_image", ""),
            "title": data["title"],
            "content": data["content"],
            "content_type": data["content_type"][:50],
            "visibility": data["visibility"] if data["visibility"] in Post.Visibility.values else Post.Visibility.PUBLIC,
            "published": _parse_datetime(data["published"]),
            "deleted": False,  # FIX: always un-delete on upsert
        },
    )

    # attach transient attrs used by templates/views
    post.remote_comments_url = data["remote_comments_url"]
    post.remote_likes_url = data["remote_likes_url"]
    post.remote_author_image = data.get("remote_author_image", "")
    post.remote_comment_count = data["remote_comment_count"]
    post.remote_like_count = data["remote_like_count"]
    return post


def _sanitize_cached_remote_post(post):
    if not post.is_remote:
        return

    changed_fields = []
    raw_content = (post.content or "").strip()
    raw_image = (post.remote_image or "").strip()

    if raw_content:
        if raw_content.startswith("data:image/"):
            post.remote_image = raw_content
            post.content = ""
            changed_fields.extend(["remote_image", "content"])
        elif _looks_like_base64_image_blob(raw_content):
            # Just clear base64 content (can't store as data URL - field size limit)
            post.content = ""
            changed_fields.append("content")

    if post.remote_image and _looks_like_base64_image_blob(post.remote_image):
        post.remote_image = f"data:image/jpeg;base64,{post.remote_image.strip()}"
        if "remote_image" not in changed_fields:
            changed_fields.append("remote_image")

    if changed_fields:
        post.save(update_fields=changed_fields)

def _fetch_remote_public_posts():
    if cache.get("federation_public_posts_refresh_lock"):
        return []

    cache.set("federation_public_posts_refresh_lock", True, 120)
    cached = []
    start = time.monotonic()
    max_seconds = 4.0
    deadline = start + max_seconds
    max_nodes = int(getattr(settings, "FEDERATION_MAX_NODES", 3) or 3)

    for node in get_configured_nodes(exclude_local=True)[:max_nodes]:
        if time.monotonic() >= deadline:
            break

        node = node.rstrip("/")
        if node == _site_url():
            continue

        auth = _auth_for_node(node)
        seen_remote_ids = set()
        found_feed = False

        for endpoint in _candidate_post_endpoints(node):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break

            # Keep individual probe timeouts short so the stream never stalls.
            probe_timeout = max(0.5, min(1.2, remaining))
            data = _try_get_json(endpoint, auth=auth, timeout=probe_timeout)
            if not data:
                continue

            items = _extract_collection_items(data)
            if not isinstance(items, list):
                continue

            found_feed = True

            for raw in items:
                if not isinstance(raw, dict):
                    continue

                normalized = _normalize_remote_post(raw, node)
                if not normalized["remote_id"]:
                    continue

                if normalized["visibility"] != Post.Visibility.PUBLIC:
                    continue

                seen_remote_ids.add(normalized["remote_id"])

                post = _upsert_remote_post_cache(normalized)
                if post:
                    cached.append(post)

            break

        # FIX (Change 2): only mark missing PUBLIC posts as deleted.
        # FRIENDS/UNLISTED posts won't appear in the public feed, so they
        # should NOT be treated as deleted just because they're absent here.
        if found_feed:
            Post.objects.filter(
                is_remote=True,
                node_url=node,
                deleted=False,
                visibility=Post.Visibility.PUBLIC,  # FIX: scope to PUBLIC only
            ).exclude(
                remote_id__in=seen_remote_ids
            ).update(deleted=True)

    return cached


def _active_remote_nodes():
    return {node.rstrip("/") for node in get_configured_nodes(exclude_local=True)}


def _is_post_from_active_remote_node(post, active_nodes=None):
    if not getattr(post, "is_remote", False):
        return True
    nodes = active_nodes if active_nodes is not None else _active_remote_nodes()
    return (getattr(post, "node_url", "") or "").rstrip("/") in nodes

def _candidate_single_post_endpoints(node_url, remote_post_id):
    base = node_url.rstrip("/")
    rid = str(remote_post_id).strip("/")
    return [
        rid,
        f"{base}/{rid}/",
    ]

def _get_remote_post_or_404(post):
    if not post.is_remote or not post.node_url or not post.remote_id:
        return post

    auth = _auth_for_node(post.node_url)
    for endpoint in _candidate_single_post_endpoints(post.node_url, post.remote_id):
        data = _try_get_json(endpoint, auth=auth)
        if not data or not isinstance(data, dict):
            continue

        normalized = _normalize_remote_post(data, post.node_url)
        updated = _upsert_remote_post_cache(normalized)
        if updated:
            return updated

    return post

def _check_remote_post_visibility(post):
    """
    Probe the remote server for the actual current visibility of a post.

    Returns:
        Post.Visibility.PUBLIC / FRIENDS / UNLISTED  — if we got a 200 and read the visibility
        False  — if remote returned 403/401/404 (access denied or deleted)
        None   — if we couldn't reach the remote at all (timeout, network error)
    """
    if not post.is_remote or not post.node_url or not post.remote_id:
        return None

    auth = _auth_for_node(post.node_url)

    for endpoint in _candidate_single_post_endpoints(post.node_url, post.remote_id):
        try:
            resp = requests.get(
                endpoint,
                auth=auth,
                timeout=3,
                headers={"Accept": "application/json"},
            )

            if resp.status_code == 200:
                try:
                    data = resp.json()
                    if isinstance(data, dict):
                        vis = _normalize_visibility_value(data.get("visibility"))
                        # Also update the full cache while we're at it
                        normalized = _normalize_remote_post(data, post.node_url)
                        _upsert_remote_post_cache(normalized)
                        return vis
                except Exception:
                    pass
                # Got 200 but couldn't parse — assume still accessible
                return post.visibility

            if resp.status_code in [403, 401]:
                # Access denied — post was restricted
                Post.objects.filter(id=post.id).update(
                    visibility=Post.Visibility.FRIENDS
                )
                return False

            if resp.status_code == 404:
                # Post was deleted on remote
                Post.objects.filter(id=post.id).update(deleted=True)
                return False

            # Other status (500, etc) — try next endpoint
        except Exception:
            continue

    # All endpoints failed
    return None

def _author_inbox_url(author_url):
    if not author_url:
        return None
    return f"{author_url.rstrip('/')}/inbox/"

def _local_author_payload(user):
    base = _site_url()
    return {
        "type": "author",
        "id": f"{base}/api/authors/{user.id}",
        "host": f"{base}/api/",
        "displayName": getattr(user, "displayName", "") or getattr(user, "username", "Local User"),
        "github": getattr(user, "github", "") or "",
        "profileImage": getattr(user, "profileImage", "") or "",
        "web": f"{base}/authors/{user.id}",
    }


def _url_variants(url):
    raw = (url or "").strip()
    if not raw:
        return []

    variants = [raw]
    if raw.endswith("/"):
        variants.append(raw.rstrip("/"))
    else:
        variants.append(f"{raw}/")

    deduped = []
    seen = set()
    for value in variants:
        if value and value not in seen:
            deduped.append(value)
            seen.add(value)
    return deduped


def _author_url_variants(url):
    raw = (url or "").strip().rstrip("/")
    if not raw:
        return set()

    variants = set(_url_variants(raw))

    if "/authors/api/authors/" in raw:
        html_variant = raw.replace("/authors/api/authors/", "/authors/")
        api_variant = raw.replace("/authors/api/authors/", "/api/authors/")
        variants.update(_url_variants(html_variant))
        variants.update(_url_variants(api_variant))

    if "/api/authors/" in raw:
        html_variant = raw.replace("/api/authors/", "/authors/")
        nested_variant = raw.replace("/api/authors/", "/authors/api/authors/")
        variants.update(_url_variants(html_variant))
        variants.update(_url_variants(nested_variant))

    if "/authors/" in raw and "/authors/api/authors/" not in raw:
        api_variant = raw.replace("/authors/", "/api/authors/")
        nested_variant = raw.replace("/authors/", "/authors/api/authors/")
        variants.update(_url_variants(api_variant))
        variants.update(_url_variants(nested_variant))

    return variants


def _post_url_variants(url):
    raw = (url or "").strip()
    if not raw:
        return []

    # For POST/DELETE, prefer slash form first to avoid 301->GET redirect semantics.
    if raw.endswith("/"):
        variants = [raw, raw.rstrip("/")]
    else:
        variants = [f"{raw}/", raw]

    deduped = []
    seen = set()
    for value in variants:
        if value and value not in seen:
            deduped.append(value)
            seen.add(value)
    return deduped

def _candidate_remote_comments_urls(post):
    urls = []

    explicit = (getattr(post, "remote_comments_url", "") or "").strip()
    if explicit:
        urls.extend(_url_variants(explicit))
        if "/api/authors/" in explicit and "/api/public/" not in explicit:
            public_explicit = explicit.replace("/api/authors/", "/api/public/authors/")
            urls.extend(_url_variants(public_explicit))

    remote_id = str(post.remote_id or "").rstrip("/")
    node_base = str(post.node_url or "").rstrip("/")

    if remote_id:
        # 1. Direct: remote_id + /comments
        urls.extend(_post_url_variants(remote_id + "/comments"))

        # 2. /api/entries/{uuid}/comments/ (no author prefix)
        if node_base:
            for marker in ["/entries/", "/posts/"]:
                if marker in remote_id:
                    entry_id = remote_id.split(marker)[-1].strip("/")
                    if entry_id:
                        urls.extend(_post_url_variants(f"{node_base}/api/entries/{entry_id}/comments"))
                        urls.extend(_post_url_variants(f"{node_base}/api/posts/{entry_id}/comments"))
                    break

        # 3. /api/public/ variant
        if "/api/authors/" in remote_id:
            public_path = remote_id.replace("/api/authors/", "/api/public/authors/") + "/comments"
            urls.extend(_post_url_variants(public_path))

        if "/authors/" in remote_id and "/api/authors/" not in remote_id:
            api_path = remote_id.replace("/authors/", "/api/authors/") + "/comments"
            public_path = remote_id.replace("/authors/", "/api/public/authors/") + "/comments"
            urls.extend(_post_url_variants(api_path))
            urls.extend(_post_url_variants(public_path))

        # 4. /entries/ ↔ /posts/ cross-variants
        if "/entries/" in remote_id:
            posts_variant = remote_id.replace("/entries/", "/posts/")
            urls.extend(_post_url_variants(posts_variant + "/comments"))
            if "/api/authors/" in posts_variant:
                urls.extend(_post_url_variants(
                    posts_variant.replace("/api/authors/", "/api/public/authors/") + "/comments"
                ))
        elif "/posts/" in remote_id:
            entries_variant = remote_id.replace("/posts/", "/entries/")
            urls.extend(_post_url_variants(entries_variant + "/comments"))
            if "/api/authors/" in entries_variant:
                urls.extend(_post_url_variants(
                    entries_variant.replace("/api/authors/", "/api/public/authors/") + "/comments"
                ))

        # 5. FQID-based
        if node_base:
            encoded_fqid = quote(remote_id, safe="")
            fqid_comments_url = f"{node_base}/api/entries/{encoded_fqid}/comments/"
            urls.extend(_post_url_variants(fqid_comments_url))
    deduped = []
    seen = set()
    for value in urls:
        if value and value not in seen:
            deduped.append(value)
            seen.add(value)
    return deduped


def _candidate_remote_likes_urls(post):
    urls = []

    explicit = (getattr(post, "remote_likes_url", "") or "").strip()
    if explicit:
        urls.extend(_url_variants(explicit))

    remote_id = str(post.remote_id or "").rstrip("/")
    if remote_id:
        if "/api/authors/" in remote_id and "/entries/" in remote_id:
            public_path = remote_id.replace("/api/authors/", "/api/public/authors/") + "/likes"
            api_path = remote_id + "/likes"
            html_path = remote_id.replace("/api/authors/", "/authors/") + "/likes"
            urls.extend(_post_url_variants(public_path))
            urls.extend(_post_url_variants(api_path))
            urls.extend(_post_url_variants(html_path))
        elif "/api/authors/" in remote_id and "/posts/" in remote_id:
            public_path = remote_id.replace("/api/authors/", "/api/public/authors/") + "/likes"
            api_path = remote_id + "/likes"
            html_path = remote_id.replace("/api/authors/", "/authors/") + "/likes"
            urls.extend(_post_url_variants(public_path))
            urls.extend(_post_url_variants(api_path))
            urls.extend(_post_url_variants(html_path))
        elif "/authors/" in remote_id and "/entries/" in remote_id:
            public_path = remote_id.replace("/authors/", "/api/public/authors/") + "/likes"
            api_path = remote_id.replace("/authors/", "/api/authors/") + "/likes"
            html_path = remote_id + "/likes"
            urls.extend(_post_url_variants(public_path))
            urls.extend(_post_url_variants(api_path))
            urls.extend(_post_url_variants(html_path))
        elif "/authors/" in remote_id and "/posts/" in remote_id:
            public_path = remote_id.replace("/authors/", "/api/public/authors/") + "/likes"
            api_path = remote_id.replace("/authors/", "/api/authors/") + "/likes"
            html_path = remote_id + "/likes"
            urls.extend(_post_url_variants(public_path))
            urls.extend(_post_url_variants(api_path))
            urls.extend(_post_url_variants(html_path))
        else:
            urls.extend(_post_url_variants(remote_id + "/likes"))

    deduped = []
    seen = set()
    for value in urls:
        if value and value not in seen:
            deduped.append(value)
            seen.add(value)
    return deduped


def _auth_candidates_for_post(post):
    auth = _auth_for_node(post.node_url.rstrip("/")) if post.node_url else None
    return [auth, None] if auth else [None]


def _candidate_remote_author_urls(post):
    urls = []

    explicit = (getattr(post, "remote_author_url", "") or "").strip()
    if explicit:
        urls.extend(_url_variants(explicit))

    remote_id = str(post.remote_id or "").strip()

    marker = None
    if "/entries/" in remote_id:
        marker = "/entries/"
    elif "/posts/" in remote_id:
        marker = "/posts/"

    if marker and marker in remote_id:
        author_part = remote_id.split(marker)[0].rstrip("/")
        if author_part:
            urls.extend(_url_variants(author_part))

    expanded = []
    for url in urls:
        base = url.rstrip("/")
        expanded.extend(_url_variants(base))
        if "/api/authors/" in base:
            expanded.extend(_url_variants(base.replace("/api/authors/", "/authors/")))
            expanded.extend(_url_variants(base.replace("/api/authors/", "/authors/api/authors/")))
        elif "/authors/api/authors/" in base:
            expanded.extend(_url_variants(base.replace("/authors/api/authors/", "/authors/")))
            expanded.extend(_url_variants(base.replace("/authors/api/authors/", "/api/authors/")))
        elif "/authors/" in base:
            expanded.extend(_url_variants(base.replace("/authors/", "/api/authors/")))
            expanded.extend(_url_variants(base.replace("/authors/", "/authors/api/authors/")))

    deduped = []
    seen = set()
    for value in expanded:
        if value and value not in seen:
            deduped.append(value)
            seen.add(value)
    return deduped


def _candidate_remote_inbox_urls(post):
    inboxes = []
    for author_url in _candidate_remote_author_urls(post):
        inboxes.extend(_post_url_variants(f"{author_url.rstrip('/')}/inbox"))

    deduped = []
    seen = set()
    for value in inboxes:
        if value and value not in seen:
            deduped.append(value)
            seen.add(value)
    return deduped

def _fetch_remote_comments(post, viewer=None, include_like_state=True):
    data = None
    working_url = None

    candidate_urls = _candidate_remote_comments_urls(post)
    auth_candidates = _auth_candidates_for_post(post)

    for candidate_url in candidate_urls:
        for auth in auth_candidates:
            try:
                resp = requests.get(
                    candidate_url,
                    auth=auth,
                    timeout=5,
                    headers={"Accept": "application/json"},
                )
                if resp.status_code != 200:
                    continue

                try:
                    payload = resp.json()
                except Exception as e:
                    continue

                data = payload
                working_url = candidate_url
                break

            except Exception as e:
                continue

        if data is not None:
            break

    if data is None:
        return []

    # support more response shapes
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = (
            data.get("src")
            or data.get("items")
            or data.get("comments")
            or data.get("results")
            or data.get("data")
            or []
        )

        # sometimes comments are nested one level deeper
        if isinstance(items, dict):
            items = (
                items.get("src")
                or items.get("items")
                or items.get("comments")
                or items.get("results")
                or []
            )
    else:
        items = []

    if not isinstance(items, list):
        return []

    normalized = []

    for raw in items:
        if not isinstance(raw, dict):
            continue

        author = raw.get("author", {}) if isinstance(raw.get("author"), dict) else {}
        likes_obj = raw.get("likes") if isinstance(raw.get("likes"), dict) else {}

        comment_id = str(raw.get("id") or "").strip()

        comment_likes_url = (
            likes_obj.get("id")
            or likes_obj.get("url")
            or ""
        )

        normalized.append({
            "id": comment_id,
            "comment": raw.get("comment") or raw.get("content") or "",
            "content_type": raw.get("contentType") or raw.get("content_type") or "text/plain",
            "published": raw.get("published") or raw.get("created") or "",
            "author_name": (
                author.get("displayName")
                or author.get("username")
                or author.get("name")
                or "Remote Author"
            ),
            "author_id": (
                author.get("id")
                or author.get("url")
                or ""
            ),
            "like_count": likes_obj.get("count", 0),
            "likes_url": comment_likes_url,
            "liked_by_me": False,
        })
    return normalized

def _fetch_remote_likes(post):
    data = None
    working_url = None

    candidate_urls = _candidate_remote_likes_urls(post)
    auth_candidates = _auth_candidates_for_post(post)

    for likes_url in candidate_urls:
        for auth in auth_candidates:
            try:
                resp = requests.get(
                    likes_url,
                    auth=auth,
                    timeout=5,
                    headers={"Accept": "application/json"},
                )

                if resp.status_code != 200:
                    continue

                try:
                    payload = resp.json()
                except Exception as e:
                    continue

                data = payload
                working_url = likes_url
                break

            except Exception as e:
                continue

        if data is not None:
            break

    if not data:
        return []

    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = (
            data.get("src")
            or data.get("items")
            or data.get("likes")
            or data.get("results")
            or []
        )
    else:
        items = []

    if not isinstance(items, list):
        return []

    normalized = []
    for raw in items:
        if not isinstance(raw, dict):
            continue

        author = raw.get("author", {}) if isinstance(raw.get("author"), dict) else {}

        normalized.append({
            "id": raw.get("id") or "",
            "author_id": (
                author.get("id")
                or author.get("url")
                or raw.get("author_id")
                or ""
            ),
            "author": author,
            "author_name": (
                author.get("displayName")
                or author.get("username")
                or raw.get("author_name")
                or "Remote Author"
            ),
            "summary": raw.get("summary", ""),
            "published": raw.get("published", ""),
            "object": raw.get("object", ""),
            "source_likes_url": working_url or "",
        })
    return normalized

def _send_remote_comment(user, post, text):
    comment_id = str(uuid.uuid4())
    base_url = _site_url()

    payload = {
        "type": "comment",
        "id": f"{base_url}/api/authors/{user.id}/commented/{comment_id}",
        "author": _local_author_payload(user),
        "comment": text,
        "content": text,
        "contentType": "text/plain",
        "object": str(post.remote_id or ""),
        "entry": str(post.remote_id or ""),
        "published": timezone.now().isoformat(),
    }

    for comments_url in _candidate_remote_comments_urls(post):
        for auth in _auth_candidates_for_post(post):
            try:
                resp = requests.post(
                    comments_url,
                    json=payload,
                    auth=auth,
                    timeout=5,
                    allow_redirects=False,
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                )
                if resp.status_code in [200, 201, 202, 204, 409]:
                    return True
            except Exception:
                continue

    for inbox_url in _candidate_remote_inbox_urls(post):
        for auth in _auth_candidates_for_post(post):
            try:
                resp = requests.post(
                    inbox_url,
                    json=payload,
                    auth=auth,
                    timeout=5,
                    allow_redirects=False,
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                )
                if resp.status_code in [200, 201, 202, 204, 409]:
                    return True
            except Exception:
                continue

    return False


def _send_remote_like(user, post):
    like_id = str(uuid.uuid4())
    payload = {
        "type": "like",
        "id": f"{_site_url()}/api/authors/{user.id}/liked/{like_id}",
        "author": _local_author_payload(user),
        "object": post.remote_id,
        "published": timezone.now().isoformat(),
    }

    for likes_url in _candidate_remote_likes_urls(post):
        for auth in _auth_candidates_for_post(post):
            try:
                resp = requests.post(
                    likes_url,
                    json=payload,
                    auth=auth,
                    timeout=5,
                    allow_redirects=False,
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                )

                if resp.status_code in [200, 201, 202, 204, 409]:
                    return True
            except Exception as e:
                continue

    for inbox_url in _candidate_remote_inbox_urls(post):
        for auth in _auth_candidates_for_post(post):
            try:
                resp = requests.post(
                    inbox_url,
                    json=payload,
                    auth=auth,
                    timeout=5,
                    allow_redirects=False,
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                )
                if resp.status_code in [200, 201, 202, 204, 409]:
                    return True
            except Exception as e:
                continue

    return False


def _send_remote_comment_like(user, post, remote_comment_id, remote_likes_url=""):
    like_id = str(uuid.uuid4())
    likes_url = (remote_likes_url or "").strip()
    comment_object = (remote_comment_id or "").strip()

    if not likes_url and comment_object:
        if comment_object.startswith("http://") or comment_object.startswith("https://"):
            likes_url = f"{comment_object.rstrip('/')}/likes/"
        else:
            remote_id = str(post.remote_id or "").rstrip("/")
            if "/api/authors/" in remote_id and "/posts/" in remote_id:
                comments_base = remote_id.replace("/api/authors/", "/api/public/authors/") + "/comments"
            else:
                comments_base = remote_id + "/comments"
            likes_url = f"{comments_base.rstrip('/')}/{comment_object}/likes/"

    if not likes_url:
        return False

    if not comment_object:
        # Derive object from likes endpoint for remote servers that validate Like.object.
        comment_object = likes_url.rstrip("/").replace("/likes", "")

    payload = {
        "type": "like",
        "id": f"{_site_url()}/api/authors/{user.id}/liked/{like_id}",
        "author": _local_author_payload(user),
        "object": comment_object,
        "published": timezone.now().isoformat(),
    }

    like_urls = _post_url_variants(likes_url)
    auth_candidates = _auth_candidates_for_post(post)

    for candidate_url in like_urls:
        for auth in auth_candidates:
            try:
                resp = requests.post(
                    candidate_url,
                    json=payload,
                    auth=auth,
                    timeout=5,
                    allow_redirects=False,
                    headers={
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                )
                if resp.status_code in [200, 201, 202, 204, 409]:
                    return True
            except Exception:
                continue

    return False

# ---------- Stream ----------

@login_required
def stream(request):
    pending_remote_comments = request.session.get("pending_remote_comments", {})
    user = request.user
    allowed_remote_nodes = _active_remote_nodes()
    liked_remote_posts = set(request.session.get("liked_remote_posts", []))

    try:
        _fetch_remote_public_posts()
    except Exception:
        pass

    following_ids = set(Follower.objects.filter(
        follower=user,
        status="accepted",
    ).values_list("following_id", flat=True))

    follower_ids = set(Follower.objects.filter(
        following=user,
        status="accepted",
    ).values_list("follower_id", flat=True))

    friend_ids = following_ids.intersection(follower_ids)

    remote_following_author_urls = set()
    remote_friend_author_urls = set()

    for remote_id in (
        Author.objects.filter(id__in=following_ids, is_remote=True)
        .exclude(remote_id__isnull=True)
        .exclude(remote_id="")
        .values_list("remote_id", flat=True)
    ):
        remote_following_author_urls.update(_author_url_variants(remote_id))

    for remote_id in (
        Author.objects.filter(id__in=friend_ids, is_remote=True)
        .exclude(remote_id__isnull=True)
        .exclude(remote_id="")
        .values_list("remote_id", flat=True)
    ):
        remote_friend_author_urls.update(_author_url_variants(remote_id))

    all_posts = list(
        Post.objects.filter(deleted=False)
        .exclude(title__startswith="GitHub Activity:")
        .filter(
            Q(is_remote=False, author=user)
            | Q(is_remote=False, visibility=Post.Visibility.PUBLIC)
            | Q(
                is_remote=False,
                author_id__in=following_ids,
                visibility=Post.Visibility.UNLISTED,
            )
            | Q(
                is_remote=False,
                author_id__in=friend_ids,
                visibility=Post.Visibility.FRIENDS,
            )
            | Q(is_remote=True, visibility=Post.Visibility.PUBLIC, node_url__in=allowed_remote_nodes)
            | Q(
                is_remote=True,
                remote_author_url__in=remote_following_author_urls,
                visibility=Post.Visibility.UNLISTED,
                node_url__in=allowed_remote_nodes,
            )
            | Q(
                is_remote=True,
                remote_author_url__in=remote_friend_author_urls,
                visibility=Post.Visibility.FRIENDS,
                node_url__in=allowed_remote_nodes,
            )
        )
        .prefetch_related("comments__author", "comments__likes", "likes")
        .select_related("author")
        .order_by("-published", "-created")
    )

    all_posts.sort(key=lambda p: (p.effective_published or p.created), reverse=True)

    local_posts = [p for p in all_posts if not p.is_remote]

    post_liked_ids = set(
        Like.objects.filter(
            author=user,
            post__in=local_posts
        ).values_list("post_id", flat=True)
    )
    comment_liked_ids = set(
        Like.objects.filter(
            author=user,
            comment__post__in=local_posts
        ).values_list("comment_id", flat=True)
    )

    refreshed_remote = 0
    max_remote_refresh = int(getattr(settings, "FEDERATION_STREAM_REMOTE_REFRESH", 8) or 8)

    for p in all_posts:
        if p.is_remote:
            _sanitize_cached_remote_post(p)

        if p.content_type == Post.ContentType.MARKDOWN:
            p.rendered = md.markdown(p.content or "", extensions=["extra"])
        else:
            p.rendered = None

        if p.is_remote:
            p.comment_list = []
            p.remote_comment_list = []
            p.remote_like_list = []

            pending_for_post = pending_remote_comments.get(str(p.remote_id), [])

            if refreshed_remote < max_remote_refresh:

                # FIX: BEFORE fetching comments/likes, check the post itself
                # to see if the remote changed its visibility.
                # This catches PUBLIC->FRIENDS changes even when the remote
                # still serves comments/likes on public endpoints.
                current_visibility = _check_remote_post_visibility(p)

                if current_visibility is not None and current_visibility != Post.Visibility.PUBLIC:
                    # Remote says this post is now FRIENDS or UNLISTED.
                    # Update our cache and hide it.
                    Post.objects.filter(id=p.id).update(visibility=current_visibility)
                    p._hide_from_stream = True
                    refreshed_remote += 1
                    continue

                if current_visibility is False:
                    # Remote denied access (403/401) or post deleted (404)
                    p._hide_from_stream = True
                    refreshed_remote += 1
                    continue

                # If post is FRIENDS in our cache, also hide
                if p.visibility == Post.Visibility.FRIENDS:
                    if current_visibility is None:
                        # Couldn't reach the remote — hide to be safe
                        p._hide_from_stream = True
                        refreshed_remote += 1
                        continue

                remote_comments = _fetch_remote_comments(p, viewer=user, include_like_state=False)
                remote_likes = _fetch_remote_likes(p)

                merged_comments = list(remote_comments)

                existing_texts = {
                    (c.get("comment", "").strip(), c.get("author_name", "").strip())
                    for c in remote_comments
                    if isinstance(c, dict)
                }

                for pending in pending_for_post:
                    key = (
                        pending.get("comment", "").strip(),
                        pending.get("author_name", "").strip(),
                    )
                    if key not in existing_texts:
                        merged_comments.insert(0, pending)

                p.remote_comment_list = merged_comments[:3]
                p.remote_like_list = remote_likes
                p.comment_count = len(merged_comments)
                p.like_count = len(remote_likes)
                p.liked_by_me = (
                    str(p.remote_id) in liked_remote_posts
                    or any(_remote_like_entry_matches_user(item, user) for item in remote_likes)
                )
                refreshed_remote += 1
            else:
                # Beyond refresh limit — hide FRIENDS posts since we can't verify
                if p.visibility == Post.Visibility.FRIENDS:
                    p._hide_from_stream = True
                    continue
                p.remote_comment_list = list(pending_for_post)[:3]
                p.comment_count = len(pending_for_post)
                p.like_count = 0
                p.liked_by_me = str(p.remote_id) in liked_remote_posts

    # Remove posts that failed the visibility/access check
    all_posts = [p for p in all_posts if not getattr(p, '_hide_from_stream', False)]

    return render(
        request,
        "posts/stream.html",
        {
            "posts": all_posts,
            "feed_title": "Public Stream",
        },
    )

# ---------- Detail ----------
def detail(request, post_id):
    if request.user.is_superuser:
        post = get_object_or_404(Post, id=post_id)
    else:
        post = get_object_or_404(Post, id=post_id, deleted=False)

    if post.is_remote:
        if not _is_post_from_active_remote_node(post):
            return HttpResponseForbidden("Remote node is not connected.")
        _sanitize_cached_remote_post(post)

    if post.content_type == Post.ContentType.MARKDOWN:
        rendered = _render_markdown(post.content)
    else:
        rendered = None

    if post.is_remote:
        # FIX: Check visibility BEFORE fetching comments/likes
        current_visibility = _check_remote_post_visibility(post)

        if current_visibility is False:
            # Remote denied access or post deleted
            Post.objects.filter(id=post.id).update(deleted=True)
            raise Http404()

        if current_visibility is not None and current_visibility == Post.Visibility.FRIENDS:
            Post.objects.filter(id=post.id).update(visibility=Post.Visibility.FRIENDS)
            return HttpResponseForbidden("This post is now friends-only.")

        if current_visibility is not None and current_visibility != post.visibility:
            # Update cache with new visibility
            Post.objects.filter(id=post.id).update(visibility=current_visibility)

        remote_comments = _fetch_remote_comments(post, viewer=request.user)
        remote_likes = _fetch_remote_likes(post)

        post_liked_by_me = False
        if request.user.is_authenticated:
            local_ids = {
                _normalize_author_id(f"{_site_url()}/api/authors/{request.user.id}"),
                _normalize_author_id(f"{_site_url()}/authors/{request.user.id}"),
            }
            post_liked_by_me = any(
                _normalize_author_id(l.get("author_id")) in local_ids
                for l in remote_likes
                if isinstance(l, dict)
            )

        return render(
            request,
            "posts/detail.html",
            {
                "post": post,
                "rendered": rendered,
                "comments": [],
                "remote_comments": remote_comments,
                "remote_likes": remote_likes,
                "post_liked_by_me": post_liked_by_me,
            },
        )

    # local post logic
    if post.visibility == Post.Visibility.PUBLIC:
        pass
    elif post.visibility == Post.Visibility.UNLISTED:
        pass
    elif post.visibility == Post.Visibility.FRIENDS:
        if not request.user.is_authenticated:
            return HttpResponseForbidden("Login required.")
        if (
            request.user != post.author
            and not _is_friend(request.user, post.author)
            and not post.comments.filter(author=request.user).exists()
        ):
            return HttpResponseForbidden("Not allowed.")
    else:
        return HttpResponseForbidden("Invalid visibility.")

    comments = _visible_comments_for_viewer(request.user, post)
    comment_liked_ids = set()
    post_liked_by_me = False

    if request.user.is_authenticated:
        comment_liked_ids = set(
            Like.objects.filter(author=request.user, comment__post=post).values_list("comment_id", flat=True)
        )
        post_liked_by_me = Like.objects.filter(author=request.user, post=post).exists()

    for c in comments:
        c.like_count = c.likes.count()
        c.liked_by_me = c.id in comment_liked_ids

    return render(
        request,
        "posts/detail.html",
        {
            "post": post,
            "rendered": rendered,
            "comments": comments,
            "remote_comments": [],
            "remote_likes": [],
            "post_liked_by_me": post_liked_by_me,
        },
    )
# ---------- Comment ----------

@login_required
def add_comment(request, post_id):
    post = get_object_or_404(Post, id=post_id, deleted=False)

    if request.method != "POST":
        raise Http404()

    text = (request.POST.get("comment") or "").strip()
    if not text:
        next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or redirect("posts:detail", post_id=post.id).url
        return redirect(next_url)

    if post.is_remote:
        if not _is_post_from_active_remote_node(post):
            return HttpResponseForbidden("Remote node is not connected.")

        ok = _send_remote_comment(request.user, post, text)
        if not ok:
            return HttpResponseForbidden("Could not send remote comment.")

        pending_remote_comments = request.session.get("pending_remote_comments", {})
        post_key = str(post.remote_id)

        existing = pending_remote_comments.get(post_key, [])
        existing.append({
            "id": f"local-pending-{uuid.uuid4()}",
            "comment": text,
            "content_type": "text/plain",
            "published": timezone.now().isoformat(),
            "author_name": getattr(request.user, "displayName", "") or getattr(request.user, "username", "You"),
            "author_id": str(request.user.id),
            "like_count": 0,
            "likes_url": "",
            "liked_by_me": False,
            "pending": True,
        })
        pending_remote_comments[post_key] = existing
        request.session["pending_remote_comments"] = pending_remote_comments

    else:
        if not _can_interact_with_post(request.user, post):
            return HttpResponseForbidden("Not allowed.")

        Comment.objects.create(
            post=post,
            author=request.user,
            comment=text,
            content_type=Comment.ContentType.PLAIN,
        )

    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or redirect("posts:detail", post_id=post.id).url
    return redirect(next_url)

# ---------- Superuser helper ----------

def superuser_required(user):
    return user.is_superuser


@user_passes_test(superuser_required)
def author_posts(request, author_id):
    author = get_object_or_404(Author, id=author_id)
    if author.is_remote and author.remote_id:
        try:
            _fetch_remote_public_posts()
        except Exception:
            pass

        rid = author.remote_id.rstrip("/")
        rid_html = rid.replace("/authors/api/authors/", "/authors/").rstrip("/")
        rid_api = rid.replace("/authors/", "/authors/api/authors/").rstrip("/")
        remote_ids = [
            rid,
            rid + "/",
            rid_html,
            rid_html + "/",
            rid_api,
            rid_api + "/",
        ]
        posts = Post.objects.filter(
            is_remote=True,
            deleted=False,
            remote_author_url__in=remote_ids,
        ).order_by("-published", "-created")
    else:
        posts = Post.objects.filter(author=author)

    for post in posts:
        if post.is_remote:
            _sanitize_cached_remote_post(post)
    return render(request, "posts/author_posts.html", {"author": author, "posts": posts})


# ---------- Like post ----------

@login_required
def like_post(request, post_id):
    post = get_object_or_404(Post, id=post_id, deleted=False)

    if post.is_remote:
        if not _is_post_from_active_remote_node(post):
            return HttpResponseForbidden("Remote node is not connected.")

        ok = _send_remote_like(request.user, post)
        if not ok:
            return HttpResponseForbidden("Could not send remote like.")

        liked_remote_posts = set(request.session.get("liked_remote_posts", []))
        liked_remote_posts.add(str(post.remote_id))
        request.session["liked_remote_posts"] = list(liked_remote_posts)

    else:
        if not _can_interact_with_post(request.user, post):
            return HttpResponseForbidden("Not allowed.")

        Like.objects.get_or_create(author=request.user, post=post)

    next_url = (
        request.POST.get("next")
        or request.META.get("HTTP_REFERER")
        or redirect("posts:detail", post_id=post.id).url
    )
    return redirect(next_url)


# ---------- Like comment ----------

@login_required
def like_comment(request, post_id, comment_id):
    post = get_object_or_404(Post, id=post_id, deleted=False)

    if request.method != "POST":
        raise Http404()

    if post.is_remote:
        return HttpResponseForbidden("Use remote comment like endpoint.")

    comment = get_object_or_404(Comment, id=comment_id, post=post)

    if not _can_interact_with_post(request.user, post):
        return HttpResponseForbidden("Not allowed.")

    existing = Like.objects.filter(author=request.user, comment=comment).first()
    if existing:
        existing.delete()
    else:
        Like.objects.create(author=request.user, comment=comment)

    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or redirect("posts:detail", post_id=post.id).url
    return redirect(next_url)


@login_required
def like_remote_comment(request, post_id):
    post = get_object_or_404(Post, id=post_id, deleted=False)

    if request.method != "POST":
        raise Http404()

    if not post.is_remote:
        return HttpResponseForbidden("Not a remote post.")

    if not _is_post_from_active_remote_node(post):
        return HttpResponseForbidden("Remote node is not connected.")

    remote_comment_id = (request.POST.get("remote_comment_id") or "").strip()
    remote_likes_url = (request.POST.get("remote_likes_url") or "").strip()

    ok = _send_remote_comment_like(request.user, post, remote_comment_id, remote_likes_url)
    if not ok:
        return HttpResponseForbidden("Could not send remote comment like.")

    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or redirect("posts:detail", post_id=post.id).url
    return redirect(next_url)

def _remote_api_author_base(author_obj):
    remote_id = (author_obj.remote_id or "").strip().rstrip("/")
    if not remote_id:
        return ""

    if "/api/authors/" in remote_id:
        return remote_id

    if "/authors/" in remote_id:
        return remote_id.replace("/authors/", "/api/authors/")

    return remote_id


def _remote_inbox_url_for_author(author_obj):
    api_base = _remote_api_author_base(author_obj)
    if not api_base:
        return ""
    return f"{api_base}/inbox/"


def _post_to_activity_object(post):
    return {
        "type": "entry",
        "id": post.remote_id,
        "title": post.title,
        "contentType": post.content_type,
        "content": post.content,
        "visibility": post.visibility,
        "published": (post.published or post.created).isoformat(),
        "author": _local_author_payload(post.author),
    }


def _send_post_to_remote_inbox(remote_author, post):
    inbox_url = _remote_inbox_url_for_author(remote_author)
    if not inbox_url:
        return False

    node_base = ""
    remote_id = (remote_author.remote_id or "").strip()
    if remote_id.startswith("http://") or remote_id.startswith("https://"):
        parts = remote_id.split("/")
        if len(parts) >= 3:
            node_base = f"{parts[0]}//{parts[2]}"

    auth = _auth_for_node(node_base.rstrip("/")) if node_base else None
    payload = _post_to_activity_object(post)

    try:
        resp = requests.post(
            inbox_url,
            json=payload,
            auth=auth,
            timeout=5,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        return resp.status_code in [200, 201, 202]
    except Exception:
        return False


def _candidate_node_inbox_urls(node_base):
    base = (node_base or "").rstrip("/")
    if not base:
        return []
    return [
        f"{base}/api/inbox/",
        f"{base}/api/inbox",
        f"{base}/inbox/",
        f"{base}/inbox",
    ]


def _push_post_to_remote_recipients(post):
    """
    Push a created/updated post to remote recipients.

    PUBLIC:
      - push to known remote followers/friends
      - also optionally fan out to node-level inbox guesses

    UNLISTED:
      - push to people the author follows (remote)

    FRIENDS:
      - push only to mutual friends (remote)
    """
    # Who does the author follow?
    following_ids = set(
        Follower.objects.filter(
            follower=post.author,
            status="accepted",
        ).values_list("following_id", flat=True)
    )

    # Who follows the author?
    follower_ids = set(
        Follower.objects.filter(
            following=post.author,
            status="accepted",
        ).values_list("follower_id", flat=True)
    )

    mutual_friend_ids = following_ids.intersection(follower_ids)

    target_ids = set()

    if post.visibility == Post.Visibility.PUBLIC:
        target_ids.update(following_ids)
        target_ids.update(follower_ids)
        target_ids.update(mutual_friend_ids)
    elif post.visibility == Post.Visibility.UNLISTED:
        target_ids.update(following_ids)
    elif post.visibility == Post.Visibility.FRIENDS:
        target_ids.update(mutual_friend_ids)

    # Push to remote authors only
    recipients = (
        Author.objects.filter(id__in=target_ids, is_remote=True)
        .exclude(remote_id__isnull=True)
        .exclude(remote_id="")
        .distinct()
    )

    for remote_author in recipients:
        _send_post_to_remote_inbox(remote_author, post)

    # Optional fanout for PUBLIC posts so other nodes can cache/update it.
    if post.visibility == Post.Visibility.PUBLIC:
        payload = _post_to_activity_object(post)

        for node_url in get_configured_nodes(exclude_local=True):
            node_base = (node_url or "").rstrip("/")
            if not node_base:
                continue

            auth = _auth_for_node(node_base)

            for inbox_url in _candidate_node_inbox_urls(node_base):
                if _try_post_json(inbox_url, payload, auth=auth, timeout=5):
                    break


def _push_deleted_post_to_remote_recipients(post):
    """
    Notify remote nodes that a post was deleted.

    For PUBLIC posts:
        notify all configured remote nodes, because any node may have cached it.

    For FRIENDS / UNLISTED posts:
        notify only relevant remote followers/friends.
    """
    remote_targets = []

    if post.visibility == Post.Visibility.PUBLIC:
        # Any connected node may have cached a public post
        for node_url in get_configured_nodes(exclude_local=True):
            node_url = (node_url or "").rstrip("/")
            if node_url:
                remote_targets.append({
                    "node_url": node_url,
                    "inbox_url": None,  # may derive later if needed
                })
    else:
        # Keep narrower targeting for non-public posts
        following_ids = set(
            Follower.objects.filter(
                follower=post.author,
                status="accepted",
            ).values_list("following_id", flat=True)
        )

        follower_ids = set(
            Follower.objects.filter(
                following=post.author,
                status="accepted",
            ).values_list("follower_id", flat=True)
        )

        mutual_friend_ids = following_ids.intersection(follower_ids)

        recipients = Author.objects.filter(
            Q(id__in=following_ids) | Q(id__in=mutual_friend_ids),
            is_remote=True,
        ).exclude(remote_id__isnull=True).exclude(remote_id="").distinct()

        for remote_author in recipients:
            remote_id = (remote_author.remote_id or "").strip()
            if not remote_id:
                continue

            node_base = ""
            if remote_id.startswith("http://") or remote_id.startswith("https://"):
                parts = remote_id.split("/")
                if len(parts) >= 3:
                    node_base = f"{parts[0]}//{parts[2]}".rstrip("/")

            inbox_url = _remote_inbox_url_for_author(remote_author)
            remote_targets.append({
                "node_url": node_base,
                "inbox_url": inbox_url,
            })

    # Deduplicate by (node_url, inbox_url)
    deduped = []
    seen = set()
    for target in remote_targets:
        key = ((target.get("node_url") or "").rstrip("/"), target.get("inbox_url") or "")
        if key in seen:
            continue
        seen.add(key)
        deduped.append(target)

    # Send both styles:
    # 1) "entry" with deleted=True for nodes already using that convention
    # 2) "delete" activity for nodes expecting a delete/tombstone-style signal
    entry_payload = {
        "type": "entry",
        "id": post.remote_id,
        "title": post.title,
        "contentType": post.content_type,
        "content": post.content,
        "visibility": post.visibility,
        "deleted": True,
        "published": (post.published or post.created).isoformat(),
        "author": _local_author_payload(post.author),
    }

    delete_payload = {
        "type": "delete",
        "object": post.remote_id,
        "author": _local_author_payload(post.author),
        "published": timezone.now().isoformat(),
    }

    for target in deduped:
        node_base = (target.get("node_url") or "").rstrip("/")
        inbox_url = target.get("inbox_url") or ""

        auth = _auth_for_node(node_base) if node_base else None

        candidate_urls = []
        if inbox_url:
            candidate_urls.extend(_post_url_variants(inbox_url))
        elif node_base:
            # fallback guesses for node-level inbox-ish endpoints if no author inbox known
            candidate_urls.extend(_post_url_variants(f"{node_base}/api/inbox"))
            candidate_urls.extend(_post_url_variants(f"{node_base}/api/inbox/"))

        for url in candidate_urls:
            for payload in (delete_payload, entry_payload):
                try:
                    resp = requests.post(
                        url,
                        json=payload,
                        auth=auth,
                        timeout=5,
                        headers={
                            "Accept": "application/json",
                            "Content-Type": "application/json",
                        },
                    )

                    if resp.status_code in [200, 201, 202, 204, 409]:
                        break
                except Exception as e:
                    continue
# ---------- Create ----------

@login_required
def create(request):
    if request.method == "POST":
        form = PostForm(request.POST, request.FILES)
        if form.is_valid():
            post = form.save(commit=False)
            post.author = request.user
            post.is_remote = False
            post.node_url = _site_url() or None
            post.remote_author_url = None
            post.remote_author_name = None
            post.remote_author_host = None
            post.published = timezone.now()
            post.save()

            post.remote_id = f"{_site_url()}/api/authors/{request.user.id}/entries/{post.id}/"
            post.save(update_fields=["remote_id"])

            _push_post_to_remote_recipients(post)

            return redirect("posts:stream")
    else:
        form = PostForm()

    return render(request, "posts/create.html", {"form": form, "mode": "Create"})


# ---------- Edit ----------

# ---------- Edit ----------

@login_required
def edit(request, post_id):
    post = get_object_or_404(Post, id=post_id, deleted=False)

    if post.is_remote:
        raise Http404()

    if post.author_id != request.user.id:
        raise Http404()

    if request.method == "POST":
        form = PostForm(request.POST, request.FILES, instance=post)
        if form.is_valid():
            updated = form.save(commit=False)
            updated.updated = timezone.now()
            updated.save()

            if not updated.remote_id:
                updated.remote_id = f"{_site_url()}/api/authors/{request.user.id}/entries/{updated.id}/"
                updated.save(update_fields=["remote_id"])

            _push_post_to_remote_recipients(updated)
            return redirect("posts:stream")
    else:
        form = PostForm(instance=post)

    return render(request, "posts/create.html", {"form": form, "mode": "Edit", "post": post})


# ---------- Delete ----------

@login_required
def delete(request, post_id):
    post = get_object_or_404(Post, id=post_id, deleted=False)

    if post.is_remote:
        raise Http404()

    if post.author_id != request.user.id:
        raise Http404()

    if request.method == "POST":
        post.deleted = True
        post.updated = timezone.now()
        post.save(update_fields=["deleted", "updated"])

        _push_deleted_post_to_remote_recipients(post)

        return redirect("posts:stream")

    return render(request, "posts/delete_confirm.html", {"post": post})


# ---------- Followers / friends feed ----------

@login_required
def followers_feed(request):
    author = request.user
    allowed_remote_nodes = _active_remote_nodes()

    following_ids = set(
        Follower.objects.filter(
            follower=author,
            status="accepted",
        ).values_list("following", flat=True)
    )

    follower_ids = set(
        Follower.objects.filter(
            following=author,
            status="accepted",
        ).values_list("follower", flat=True)
    )

    friend_ids = following_ids.intersection(follower_ids)

    remote_friend_urls = set()
    remote_following_urls = set()

    for remote_id in (
        Author.objects.filter(id__in=friend_ids, is_remote=True)
        .exclude(remote_id__isnull=True)
        .exclude(remote_id="")
        .values_list("remote_id", flat=True)
    ):
        remote_friend_urls.update(_author_url_variants(remote_id))

    for remote_id in (
        Author.objects.filter(id__in=following_ids, is_remote=True)
        .exclude(remote_id__isnull=True)
        .exclude(remote_id="")
        .values_list("remote_id", flat=True)
    ):
        remote_following_urls.update(_author_url_variants(remote_id))

    posts = Post.objects.filter(deleted=False).exclude(title__startswith="GitHub").filter(
        Q(author_id__in=following_ids, visibility=Post.Visibility.UNLISTED, is_remote=False)
        | Q(author_id__in=friend_ids, visibility=Post.Visibility.FRIENDS, is_remote=False)
        | Q(author_id=author.id, is_remote=False)
        | Q(
            is_remote=True,
            remote_author_url__in=remote_following_urls,
            visibility=Post.Visibility.UNLISTED,
            node_url__in=allowed_remote_nodes,
        )
        | Q(
            is_remote=True,
            remote_author_url__in=remote_friend_urls,
            visibility=Post.Visibility.FRIENDS,
            node_url__in=allowed_remote_nodes,
        )
    ).order_by("-published", "-created")

    local_posts = [p for p in posts if not p.is_remote]

    post_liked_ids = set(
        Like.objects.filter(author=author, post__in=local_posts).values_list("post_id", flat=True)
    )
    comment_liked_ids = set(
        Like.objects.filter(author=author, comment__post__in=local_posts).values_list("comment_id", flat=True)
    )

    # FIX: Apply same visibility gate for followers_feed remote FRIENDS posts
    posts_list = list(posts)

    for p in posts_list:
        if p.content_type == Post.ContentType.MARKDOWN:
            p.rendered = md.markdown(p.content or "", extensions=["extra"])
        else:
            p.rendered = None

        if p.is_remote:
            # For remote FRIENDS posts in the friends feed, do a live check
            if p.visibility == Post.Visibility.FRIENDS:
                remote_comments = _fetch_remote_comments(p, viewer=author, include_like_state=False)
                remote_likes = _fetch_remote_likes(p)

                if not remote_comments and not remote_likes:
                    p._hide_from_stream = True
                    continue

                p.remote_comment_list = remote_comments[:3]
                p.remote_like_list = remote_likes
                p.comment_count = len(remote_comments)
                p.like_count = len(remote_likes)
                p.liked_by_me = False
                p.comment_list = []
            else:
                p.remote_comment_list = []
                p.remote_like_list = []
                p.comment_count = int(getattr(p, "remote_comment_count", 0) or 0)
                p.like_count = int(getattr(p, "remote_like_count", 0) or 0)
                p.liked_by_me = False
                p.comment_list = []
        else:
            p.like_count = p.likes.count()
            p.comment_count = p.comments.count()
            p.comment_list = list(_visible_comments_for_viewer(author, p)[:3])
            p.liked_by_me = p.id in post_liked_ids
            for c in p.comment_list:
                c.liked_by_me = c.id in comment_liked_ids

    # FIX: remove posts that failed the visibility gate
    posts_list = [p for p in posts_list if not getattr(p, '_hide_from_stream', False)]

    return render(
        request,
        "posts/stream.html",
        {
            "posts": posts_list,
            "feed_title": "Friends Feed",
        },
    )