from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
import markdown as md

from .forms import PostForm
from .models import Post

try:
    import markdown
except ImportError:
    markdown = None

def _render_markdown(text: str) -> str:
    if not markdown:
        # fallback: show as plain text if markdown lib not installed
        return text
    return markdown.markdown(text, extensions=["extra", "sane_lists"])

import markdown as md

def stream(request):
    posts = Post.objects.filter(deleted=False).order_by("-created")

    for p in posts:
        if p.content_type == Post.ContentType.MARKDOWN:
            p.rendered = md.markdown(p.content or "", extensions=["extra"])
        else:
            p.rendered = None

    return render(request, "posts/stream.html", {"posts": posts})

def detail(request, post_id):
    post = get_object_or_404(Post, id=post_id, deleted=False)

    rendered = None
    if post.content_type == Post.ContentType.MARKDOWN:
        rendered = _render_markdown(post.content)

    return render(request, "posts/detail.html", {"post": post, "rendered": rendered})

@login_required
def create(request):
    if request.method == "POST":
        form = PostForm(request.POST, request.FILES)
        if form.is_valid():
            post = form.save(commit=False)
            post.author = request.user
            post.save()
            return redirect("posts:detail", post_id=post.id)
    else:
        form = PostForm()
    return render(request, "posts/create.html", {"form": form, "mode": "Create"})

@login_required
def edit(request, post_id):
    post = get_object_or_404(Post, id=post_id, deleted=False)
    if post.author_id != request.user.id:
        raise Http404()  # don’t reveal it exists

    if request.method == "POST":
        form = PostForm(request.POST, request.FILES, instance=post)
        if form.is_valid():
            form.save()
            return redirect("posts:detail", post_id=post.id)
    else:
        form = PostForm(instance=post)

    return render(request, "posts/create.html", {"form": form, "mode": "Edit", "post": post})

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