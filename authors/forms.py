from django import forms

from .models import Author


class AuthorUpdateForm(forms.ModelForm):
    class Meta:
        model = Author
        fields = [
            "first_name",
            "last_name",
            "email",
            "displayName",
            "bio",
            "github",
            "profileImage",
        ]

        widgets = {
            "first_name": forms.TextInput(attrs={"class": "form-control"}),
            "last_name": forms.TextInput(attrs={"class": "form-control"}),
            "email": forms.EmailInput(attrs={"class": "form-control"}),
            "displayName": forms.TextInput(attrs={"class": "form-control"}),
            "bio": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
            "github": forms.URLInput(attrs={"class": "form-control"}),
            "profileImage": forms.URLInput(attrs={"class": "form-control"}),
        }
