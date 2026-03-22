from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from django.conf import settings
from django.db.models import Q
from django.core.paginator import EmptyPage, Paginator
from django.shortcuts import get_object_or_404
import requests

from .models import Author, Follower
from .serializers import AuthorSerializer
from django.http import JsonResponse
from posts.models import Post


# ---------------------------------------------------
# Get single author
# GET /authors/api/authors/<pk>/
# ---------------------------------------------------
@api_view(["GET"])
def api_get_author(request, pk):
    author=get_object_or_404(Author, pk=pk)
    serializer=AuthorSerializer(author)
    return Response(serializer.data)
#----------------------------------------------------------
# Search for author by name to add them
#GET /api/authors/?search=<name>
#GET /api/authors/?
#GET /api/authors/?page=<int>&size=<int>
#----------------------------------------------------------

@api_view(["GET"])
def api_get_all_authors(request):
    query = request.GET.get("search", "").strip()
    include_remote_lookup = request.GET.get("_federated", "0") != "1"

    # Start with local authors only; federated lookup appends canonical remote results.
    authors = Author.objects.filter(is_remote=False)
    if query:
        authors = authors.filter(
            Q(username__icontains=query) |
            Q(displayName__icontains=query)
        )

    # PAGINATION
    try:
        page = int(request.GET.get("page", 1))
        size = int(request.GET.get("size", 5))
    except ValueError:
        page = 1
        size = 5

    base_url = request.build_absolute_uri("/").rstrip("/")

    seen_ids = set()
    items = []

    # Local and cached remote-proxy authors in this node DB.
    for author in authors:
        author_url = f"{base_url}/authors/api/authors/{author.id}"
        profile_image = ""
        if getattr(author, "profileImage", None):
            try:
                profile_image = request.build_absolute_uri(author.profileImage.url)
            except Exception:
                profile_image = ""

        if author_url in seen_ids:
            continue
        seen_ids.add(author_url)

        items.append({
            "type": "author",
            "id": author_url,
            "url": author_url,
            "host": base_url,
            "username": author.username,
            "displayName": author.displayName or author.username,
            "bio": author.bio or "",
            "github": author.github or "",
            "profileImage": profile_image,
            "is_approved": bool(author.is_approved),
        })

    # Federation lookup: include remote matches directly from peer nodes.
    if query and include_remote_lookup:
        for node in getattr(settings, "REMOTE_NODES", []):
            node = (node or "").rstrip("/")
            if not node:
                continue
            remote_url = f"{node}/authors/api/authors/?search={query}&_federated=1&page=1&size={size}"
            try:
                resp = requests.get(
                    remote_url,
                    timeout=5,
                    headers={"Accept": "application/json"},
                )
                if resp.status_code != 200:
                    continue
                remote_data = resp.json()
                for entry in remote_data.get("items", []):
                    remote_id = (entry.get("id") or "").strip()
                    if not remote_id or remote_id in seen_ids:
                        continue
                    seen_ids.add(remote_id)
                    entry["host"] = (entry.get("host") or node).rstrip("/")
                    items.append(entry)
            except Exception:
                continue

    paginator = Paginator(items, size)
    page_obj = paginator.get_page(page)

    return Response({
        "type": "authors",
        "count": paginator.count,
        "page": page,
        "size": size,
        "items": list(page_obj.object_list)
    })
# ---------------------------------------------------
# Follow author
# PUT /authors/api/authors/<pk>/follow/
# ---------------------------------------------------
@api_view(["PUT", "POST"])
@permission_classes([IsAuthenticated])
def api_follow_author(request, pk):
    follower=request.user
    following=get_object_or_404(Author, pk=pk)
    if follower == following:
        context={"error": "Cannot follow yourself"}
        return Response(context, status=status.HTTP_400_BAD_REQUEST)
    follow, created=Follower.objects.get_or_create(follower=follower,following=following)
    follow.status="pending"
    follow.save()
    context={"message": "Follow request sent"}
    return Response(context,status=status.HTTP_201_CREATED)
# ---------------------------------------------------
# Accept follow request
# POST /authors/api/authors/<pk>/accept/
# ---------------------------------------------------
@api_view(["POST", "PUT"])
@permission_classes([IsAuthenticated])
def api_accept_follow(request, pk):
    follower=get_object_or_404(Author, pk=pk)
    follow=get_object_or_404(Follower,follower=follower,following=request.user)
    follow.status="accepted"
    follow.save()
    context={"message": "Follow request accepted"}
    return Response(context,status=status.HTTP_200_OK)
# ---------------------------------------------------
# Reject follow request
# POST /authors/api/authors/<pk>/reject/
# ---------------------------------------------------
@api_view(["POST", "PUT"])
@permission_classes([IsAuthenticated])
def api_reject_follow(request, pk):
    follower=get_object_or_404(Author, pk=pk)
    follow=get_object_or_404(Follower,follower=follower,following=request.user)
    follow.status="rejected"
    follow.save()
    context={"message": "Follow request rejected"}
    return Response(context,status=status.HTTP_200_OK)
# ---------------------------------------------------
# Get following list
# GET /authors/api/authors/<pk>/following/
# ---------------------------------------------------
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def api_get_following(request, pk):
    author=get_object_or_404(Author, pk=pk)
    following=Follower.objects.filter(follower=author,status="accepted")
    authors=[f.following for f in following]
    serializer=AuthorSerializer(authors, many=True)
    count=len(serializer.data)
    items=serializer.data
    return Response({"type": "following","count": count,"items": items})
# ---------------------------------------------------
# Unfollow
# DELETE /authors/api/authors/<pk>/unfollow/
# ---------------------------------------------------
@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def api_unfollow(request, pk):
    author=request.user
    following=get_object_or_404(Author, pk=pk)
    Follower.objects.filter(follower=author,following=following).delete()
    context={"message": "Unfollowed"}
    return Response(context,status=status.HTTP_204_NO_CONTENT)
# ---------------------------------------------------
# Get friends (mutual followers)
# GET /authors/api/authors/<pk>/friends/
# ---------------------------------------------------
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def api_get_friends(request, pk):
    author=get_object_or_404(Author, pk=pk)
    following_ids=Follower.objects.filter(follower=author,status="accepted").values_list("following", flat=True)
    follower_ids=Follower.objects.filter(following=author,status="accepted").values_list("follower", flat=True)
    friends=Author.objects.filter(id__in=following_ids).filter(id__in=follower_ids)
    serializer=AuthorSerializer(friends, many=True)
    count=len(serializer.data)
    items=serializer.data
    return Response({"type": "friends","count": count,"items": items})