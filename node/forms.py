from django import forms

from .models import Node


class NodeForm(forms.ModelForm):
    class Meta:
        model = Node
        fields = ["host", "auth_username", "auth_password", "is_active"]
        widgets = {
            "host": forms.URLInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "https://remote-node.com/",
                }
            ),
            "auth_username": forms.TextInput(attrs={"class": "form-control"}),
            "auth_password": forms.PasswordInput(attrs={"class": "form-control"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }
