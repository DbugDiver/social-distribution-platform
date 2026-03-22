"""
Django settings for socialdistribution project.
"""

import json
import os
from pathlib import Path
import dj_database_url

try:
    import cloudinary_storage  # noqa: F401
    USE_CLOUDINARY = True
except ImportError:
    USE_CLOUDINARY = False

BASE_DIR = Path(__file__).resolve().parent.parent

# Heroku/prod note: DEBUG must be a boolean. String values (e.g. "False") are truthy.
DEBUG = os.environ.get("DEBUG", "False").strip().lower() in ("1", "true", "yes", "on")

SECRET_KEY = os.environ.get(
    "SECRET_KEY",
    "django-insecure-vsc=qpkdlfthsm)na5b4hf9q!tiff#!cg00@=*mn@#h!+cd_))"
)

SITE_URL = os.environ.get("SITE_URL", "http://127.0.0.1:8000").rstrip("/")

REMOTE_NODES = [
    node.rstrip("/") for node in os.environ.get("REMOTE_NODES", "").split(",") if node.strip()
]

# JSON object keyed by node URL, e.g.
# {"https://node-a.example.com": {"username": "nodeuser", "password": "nodepass"}}
try:
    REMOTE_NODE_CREDENTIALS = json.loads(os.environ.get("REMOTE_NODE_CREDENTIALS", "{}"))
except (TypeError, json.JSONDecodeError):
    REMOTE_NODE_CREDENTIALS = {}

ALLOWED_HOSTS = [
    host.strip() for host in os.environ.get("ALLOWED_HOSTS", "127.0.0.1,localhost").split(",") if host.strip()
]

# Heroku sits behind a proxy, so these let Django generate correct https absolute URLs.
USE_X_FORWARDED_HOST = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Keep CSRF origins aligned with SITE_URL and any explicit env list.
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("CSRF_TRUSTED_ORIGINS", SITE_URL).split(",")
    if origin.strip()
]

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

LOGIN_URL = "/accounts/login/"
LOGIN_REDIRECT_URL = "posts:stream"
LOGOUT_REDIRECT_URL = "/accounts/login/"

INSTALLED_APPS = [
    "posts",
    "authors",
    "node",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
]

if USE_CLOUDINARY:
    INSTALLED_APPS = ["cloudinary_storage", "cloudinary"] + INSTALLED_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "socialdistribution.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "socialdistribution.wsgi.application"

DATABASES = {
    "default": dj_database_url.config(
        default=os.environ.get(
            "DATABASE_URL",
            f"sqlite:///{BASE_DIR / os.environ.get('SQLITE_NAME', 'db.sqlite3')}"
        ),
        conn_max_age=600,
        conn_health_checks=True,
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

STATICFILES_DIRS = [
    BASE_DIR / "static",
]

if USE_CLOUDINARY:
    CLOUDINARY_STORAGE = {
        "CLOUD_NAME": os.environ.get("CLOUDINARY_CLOUD_NAME"),
        "API_KEY": os.environ.get("CLOUDINARY_API_KEY"),
        "API_SECRET": os.environ.get("CLOUDINARY_API_SECRET"),
    }
    DEFAULT_FILE_STORAGE = "cloudinary_storage.storage.MediaCloudinaryStorage"
else:
    DEFAULT_FILE_STORAGE = "django.core.files.storage.FileSystemStorage"

STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"
WHITENOISE_USE_FINDERS = True

AUTH_USER_MODEL = "authors.Author"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"