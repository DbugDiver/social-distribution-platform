from django.contrib import admin
from django.core.cache import cache

from node.registry import get_configured_nodes

from .models import Author, Follower, Notification


@admin.register(Author)
class AuthorAdmin(admin.ModelAdmin):
	list_display = (
		"username",
		"displayName",
		"is_remote",
		"remote_id",
		"host",
		"is_active",
		"is_approved",
	)
	list_filter = ("is_remote", "is_active", "is_approved", "is_staff")
	search_fields = ("username", "displayName", "remote_id", "host")
	ordering = ("-is_remote", "username")

	def get_queryset(self, request):
		self._sync_remote_authors_for_admin()
		return super().get_queryset(request)

	def get_readonly_fields(self, request, obj=None):
		if obj and obj.is_remote:
			# Remote proxies are hydrated by federation; keep them view-only in admin.
			return [field.name for field in obj._meta.fields] + ["groups", "user_permissions"]
		return super().get_readonly_fields(request, obj)

	def has_change_permission(self, request, obj=None):
		if obj and obj.is_remote:
			return False
		return super().has_change_permission(request, obj)

	def has_delete_permission(self, request, obj=None):
		if obj and obj.is_remote:
			return False
		return super().has_delete_permission(request, obj)

	def _sync_remote_authors_for_admin(self):
		lock_key = "author_admin_remote_sync_lock"
		last_key = "author_admin_remote_sync_last"

		if cache.get(lock_key):
			return
		if cache.get(last_key):
			return

		cache.set(lock_key, True, 30)
		try:
			from .views import (
				_auth_for_node,
				_fetch_remote_authors_from_node,
				_normalize_remote_author_card,
				_upsert_remote_author,
			)

			for node in get_configured_nodes(exclude_local=True):
				node = (node or "").rstrip("/")
				if not node:
					continue

				auth = _auth_for_node(node)
				items = _fetch_remote_authors_from_node(node, "", auth)

				for raw_author in items[:300]:
					normalized = _normalize_remote_author_card(raw_author, node)
					if normalized.get("id"):
						_upsert_remote_author(normalized)
		except Exception:
			# Never break admin rendering because remote federation is down.
			pass
		finally:
			cache.delete(lock_key)
			cache.set(last_key, True, 300)


@admin.register(Follower)
class FollowerAdmin(admin.ModelAdmin):
	list_display = ("follower", "following", "status", "time_created")
	list_filter = ("status",)
	search_fields = ("follower__username", "following__username")


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
	list_display = ("sender", "recipient", "notification_type", "is_read", "created_at")
	list_filter = ("notification_type", "is_read")

