from django.core.management.base import BaseCommand
from posts.models import Post
import re


def looks_like_base64(value):
    """More robust base64 detection."""
    raw = (value or "").strip()
    
    # Skip if too short, already a data URL, or a real URL
    if len(raw) < 128:
        return False
    if raw.startswith(("data:", "http://", "https://", "/")):
        return False
    
    # Check for common image base64 signatures
    if raw.startswith(("/9j/", "iVBOR", "R0lGOD", "UklGR")):
        return True
    
    # Additional checks: mostly alphanum + /+= with long continuous blocks
    # This catches base64 that doesn't match known signatures
    if len(raw) > 200:
        # Remove all padding and splits
        clean = raw.replace("=", "").replace("+", "").replace("/", "").replace("\n", "")
        # If it's mostly alphanumeric for very long strings, likely base64 encoded data
        if re.match(r"^[A-Za-z0-9]+$", clean) and len(clean) > 150:
            # Additional heuristic: base64 commonly repeats pattern of Az (uppercase, lowercase),
            # or contains many 'A' or 'i' (common in binary-to-base64 conversions)
            uppercase_count = sum(1 for c in raw if c.isupper())
            lowercase_count = sum(1 for c in raw if c.islower())
            if uppercase_count > 20 and lowercase_count > 20:  # Both cases present = likely base64
                return True
    
    return False


class Command(BaseCommand):
    help = "Bulk sanitize all remote posts with base64 content or image fields"

    def handle(self, *args, **options):
        self.stdout.write("Starting bulk sanitization of remote posts...")
        
        # Get all remote posts
        posts = Post.objects.filter(is_remote=True, deleted=False)
        total = posts.count()
        updated_count = 0
        
        self.stdout.write(f"Found {total} remote posts to scan")
        
        for i, post in enumerate(posts, 1):
            changed = False
            raw_content = (post.content or "").strip()
            raw_image = (post.remote_image or "").strip()
            
            # Check content field for base64 - always clear it, even if remote_image exists
            if raw_content and looks_like_base64(raw_content):
                # If no remote image URL, convert base64 to data URL
                if not raw_image or raw_image.startswith("data:"):
                    post.remote_image = f"data:image/jpeg;base64,{raw_content}"
                # Always clear the content field when we detect base64
                post.content = ""
                changed = True
                self.stdout.write(
                    f"  [{i}/{total}] Post {post.remote_id}: Moved base64 from content to remote_image"
                )
            
            # Check remote_image field for base64 (not already wrapped in data:)
            if raw_image and not raw_image.startswith("data:") and looks_like_base64(raw_image):
                post.remote_image = f"data:image/jpeg;base64,{raw_image}"
                changed = True
                self.stdout.write(
                    f"  [{i}/{total}] Post {post.remote_id}: Wrapped base64 image in data URL"
                )
            
            if changed:
                post.save(update_fields=["content", "remote_image"])
                updated_count += 1
        
        self.stdout.write(
            self.style.SUCCESS(f"\n✓ Sanitization complete: Updated {updated_count}/{total} posts")
        )
