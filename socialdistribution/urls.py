from django.contrib import admin
from django.urls import include, path
from django.conf import settings
from django.conf.urls.static import static

'''
urlpatterns = [
    # Include the authentication URLs provided by Django
    path("accounts/", include("django.contrib.auth.urls")),
    path("", include("posts.urls")), 
    path("admin/", admin.site.urls),
    path("", include("authors.urls"))
]
'''
# Changes made for merging backened and social distribution
urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("django.contrib.auth.urls")),

    path("", include("posts.urls")),           # homepage = posts stream
    path("authors/", include("authors.urls")),
]


if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)