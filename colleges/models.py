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


class MessageRoute(models.Model):
    """Which college a WhatsApp message id belongs to.

    Meta calls ONE webhook URL for every college, and its delivery reports carry only the message id, so this public
    table maps an id back to the college that sent it. It holds no message text and no phone number.
    """
    wamid = models.CharField(max_length=200, unique=True)
    college = models.ForeignKey(College, on_delete=models.CASCADE, related_name='message_routes')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return 'Message route #%s' % self.pk
