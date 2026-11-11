"""Platform staff set the per-message rates (what Meta charges) per college; CollegeSecret is deliberately not registered."""
from django.contrib import admin

from .models import UsageRate

admin.site.register(UsageRate)
