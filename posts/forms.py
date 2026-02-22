from django import forms
from .models import Post

class PostForm(forms.ModelForm):
    class Meta:
        model = Post
        fields = ["title", "content_type", "content", "image", "visibility"]

    def clean(self):
        cleaned = super().clean()
        ct = cleaned.get("content_type")
        content = (cleaned.get("content") or "").strip()
        image = cleaned.get("image")

        if ct == Post.ContentType.PLAIN:
            # Plain: text only
            if image:
                raise forms.ValidationError("Plain text posts cannot include an image.")
            if not content:
                raise forms.ValidationError("Plain text posts need some content.")

        elif ct == Post.ContentType.MARKDOWN:
            # Markdown: image optional, text optional (but require at least one)
            if not content and not image:
                raise forms.ValidationError("Markdown posts need content or an image (or both).")

        else:
            raise forms.ValidationError("Invalid content type.")

        return cleaned