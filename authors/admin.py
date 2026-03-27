from django.contrib import admin

from .models import Author, Follower, Notification

admin.site.register(Author)
admin.site.register(Follower)
admin.site.register(Notification)

