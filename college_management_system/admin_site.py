"""The Django admin, reserved for our own (platform) staff.

Reaching any admin page needs ALL of:
  1. an active staff account that is a superuser (the usual admin rule),
  2. an e-mail listed in PLATFORM_ADMIN_EMAILS (environment), so a college user can never qualify
     even if someone flags their account as staff/superuser,
  3. a one-time code from the person's authenticator app, entered at this login (django-otp TOTP).
College users (HoD, staff, students) therefore never get in. Changes made here are saved through the
ORM, so the audit log records them with the admin as the actor.
"""
from django.conf import settings
from django_otp.admin import OTPAdminSite


class PlatformAdminSite(OTPAdminSite):
    def __init__(self, name='admin'):
        super().__init__(name)

    def has_permission(self, request):
        user = request.user
        return bool(
            super().has_permission(request)  # active + staff + OTP-verified in this session
            and user.is_superuser
            and (user.email or '').lower() in settings.PLATFORM_ADMIN_EMAILS
        )

