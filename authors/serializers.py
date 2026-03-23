from rest_framework import serializers
from .models import Author, Follower


class AuthorSerializer(serializers.ModelSerializer):
    id=serializers.SerializerMethodField()
    class Meta:
        model=Author
        fields=["id","host","username","displayName","github","bio","profileImage",]
    def get_id(self, obj):
        host = (obj.host or "").rstrip("/")
        return f"{host}/authors/{obj.id}"
class FollowerSerializer(serializers.ModelSerializer):
    class Meta:
        model=Follower