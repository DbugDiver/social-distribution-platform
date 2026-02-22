from django.contrib import admin
from django.urls import include, path
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    # Include the authentication URLs provided by Django
    path("accounts/", include("django.contrib.auth.urls")),
    path("", include("posts.urls")), 
    path("admin/", admin.site.urls),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)