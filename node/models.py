from django.db import models


class Node(models.Model):
    """Stores Credentials for Remote Nodes - Outbound"""

    host = models.URLField(unique=True)
    auth_username = models.CharField(max_length=100)
    auth_password = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)

    def __str__(self) -> str:
        return str(self.host)
