"""The scoped repository. Every read of a tenant-owned row goes through here."""
from .models import Location


def locations_for(user):
    """The ONLY sanctioned way to reach Location rows for a request's user."""
    return Location.objects.filter(tenant=user.tenant, is_archived=False)
