import base64

from django.contrib.auth import get_user_model
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from node.models import Node

Author = get_user_model()


class NodeAuthentication(BaseAuthentication):
    def authenticate(self, request):
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Basic "):
            return None

        # Decode the Basic Auth header
        try:
            auth_decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
            username, password = auth_decoded.split(":", 1)
        except Exception:
            raise AuthenticationFailed("Invalid Basic Auth format.")

        # 1. Check if these credentials belong to an ACTIVE Node
        node = Node.objects.filter(
            auth_username=username,
            auth_password=password,
            is_active=True,  # THIS IS THE MAGIC FILTER!
        ).first()

        if node:
            # We must return a Django User object for DRF to be happy.
            # We will grab a proxy/remote Author to represent this node,
            # or you can just return an anonymous user if your views don't need a specific local author.
            proxy_user, _ = Author.objects.get_or_create(
                username=f"node_{username}",
                defaults={"is_remote": True, "displayName": f"Node {username}"},
            )
            return (proxy_user, None)

        # 2. If it's not a node, let DRF fall back to checking normal Authors (optional)
        return None
