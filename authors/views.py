from socket import timeout

import requests
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from .forms import AuthorUpdateForm
from .models import Author, Follower


def home_feed(request):
    """Main Page"""
    return render(request, "posts/stream.html")


def author_profile(request, pk):
    """Authors Page with github activity"""
    author = get_object_or_404(Author, pk=pk)

    # Get Public entries [placeholder for now]
    entries = []

    # Fetch Github activity
    github_events = []
    if author.github:
        # Extract GitHub username from URL
        gh_username = author.github.strip("/").split("/")[-1]
        try:
            gh_res = requests.get(
                f"https://api.github.com/users/{gh_username}/events/public", timeout=2
            )
            if gh_res.status_code == 200:
                # Getting the 5 most recent events
                github_events = gh_res.json()[:5]
        except:
            pass

    context = {
        "profile_user": author,
        "entries": entries,
        "github_events": github_events,
    }

    return render(request, "authors/profile.html", context)


@login_required
def edit_profile(request):
    """Edit Profile Logic"""
    # Grab the currently logged-in user
    author = request.user

    if request.method == "POST":
        form = AuthorUpdateForm(request.POST, instance=author)
        if form.is_valid():
            form.save()
            return redirect("author-profile", pk=author.pk)
    else:
        # If it's a GET request, load the form pre-filled with their current info
        form = AuthorUpdateForm(instance=author)

    return render(request, "authors/edit_profile.html", {"form": form})

@login_required
def send_a_follow_request(request,pk):
    """Send a follow request to another author"""
    author=request.user                         #get the currently logged in user
    
    following=get_object_or_404(Author,pk=pk)   #get the author that the user wants to follow, if the author does not exist, return a 404 error
    
    if author==following:
        return redirect("author-profile",pk=pk)
    #check if the follow request already exists
    Follower.objects.get_or_create(follower=author,following=following,defaults={"status": "pending"}) #create a new follow request for the following author, if it already exists, do nothing
    return redirect("author-profile",pk=pk)

@login_required
def accept_follow_request(request,pk):
    """ accept a follow request from another author"""
    author=request.user #get the currently logged in user
    
    follower=get_object_or_404(Author,pk=pk) #get the author that sent the follow request, if the author does not exist, return a 404 error
    follow_request=get_object_or_404(Follower,follower=follower,following=author) #get the follow request, if it does not exist, return a 404 error
    follow_request.status="accepted" #update the status of the follow request to accepted
    follow_request.save() #save the changes to the database
    return redirect("author-profile",pk=author.pk)
@login_required
def reject_follow_request(request,pk):
    """ reject a follow request from another author"""
    author=request.user #get the currently logged in user
    
    follower=get_object_or_404(Author,pk=pk) #get the author that sent the follow request, if the author does not exist, return a 404 error
    follow_request=get_object_or_404(Follower,follower=follower,following=author) #get the follow request, if it does not exist, return a 404 error
    follow_request.status="rejected" #update the status of the follow request to rejected
    follow_request.save() #save the changes to the database
    return redirect("author-profile",pk=author.pk)

@login_required
# As an author, I want to know if I have "follow requests," so I can approve them
def follow_requests(request):
    """View all pending follow requests for the logged-in author"""
    author = request.user
    pending_follow_requests = Follower.objects.filter(following=author, status="pending")
    context = {"pending_follow_requests": pending_follow_requests,}
    return render(request, "authors/follow_requests.html", context)

@login_required
def unfollow(request,pk):
    """UNfollow an author that you are currently following"""
    author=request.user #get the currently logged in user
    
    following=get_object_or_404(Author,pk=pk) #get the author that the user wants to unfollow, if the author does not exist, return a 404 error
    Follower.objects.filter(follower=author,following=following).delete()
    return redirect("author-profile",pk=pk)

# As an author, if I am following another author, and they are following me (only after both follow requests are approved), I want us to be considered friends, so that they can see my friends-only entries.
@login_required
def mutual_following_became_friends(request):
    """View all friends of the logged-in author"""
    author=request.user         #get the currently logged in user
    following=Follower.objects.filter(follower=author, status="accepted").values_list("following", flat=True) #get all the authors that the author that is logged-in is following and those that have accepted thier follow request
    followers=Follower.objects.filter(following=author, status="accepted").values_list("follower", flat=True) #get all the authors that are following the author that is currently logged in and that have accepted the follow request ie they are both following each other
    friends=Author.objects.filter(id__in=following).filter(id__in=followers) #get all the authors that are both following the logged-in author and that are being followed by the logged-in author, these are the friends of the logged-in author
    context={"friends": friends}                #create a context dictionary to pass the friends to the template
    return render(request, "authors/friends.html", context)