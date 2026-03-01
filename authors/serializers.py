from rest_framework import serializers
from .models import Author, Follower


class AuthorSerializer(serializers.ModelSerializer):
    id=serializers.SerializerMethodField()
    class Meta:
        model=Author
        fields=["id","host","displayName","github","profileImage",]
    def get_id(self, obj):
        return obj.get_fqid()
class FollowerSerializer(serializers.ModelSerializer):
    class Meta:
        model=Follower