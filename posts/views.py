from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
import markdown as md

from authors.models import Author, Follower
from .forms import PostForm
from .models import Post
from django.db.models import Q


try:
    import markdown
except ImportError:
    markdown = None

"""
Helper function to render markdown content to HTML. If the markdown library is not installed, it will return the original text.
"""
def _render_markdown(text: str) -> str:
    if not markdown:
        return text  # fallback: show as plain text if markdown lib not installed
    return markdown.markdown(text, extensions=["extra", "sane_lists"])

"""
This function handle the logic for displaying the stream of posts.
It will GET the posts that are not deleted, ordered by created time (newest first).
Then it will loop through the posts. If the content type is markdown then convert it to HTML.
Finally, it will send the posts to the stream.html template for rendering.
"""
@login_required   # Posts only stream when account exists and logged in
def stream(request):
    user = request.user     #get current user
    posts = Post.objects.filter(
            Q(author=user) |   #all posts posted by user even if unlisted or friends only
            Q(visibility=Post.Visibility.PUBLIC)  #all public posts
            ).filter(deleted=False
            ).order_by("-created") # GET the posts that are not deleted, ordered
    #-> |Q(author__in=following, visibility=Post.Visibility.FRIENDS) # later for friends only
    for p in posts:
        if p.content_type == Post.ContentType.MARKDOWN:
            p.rendered = md.markdown(p.content or "", extensions=["extra"])
        else:
            p.rendered = None
    return render(request, "posts/stream.html", {"posts": posts}) # Send the posts to the stream.html template

"""
This function handle the logic for displaying the details of a single post.
It will GET a single post by its ID, but only if it is not deleted. If it does not exist, return a 404 error page.
If markdown then it will convert it to HTML.
Finally, it will send the post content to the detail.html template for rendering.
"""
def detail(request, post_id):
    post = get_object_or_404(Post, id=post_id, deleted=False)
    followers = [] #place holder for now 
    #-> post.author.followers.all()

    # public everyone allowed
    if post.visibility == Post.Visibility.PUBLIC: pass # allowing direct link to all public
    # unlisted everyone allowed
    elif post.visibility == Post.Visibility.UNLISTED: pass  # Anyone with link can see
    # friends only allowed if user is author
    elif post.visibility == Post.Visibility.FRIENDS:
        if not request.user.is_authenticated:
            return HttpResponseForbidden("Login required.")
        if request.user != post.author and request.user not in followers:
            return HttpResponseForbidden("Not allowed.")
    # Safety fallback 
    else:
        return HttpResponseForbidden("Invalid visibility.")
    
    rendered = None
    if post.content_type == Post.ContentType.MARKDOWN:
        rendered = _render_markdown(post.content)
   
    return render(request, "posts/detail.html", {"post": post, "rendered": rendered})

"""
  Rosy: I have a form that I want to use for posting. I want this form to craete, and edit posts. 
  How do I implement these in views.py depening on is user is autheticated/logged In or not.
  ChatGPT: create and edit below are based on the answer provided
  Citation: ChatGPT, OpenAI, 2026-02-22, https://chatgpt.com/share/699ab22d-6bac-800a-ab03-5609dd01c80e
"""

"""
This function handle the logic for creating a new post.
It requires the user to be logged in.
If the request method is POST, it will validate the form data and create a new post with the current user as the author.
And then redirect the user to the stream page. 
If the request method is GET, it will display an empty form for creating a new post.
"""
@login_required
def create(request):
    if request.method == "POST":
        form = PostForm(request.POST, request.FILES)
        if form.is_valid():
            post = form.save(commit=False)
            post.author = request.user
            post.save()
            return redirect("posts:stream")
    else:
        form = PostForm()
    return render(request, "posts/create.html", {"form": form, "mode": "Create"})

"""
This function handle the logic for editing an existing post.
It requires the user to be logged in.  
It will GET the post by its ID, but only if it is not deleted. If it does not exist, return a 404 error page.
If the current user is not the author of the post, return a 404 error page.
If the request method is POST, it will validate the form data and update the post. And then redirect the user to the stream page. 
If the request method is GET, it will display a form pre-filled with the post data for editing. 
"""
@login_required
def edit(request, post_id):
    post = get_object_or_404(Post, id=post_id, deleted=False)
    if post.author_id != request.user.id:
        raise Http404()
    if request.method == "POST":
        form = PostForm(request.POST, request.FILES, instance=post)
        if form.is_valid():
            form.save()
            return redirect("posts:stream")
    else:
        form = PostForm(instance=post)

    return render(request, "posts/create.html", {"form": form, "mode": "Edit", "post": post})

"""
This function handle the logic for deleting an existing post.
It requires the user to be logged in.
It will GET the post by its ID, but only if it is not deleted. If it does not exist, return a 404 error page.
If the current user is not the author of the post, return a 404 error page.
If the request method is POST, it will mark the post as deleted and save it. And then redirect the user to the stream page. 
If the request method is GET, it will display a confirmation page for deleting the post. 
"""
@login_required
def delete(request, post_id):
    post = get_object_or_404(Post, id=post_id, deleted=False)
    if post.author_id != request.user.id:
        raise Http404()
    if request.method == "POST":
        post.deleted = True
        post.save(update_fields=["deleted", "updated"])
        return redirect("posts:stream")
    return render(request, "posts/delete_confirm.html", {"post": post})


@login_required
def friends_feed(request):
    """Show posts from friends only (mutual followers)"""
    author=request.user

    # Get friends: authors who you follow AND who follow you back
    following=Follower.objects.filter(follower=author, status="accepted").values_list("following", flat=True)
    followers=Follower.objects.filter(following=author, status="accepted").values_list("follower", flat=True)
    friends=Author.objects.filter(id__in=following).filter(id__in=followers)

    # Fetch posts by friends
    posts=Post.objects.filter(author__in=friends).order_by("-created")
    context={"posts": posts,"feed_title": "Friends Feed",}
    return render(request, "posts/stream.html", context)