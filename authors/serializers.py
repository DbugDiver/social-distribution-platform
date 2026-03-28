from rest_framework import serializers
from .models import Author, Follower


class AuthorSerializer(serializers.ModelSerializer):
    id=serializers.SerializerMethodField()
    type = serializers.SerializerMethodField() 
    host = serializers.SerializerMethodField() # overiiding host to append /api at end 
    class Meta:
        model=Author
        fields=["type", "id","host","username","displayName","github","bio","profileImage",]
    def get_id(self, obj):
        host = (obj.host or "").rstrip("/")
        return f"{host}/api/authors/{obj.id}"
    def get_type(self, obj):
        return "author"
    
    def get_host(self, obj):
        host = obj.host
        if host:
            host = host.rstrip("/")
            return f"{host}/api" 
        
        return host
class FollowerSerializer(serializers.ModelSerializer):
    class Meta:
        model=Follower