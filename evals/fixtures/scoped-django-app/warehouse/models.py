from django.db import models


class Tenant(models.Model):
    name = models.CharField(max_length=120)


class Location(models.Model):
    tenant = models.ForeignKey(Tenant, on_delete=models.PROTECT)
    name = models.CharField(max_length=120)
    is_archived = models.BooleanField(default=False)
