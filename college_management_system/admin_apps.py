from django.contrib.admin.apps import AdminConfig


class PlatformAdminConfig(AdminConfig):
    """Swap Django's default admin site for the 2FA, platform-staff-only one (see admin_site.py)."""
    default_site = 'college_management_system.admin_site.PlatformAdminSite'
