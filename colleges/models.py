from django.db import models
from django_tenants.models import DomainMixin, TenantMixin


class College(TenantMixin):
    """One row per college. Each college's data lives in its own PostgreSQL schema."""

    name = models.CharField(max_length=200)
    created_on = models.DateField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    # Create the schema (and run tenant migrations) when the row is first saved.
    auto_create_schema = True
    # Deleting a College row must never silently drop the college's data;
    # tests and ops tooling call delete(force_drop=True) explicitly.
    auto_drop_schema = False

    def __str__(self):
        return self.name


class Domain(DomainMixin):
    """Subdomain(s) that route to a college, e.g. demo.ourerp.in."""
