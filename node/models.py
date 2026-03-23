from django.db import models


class Node(models.Model):
	host = models.URLField(unique=True)
	auth_username = models.CharField(max_length=100)
	auth_password = models.CharField(max_length=100)
	is_active = models.BooleanField(default=True)

	class Meta:
		ordering = ["host"]

	def __str__(self):
		return f"{self.host} ({'active' if self.is_active else 'inactive'})"
