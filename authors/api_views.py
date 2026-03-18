from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from django.shortcuts import get_object_or_404

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