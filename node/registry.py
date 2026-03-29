from django.conf import settings
from urllib.parse import urlsplit


def _normalize(url):
    return (url or "").strip().rstrip("/")


def _node_auth_candidates(node_url):
    raw = _normalize(node_url)
    if not raw:
        return []

    candidates = [raw]
    try:
        parsed = urlsplit(raw)
        if parsed.scheme and parsed.netloc:
            base = f"{parsed.scheme}://{parsed.netloc}"
            candidates.append(base)
            if parsed.path:
                path = parsed.path.rstrip("/")
                if path:
                    candidates.append(f"{base}{path}")
    except Exception:
        pass

    deduped = []
    seen = set()
    for value in candidates:
        n = _normalize(value)
        if n and n not in seen:
            deduped.append(n)
            seen.add(n)
    return deduped


def get_configured_nodes(exclude_local=True):
    local_site = _normalize(getattr(settings, "SITE_URL", ""))
    seen = set()
    nodes = []

    try:
        from .models import Node

        qs = Node.objects.filter(is_active=True).values_list("host", flat=True)
        for host in qs:
            n = _normalize(host)
            if not n:
                continue
            if exclude_local and n == local_site:
                continue
            if n in seen:
                continue
            seen.add(n)
            nodes.append(n)
    except Exception:
        # During early startup or migrations, DB-backed node lookup can be unavailable.
        pass

    return nodes


def get_node_auth(node_url):
    candidates = _node_auth_candidates(node_url)
    if not candidates:
        return None

    try:
        from .models import Node

        for n in candidates:
            row = Node.objects.filter(host=n, is_active=True).only(
                "auth_username", "auth_password"
            ).first()
            if row and row.auth_username and row.auth_password:
                return (row.auth_username, row.auth_password)

            # Also try fuzzy host match in case DB host includes extra path segment.
            row = (
                Node.objects.filter(is_active=True)
                .filter(host__startswith=n)
                .only("auth_username", "auth_password")
                .first()
            )
            if row and row.auth_username and row.auth_password:
                return (row.auth_username, row.auth_password)
    except Exception:
        pass

    # Optional env-based credentials fallback for hosts not present in Node table.
    creds_map = getattr(settings, "REMOTE_NODE_CREDENTIALS", {}) or {}
    for n in candidates:
        entry = creds_map.get(n) or creds_map.get(f"{n}/")
        if isinstance(entry, dict):
            username = (entry.get("username") or "").strip()
            password = (entry.get("password") or "").strip()
            if username and password:
                return (username, password)

    # Restrict federation auth to explicitly registered Node Management entries.
    return None
