from django.conf import settings


def _normalize(url):
    return (url or "").strip().rstrip("/")


def get_configured_nodes(exclude_local=True):
    local_site = _normalize(getattr(settings, "SITE_URL", ""))
    seen = set()
    nodes = []

    for node in getattr(settings, "REMOTE_NODES", []) or []:
        n = _normalize(node)
        if not n:
            continue
        if exclude_local and n == local_site:
            continue
        if n in seen:
            continue
        seen.add(n)
        nodes.append(n)

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
    n = _normalize(node_url)
    if not n:
        return None

    try:
        from .models import Node

        row = Node.objects.filter(host=n, is_active=True).only(
            "auth_username", "auth_password"
        ).first()
        if row and row.auth_username and row.auth_password:
            return (row.auth_username, row.auth_password)
    except Exception:
        pass

    creds = getattr(settings, "REMOTE_NODE_CREDENTIALS", {}) or {}
    info = creds.get(n)
    if info and info.get("username") and info.get("password"):
        return (info["username"], info["password"])
    return None
