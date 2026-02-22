from django import forms
from .models import Post

"""
  Rosy: I want a form class that take a model and fields and allows me to add custom validations
  ChatGPT: PostForm class is based on the answer provided
  Citation: ChatGPT, OpenAI, 2026-02-22, https://chatgpt.com/share/699ab4ee-3e20-800a-8488-8e698efb894f
"""

class PostForm(forms.ModelForm):
    class Meta:
        model = Post
        fields = ["title", "content_type", "content", "image", "visibility"]

    """
    The clean method is overridden to add custom validation logic based on the content type.
    For plain text posts, it checks that there is no image and that there is some content.
    For markdown posts, it checks that there is either content or an image (or both).
    If the validation fails, it raises a ValidationError with an appropriate message.
    """
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