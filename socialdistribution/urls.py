from django.contrib import admin
from django.urls import include, path
from django.conf import settings
from django.conf.urls.static import static
from django.views.static import serve
from django.urls import re_path

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
'''
urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("django.contrib.auth.urls")),

    path("", include("posts.urls")),           # homepage = posts stream
    path("authors/", include("authors.urls")),
    path("node/", include("node.urls")),
]
'''
urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("django.contrib.auth.urls")),

    path("", include("posts.urls")),

    # UI routes
    path("authors/", include("authors.urls")),

    # ✅ API routes (NEW)
    path("api/authors/", include("authors.api_urls")),

    path("node/", include("node.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# Serve user-uploaded media in production deployments without external media storage.
urlpatterns += [
    re_path(r"^media/(?P<path>.*)$", serve, {"document_root": settings.MEDIA_ROOT}),
]