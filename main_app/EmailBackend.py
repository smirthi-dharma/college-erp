from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class EmailBackend(ModelBackend):
    """Log in with email + password. Deactivated users (is_active=False) are refused."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        user_model = get_user_model()
        email = username if username is not None else kwargs.get('email')
        if not email or password is None:
            return None
        try:
            user = user_model.objects.get(email=email)
        except user_model.DoesNotExist:
            user_model().set_password(password)  # spend the same time as a real check
            return None
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
