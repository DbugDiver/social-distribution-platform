from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from django.db.models import Q
from django.core.paginator import EmptyPage, Paginator
from django.shortcuts import get_object_or_404
import requests
from urllib.parse import unquote
from .models import Author, Follower
from .serializers import AuthorSerializer
from django.http import JsonResponse
from django.conf import settings
from posts.models import Post
from node.registry import get_configured_nodes


# ---------------------------------------------------
# Get single author
# GET /api/authors/<pk>/
# ---------------------------------------------------
@api_view(["GET"])
def api_get_author(request, pk):
    author=get_object_or_404(Author, pk=pk)
    #serializer=AuthorSerializer(author)
    #return Response(serializer.data)
    
    base = f"{request.scheme}://{request.get_host()}"

    return JsonResponse({
        "type": "author",
        "id": f"{base}/api/authors/{author.id}",
        "host": f"{base}/",
        "displayName": author.displayName or author.username,
        "github": author.github or "",
        "profileImage": author.profileImage or "",
        "web": f"{base}/authors/{author.id}"
    })
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
        author_url = f"{base_url}/api/authors/{author.id}"
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
        for node in get_configured_nodes(exclude_local=True):
            node = (node or "").rstrip("/")
            if not node:
                continue
            remote_url = f"{node}/api/authors/?search={query}&_federated=1&page=1&size={size}"
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
# Get following list
# GET /api/authors/<pk>/following/  -> return all users this auhtors follow 
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
# Follow author
# Get node_url/api/authors/following/foreign_id -> return Bool if they follow foreigh auhtor or not
# Delete node_url/api/authors/following/foreign_id - > sends unfollow request in view.py inbox
# post node_ur/api/authors/following/foreign_id -> post follow request to remote author and send in its inbox
# ---------------------------------------------------

@api_view(["GET", "PUT", "DELETE"])
@permission_classes([IsAuthenticated])
def api_follow_author(request, pk, foreign_id):
    follower = get_object_or_404(Author, pk=pk)

    # Decode percent-encoded URL
    decoded_id = unquote(foreign_id).rstrip("/")

    # Try to find existing author (local or remote)
    following = Author.objects.filter(
        remote_id=decoded_id
    ).first()

    # If not found → create remote author placeholder
    if not following:
        following = Author.objects.create(
            remote_id=decoded_id,
            displayName="Remote User",
            is_remote=True,
        )

    #  Cannot follow yourself
    if follower == following:
        return Response(
            {"error": "Cannot follow yourself"},
            status=status.HTTP_400_BAD_REQUEST
        )

    # =========================
    #  GET → check following
    # =========================
    if request.method == "GET":
        exists = Follower.objects.filter(
            follower=follower,
            following=following,
            status="accepted"
        ).exists()

        return Response(
            {"following": exists},
            status=status.HTTP_200_OK
        )

    # =========================
    #  PUT → follow request
    # =========================
    if request.method == "PUT":
        follow, created = Follower.objects.get_or_create(
            follower=follower,
            following=following
        )

        follow.status = "pending"
        follow.save()

        #  If remote → send to inbox
        if following.is_remote:
            try:
                #inbox_url = f"{decoded_id}/inbox/".replace("/authors/", "/api/authors/")
                inbox_url = decoded_id.rstrip("/") + "/inbox/"

                payload = {
                    "type": "Follow",
                    "actor": {
                        "type": "author",
                        "id": f"{request.scheme}://{request.get_host()}/api/authors/{follower.id}",
                        "displayName": follower.displayName,
                        "host": f"{request.scheme}://{request.get_host()}",
                    },
                    "object": {
                        "type": "author",
                        "id": decoded_id,
                    }
                }

                requests.post(inbox_url, json=payload, timeout=5)

            except Exception as e:
                print(" Failed to send to remote inbox:", e)

        return Response(
            {"message": "Follow request sent"},
            status=status.HTTP_201_CREATED
        )

    # =========================
    #  DELETE → unfollow
    # =========================
    if request.method == "DELETE":
        deleted, _ = Follower.objects.filter(
            follower=follower,
            following=following
        ).delete()

        if deleted == 0:
            return Response(
                {"error": "Not following"},
                status=status.HTTP_404_NOT_FOUND
            )
        
        #  notify remote node
        if following.is_remote:
            try:
                inbox_url = decoded_id + "/inbox/"

                payload = {
                    "type": "Unfollow",
                    "actor": {
                        "type": "author",
                        "id": f"{request.scheme}://{request.get_host()}/api/authors/{follower.id}",
                        "host": f"{request.scheme}://{request.get_host()}",
                    },
                    "object": {
                    "type": "author",
                    "id": decoded_id,
                    }
                }

                requests.post(inbox_url, json=payload, timeout=5)

            except Exception as e:
                print(" Remote delete failed:", e)

        return Response({"message": "Unfollowed"}, status=200)
    


# ---------------------------------------------------
# Accept follow request
# POST /api/authors/<pk>/accept/
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
# POST /api/authors/<pk>/reject/
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
# Get followers list
# GET /api/authors/<pk>/followers/
# ---------------------------------------------------
@api_view(["GET"])
@permission_classes([IsAuthenticated])

def api_get_followers(request, pk):
    author = get_object_or_404(Author, pk=pk)

    followers = Follower.objects.filter( following=author,status="accepted")
    authors = [f.follower for f in followers]
    serializer = AuthorSerializer(authors, many=True)
    return Response({
        "type": "followers",
        "followers": serializer.data
    })

# ---------------------------------------------------
# Get pending request list
# GET /api/authors/<pk>/follow_requests/
# ---------------------------------------------------
@api_view(["GET"])
@permission_classes([IsAuthenticated])

def api_get_follow_requests(request, pk):
    if str(request.user.id) != str(pk):
        return Response({"error": "Unauthorized"}, status=403)
    author = get_object_or_404(Author, pk=pk)
    pending = Follower.objects.filter( following=author,status="pending")
    authors = [f.follower for f in pending]
    serializer = AuthorSerializer(authors, many=True)
    return Response({
        "type": "follow_requests",
        "items": serializer.data
    })


@api_view(["GET", "PUT", "DELETE"])
@permission_classes([IsAuthenticated])
def api_accept_reject_followers(request, pk, foreign_id):
    print("---- REQUEST DEBUG ----")
    print("Method:", request.method)
    print("Path:", request.path)
    print("GET params:", request.GET)
    print("Body (raw):", request.body)
    print("Headers:", dict(request.headers))
    print("-----------------------")
    # decode foreign author
    #decoded_id = unquote(foreign_id)
    decoded_id = foreign_id
    print(f"decoding beofre = foreign_id: {decoded_id}")
    # Decode ONLY if encoded
    if "%" in decoded_id:
        decoded_id = unquote(decoded_id)
        print(f"After decodeing using unquote: {decoded_id}")

    decoded_id = decoded_id.rstrip("/")
    print(f"After decodeing using rstrip: {decoded_id}")

    if not decoded_id.startswith("http"):
        print("Invalid FQID format")
        return Response({"detail": "Invalid FQID"}, status=400)
    decoded_id = unquote(foreign_id).rstrip("/")
    
    target = get_object_or_404(Author, pk=pk)
    # find or create remote follower
    remote_follower = Author.objects.filter(remote_id=decoded_id).first()
    print(f"Remote follower found : {remote_follower} decoded id = {decoded_id}")
    '''
    if not remote_follower:
        remote_follower = Author.objects.create(
            remote_id=decoded_id,
            displayName="Remote User",
            is_remote=True,
        )
    '''
    if not remote_follower:
        uuid = decoded_id.rstrip("/").split("/")[-1]
        remote_follower = Author.objects.filter(id=uuid).first()

    if not remote_follower:
        return Response({"detail": "Remote author not found"}, status=404)

    # =========================
    #  GET → check follower
    # =========================
    if request.method == "GET":
        '''
        relation = Follower.objects.filter(
            follower=remote_follower,
            following=target,
            status="accepted"
        ).first()
        '''
        
        relation = Follower.objects.filter(
            follower__remote_id=decoded_id,
            following=target,
            status="accepted"
        ).first()
        if not relation:
            print("---- REQUEST DEBUG ----")
            print("Method:", request.method)
            print("Path:", request.path)
            print("GET params:", request.GET)
            print("Body (raw):", request.body)
            print("Headers:", dict(request.headers))
            print("-----------------------")
            return Response({"detail": "Not a follower"}, status=404)

        serializer = AuthorSerializer(remote_follower)
        #serializer = AuthorSerializer(relation.follower)
        print("Relation found: sending 200")
        return Response(serializer.data, status=200)

    # =========================
    #  PUT → accept follower
    # =========================
    if request.method == "PUT":
        relation = Follower.objects.filter(
            follower=remote_follower,
            following=target,
            status="pending"
        ).first()
        
        if not relation:
            return Response({"detail": "No pending request"}, status=404)
        
        relation.status = "accepted"
        relation.save(update_fields=["status"])

        return Response({"message": "Follower accepted"}, status=200)

    # =========================
    #  DELETE → remove / reject follower
    # =========================
    if request.method == "DELETE":
        deleted, _ = Follower.objects.filter(
            follower=remote_follower,
            following=target
        ).delete()

        if deleted == 0:
            return Response(
                {"detail": "Follower not found"},
                status=404
            )

        return Response(
            {"message": "Follower removed"},
            status=200
        )
    
# ---------------------------------------------------
# Unfollow
# DELETE /api/authors/<pk>/unfollow/
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
# GET /api/authors/<pk>/friends/
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