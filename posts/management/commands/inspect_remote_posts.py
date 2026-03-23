from django.core.management.base import BaseCommand
from posts.models import Post


class Command(BaseCommand):
    help = "Inspect remote posts to see their content patterns"

    def handle(self, *args, **options):
        posts = Post.objects.filter(is_remote=True, deleted=False)[:10]
        
        self.stdout.write(f"Found {posts.count()} sample remote posts\n")
        
        for post in posts:
            self.stdout.write(f"\nPost: {post.remote_id}")
            self.stdout.write(f"  Title: {post.title[:50]}")
            self.stdout.write(f"  Content length: {len(post.content or '')}")
            if post.content:
                first_50 = (post.content or "")[:50].replace("\n", "\\n")
                self.stdout.write(f"  Content start: {first_50}")
            self.stdout.write(f"  Remote image: {(post.remote_image or '')[:80]}")
