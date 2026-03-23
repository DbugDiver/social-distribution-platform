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
            # FIXED: Changed from URLInput to FileInput so they can actually upload!
            "profileImage": forms.FileInput(attrs={"class": "form-control"}),
        }

    # The bonus validation we talked about!
    def clean_github(self):
        github_link = self.cleaned_data.get("github")

        # Only validate if they actually typed something (it might be an optional field)
        if github_link:
            # Check if it starts with the correct GitHub domain
            if not github_link.startswith(
                ("https://github.com/", "http://github.com/")
            ):
                raise forms.ValidationError(
                    "Please enter a valid GitHub profile link starting with https://github.com/"
                )

        return github_link

        # Your new backend email validation
        def clean_email(self):
            email = self.cleaned_data.get("email")

            if email:
                try:
                    # This runs Django's heavy-duty internal email checker
                    validate_email(email)
                except forms.ValidationError:
                    # If it fails, we throw a custom error back to the template
                    raise forms.ValidationError(
                        "Please enter a valid email address (e.g., name@domain.com)"
                    )

            return email
