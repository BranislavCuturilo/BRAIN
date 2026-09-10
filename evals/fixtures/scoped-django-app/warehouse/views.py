from django.views.generic import ListView

from .models import Location
from .selectors import locations_for


class LocationListView(ListView):
    model = Location
    template_name = "warehouse/location_list.html"

    def get_queryset(self):
        return locations_for(self.request.user)
